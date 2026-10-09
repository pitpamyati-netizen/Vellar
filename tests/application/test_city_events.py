"""Вклад и ремесло идут вместе с расходом, а награда выдаётся один раз."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mmorpg.application.operations import MissingResourceError, atomic_action
from mmorpg.application.services.city_event import CityEvents
from mmorpg.domain.entities import Character
from mmorpg.domain.rules import city_event as rules
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)


@pytest.fixture
async def town(content):
    characters = InMemoryCharacterRepository()
    inventory = InMemoryInventoryRepository()
    cache = InMemoryStateCache()
    people = []
    for index in range(4):
        people.append(
            await characters.create(
                Character(
                    id=0,
                    user_id=7100 + index,
                    name=f"Мастер{index}",
                    race_id="human",
                    class_id="warrior",
                    city_id="farhold",
                    gold=100,
                )
            )
        )
    service = CityEvents(content, cache, characters, inventory)
    return SimpleNamespace(
        service=service,
        people=people,
        event=content.city_events[0],
        cache=cache,
        characters=characters,
        inventory=inventory,
    )


async def finish(service, people):
    for stage in range(2):
        for index, person in enumerate(people):
            await service.record(person.id, "farhold", 1, "battle", f"{stage}:{index}")


async def test_concurrent_contributors_share_state_and_claims_are_unique(town):
    e = town
    await asyncio.gather(
        *(
            e.service.record(person.id, "farhold", 1, "battle", f"battle:{index}", expected_stage=0)
            for index, person in enumerate(e.people)
        )
    )
    state = await e.service.load(e.event)
    assert state.stage == 1 and len(state.contributions) == 4
    for person in e.people:
        await e.service.record(person.id, "farhold", 1, "scout", f"new:{person.id}")
    assert (await e.service.load(e.event)).stage == 2
    await asyncio.gather(*(e.service.claim(e.people[0].id, "farhold") for _ in range(3)))
    assert (await e.characters.get(e.people[0].id)).gold == 140
    assert rules.reward_due(e.event, await e.service.load(e.event), e.people[0].id) == (0, ())
    assert await e.service.travel_discount("farhold") == 10
    assert await e.service.travel_discount("unknown") == 0
    assert "для всех" in await e.service.summary()


async def test_invalid_place_actor_kind_and_duplicate_do_not_advance(town):
    e = town
    assert not await e.service.record(e.people[0].id, "unknown", 1, "battle", "a")
    assert not await e.service.record(99999, "farhold", 1, "battle", "a")
    assert not await e.service.record(e.people[0].id, "farhold", 2, "battle", "a")
    assert not await e.service.record(e.people[0].id, "farhold", 1, "craft", "a")
    assert not await e.service.record(e.people[0].id, "farhold", 1, "battle", "a", expected_stage=1)
    assert await e.service.record(e.people[0].id, "farhold", 1, "battle", "a")
    assert not await e.service.record(e.people[0].id, "farhold", 1, "battle", "a")
    assert (await e.service.load(e.event)).stage == 0
    assert "нет" in await e.service.claim(e.people[0].id, "farhold")


@atomic_action
async def broken_craft(service, author_id, command, *, operation_id=None) -> str:
    result = await service.craft(author_id, "farhold", 0, command)
    if command == "broken":
        raise RuntimeError("after craft and world")
    return result


async def test_real_craft_spends_materials_and_rollback_restores_work_and_city(town):
    e = town
    actor = e.people[0]
    assert "материалов" in await e.service.craft(actor.id, "farhold", 0, "empty")
    await e.inventory.add(actor.id, "iron_scrap", 8)
    with pytest.raises(RuntimeError, match="after craft"):
        await broken_craft(e.service, actor.id, "broken")
    assert await e.inventory.count(actor.id, "iron_scrap") == 8
    assert await e.characters.get(actor.id) == actor
    assert not (await e.service.load(e.event)).contributions
    notice = await e.service.craft(actor.id, "farhold", 0, "one")
    assert "передано городу" in notice
    assert 6 <= await e.inventory.count(actor.id, "iron_scrap") <= 7
    assert await e.inventory.count(actor.id, "crude_ingot") == 0
    assert (await e.characters.get(actor.id)).crafts != actor.crafts
    before = await e.inventory.count(actor.id, "iron_scrap")
    assert "уже" in await e.service.craft(actor.id, "farhold", 0, "one")
    assert await e.inventory.count(actor.id, "iron_scrap") == before
    assert "изменилось" in await e.service.craft(actor.id, "farhold", 1, "stale")


async def test_material_disappearance_cancels_everything(town, monkeypatch):
    e = town
    actor = e.people[0]
    await e.inventory.add(actor.id, "iron_scrap", 4)
    monkeypatch.setattr(e.inventory, "remove", AsyncMock(return_value=False))
    with pytest.raises(MissingResourceError):
        await e.service.craft(actor.id, "farhold", 0, "gone")
    assert await e.characters.get(actor.id) == actor
    assert not (await e.service.load(e.event)).contributions
