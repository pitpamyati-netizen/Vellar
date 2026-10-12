"""Отдых и бой входят в одну операцию; сбой не оставляет лечения."""

import asyncio
import json
from dataclasses import replace

import pytest

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.battle import BattleStore, begin, deserialise, serialise
from mmorpg.application.services.health_recovery import HealthRecovery
from mmorpg.domain.entities.character import Character
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.persistence import InMemoryCharacterRepository


@pytest.fixture
async def resting(content):
    characters = InMemoryCharacterRepository()
    cache = InMemoryStateCache()
    hero = await characters.create(
        Character(0, 42, "Аргус", "human", "warrior", health=1, health_recovered_at=1000)
    )
    return HealthRecovery(content, characters, cache), hero


async def test_parallel_actions_do_not_duplicate_healing(resting):
    service, hero = resting
    first, second = await asyncio.gather(
        service.apply(hero.id, now=1060), service.apply(hero.id, now=1060)
    )
    assert first.health == second.health > hero.health
    assert (await service.characters.get(hero.id)).revision == hero.revision + 1


async def test_active_or_paused_battle_never_heals(resting, content):
    service, hero = resting
    foe = replace(hero, id=hero.id + 1, user_id=43)
    session, _ = begin(
        content,
        battle_id="rest-battle",
        attackers=[(hero, True)],
        defenders=[(foe, False)],
        seed=b"rest-control",
    )
    store = BattleStore(service._cache)
    await store.pin(session, content)
    await store.save(session)
    assert await service.apply(hero.id, now=10000) == hero
    assert (await store.load(session.id)).state == session.state


async def test_failure_rolls_back_health_and_time(resting):
    service, hero = resting

    @atomic_action
    async def interrupted(characters, state_cache):
        await service.apply(hero.id, now=1060)
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled failure"):
        await interrupted(service.characters, service._cache)
    assert await service.characters.get(hero.id) == hero


async def test_missing_character_is_safe(resting):
    service, _ = resting
    assert await service.apply(99999, now=1060) is None


def test_old_battle_resource_labels_keep_current_amounts(content):
    hero = Character(1, 42, "Аргус", "human", "paladin")
    session, _ = begin(
        content,
        battle_id="old-resource",
        attackers=[(hero, True)],
        defenders=[(replace(hero, id=2), False)],
        seed=b"old-resource",
    )
    raw = json.loads(serialise(session))
    raw["combatants"][0]["resource_name"] = "Благодать"
    restored = deserialise(json.dumps(raw))
    one = restored.state.combatants[0]
    previous = session.state.combatants[0]
    assert one.resource_name == "Выносливость"
    assert (one.resource, one.max_resource) == (previous.resource, previous.max_resource)
