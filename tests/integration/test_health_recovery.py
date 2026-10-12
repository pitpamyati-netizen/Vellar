"""Здоровье после перерыва: SQL, одновременные команды, откат и настоящий путь экрана."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import StaleCharacterError, atomic_action
from mmorpg.application.services.health_recovery import HealthRecovery
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.health_recovery import recover
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.middlewares.health_recovery import HealthRecoveryMiddleware
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play
from tests.integration.test_combat_entry import combat_app as combat_app
from tests.integration.test_combat_entry import feed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def resting_sql(pool, content):
    account = 9_000_000_000 + uuid4().int % 1_000_000_000
    await PostgresUserRepository(pool).upsert(User(telegram_id=account))
    characters = PostgresCharacterRepository(pool)
    hero = await characters.create(
        Character(
            0, account, f"Отдых{account}", "human", "warrior", health=1, health_recovered_at=1000
        )
    )
    cache = PostgresGameplayState(pool, InMemoryStateCache())
    try:
        yield HealthRecovery(content, characters, cache), hero
    finally:
        await pool.execute("DELETE FROM users WHERE telegram_id=$1", account)


async def test_sql_fraction_and_time_survive_repository_restart(resting_sql, pool, content):
    service, hero = resting_sql
    fraction = await service.apply(hero.id, now=1001)
    restored = await PostgresCharacterRepository(pool).get(hero.id)
    assert restored == fraction
    assert restored.health_recovery_remainder > 0
    minute = await service.apply(hero.id, now=1060)
    assert minute == recover(content, hero, now=1060)


async def test_sql_concurrency_counts_elapsed_time_once(resting_sql, content):
    service, hero = resting_sql
    one, two = await asyncio.gather(
        service.apply(hero.id, now=1060), service.apply(hero.id, now=1060)
    )
    saved = await service.characters.get(hero.id)
    assert saved.health == one.health == two.health == recover(content, hero, now=1060).health
    assert saved.revision == hero.revision + 1
    with pytest.raises(StaleCharacterError):
        await service.characters.save(hero)


async def test_sql_failure_rolls_back_healing_and_fraction(resting_sql):
    service, hero = resting_sql

    @atomic_action
    async def interrupted(characters, state_cache):
        await service.apply(hero.id, now=1060)
        raise RuntimeError("controlled recovery failure")

    with pytest.raises(RuntimeError, match="controlled recovery failure"):
        await interrupted(service.characters, service._cache)
    assert await service.characters.get(hero.id) == hero


async def test_sql_old_character_is_initialised_without_free_healing(resting_sql):
    service, hero = resting_sql
    old = await service.characters.save(replace(hero, health_recovered_at=None))
    rested = await service.apply(hero.id, now=5000)
    assert rested.health == old.health
    assert rested.health_recovered_at == 5000


async def test_command_heals_once_replay_preserves_answer_and_next_command_updates(
    combat_app, monkeypatch
):
    app, deps, hero, state, recording = combat_app
    clock = [1060]
    for middleware in app.dispatcher.message.outer_middleware:
        if isinstance(middleware, HealthRecoveryMiddleware):
            monkeypatch.setattr(middleware, "clock", lambda: clock[0])
    hero = await deps.characters.save(replace(hero, health=1, health_recovered_at=1000))
    await state.set_state(Play.main_menu)
    await state.set_data({"play": PlayState(screen=ScreenId.MAIN_MENU).serialise()})
    event, reply = await feed(app, recording, hero, "Главное меню", 401)
    first = await deps.characters.get(hero.id)
    assert first.health > hero.health
    assert f"{first.health} из" in reply.text
    clock[0] = 1120
    await app.dispatcher.feed_update(app.bot, event)
    assert await deps.characters.get(hero.id) == first
    _, reply = await feed(app, recording, hero, "Главное меню", 402)
    second = await deps.characters.get(hero.id)
    assert second.health > first.health
    assert all(method.parse_mode is None for method in recording.sent)


async def test_paused_battle_does_not_heal_and_surrender_starts_new_rest(combat_app, monkeypatch):
    from mmorpg.presentation.telegram.handlers import combat

    app, deps, hero, state, recording = combat_app
    clock = [1060]
    monkeypatch.setattr(combat.time, "time", lambda: clock[0])
    for middleware in app.dispatcher.message.outer_middleware:
        if isinstance(middleware, HealthRecoveryMiddleware):
            monkeypatch.setattr(middleware, "clock", lambda: clock[0])
    hero = await deps.characters.save(replace(hero, health=1, health_recovered_at=1000))
    flow = PlayState(
        screen=ScreenId.DUNGEON_PICK, city_id="farhold", dungeon_pick="farhold_first_adit"
    )
    await state.set_state(Play.dungeon_pick)
    await state.set_data({"play": flow.serialise()})
    await feed(app, recording, hero, "Разведка", 410)
    before = await deps.characters.get(hero.id)
    clock[0] = 10000
    await feed(app, recording, hero, "/пауза", 411)
    await feed(app, recording, hero, "Главное меню", 412)
    assert await deps.characters.get(hero.id) == before
    await feed(app, recording, hero, "/продолжить", 413)
    await feed(app, recording, hero, "Сдаться", 414)
    left = await deps.characters.get(hero.id)
    assert left.health_recovered_at == clock[0]
    await feed(app, recording, hero, "Главное меню", 415)
    assert (await deps.characters.get(hero.id)).health == left.health
    clock[0] += 60
    await feed(app, recording, hero, "Главное меню", 416)
    assert (await deps.characters.get(hero.id)).health > left.health
