"""Настоящий SQL: история, этапы и чтение переживают сбой и смену адаптера."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from mmorpg.application.journey import Activity, Visit
from mmorpg.application.operations import Operation
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.journey import PostgresJourneyRepository
from mmorpg.presentation.telegram import reading
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
BOT = 988_704
USER = -988_704


async def clean(pool):
    await pool.execute("DELETE FROM player_activity WHERE bot_id=$1", BOT)
    await pool.execute("DELETE FROM player_journey WHERE bot_id=$1", BOT)


async def test_sql_failure_replay_concurrency_and_private_aggregates(pool):
    await clean(pool)
    repo = PostgresJourneyRepository(pool)
    operation_id = "m04-test:" + str(uuid4())
    attempts = 0

    async def action():
        nonlocal attempts
        attempts += 1
        await repo.observe(Visit(BOT, USER, "name", 10), None, operation_id)
        await repo.observe(
            Visit(BOT, USER, "main_menu", 2000, 1, 63),
            Activity(2000, 8, tutorial_steps=1, items=(("small_healing_potion", 3),)),
            operation_id,
        )
        if attempts == 1:
            raise ValueError("controlled failure")
        return 1

    async def run(adapter):
        return await adapter.operations.run(
            action,
            operation=Operation(operation_id, "m04", participants=(adapter,)),
            fingerprint="same",
            codec=TypeAdapter(int),
            participants=(adapter,),
        )

    try:
        with pytest.raises(ValueError):
            await run(repo)
        assert await repo.get(BOT, USER) is None
        assert await repo.history(BOT, USER) == ()
        other = PostgresJourneyRepository(pool)
        assert await asyncio.gather(run(repo), run(other)) == [1, 1]
        assert attempts == 2
        saved = await other.get(BOT, USER)
        assert saved.completed_at == 2000
        assert saved.sessions == 2
        assert len(await other.history(BOT, USER)) == 1
        assert (await other.history(BOT, USER))[0].items == (("small_healing_potion", 3),)
        assert await other.history(BOT + 1, USER) == ()
        assert await other.history(BOT, USER + 1) == ()
        stats = await other.stats(BOT, inactive_before=3000)
        assert (stats.started, stats.created, stats.completed, stats.returned) == (1, 1, 1, 1)
        assert dict(stats.inactive) == {"main_menu": 1}
        assert dict(stats.reached)["tutorial:hand_in"] == 1
        # Нет ни текста переписки, ни имени, ни принятой кнопки в новой схеме.
        columns = await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='player_journey'"
        )
        assert not {"text", "name", "message", "button"} & {r["column_name"] for r in columns}
    finally:
        await clean(pool)
        await pool.execute("DELETE FROM economic_operations WHERE id=$1", operation_id)


async def test_long_screen_is_persistent_without_redis(pool):
    cache = PostgresGameplayState(pool, InMemoryStateCache())
    token = reading.reader_cache.set(cache)
    saved_key = reading.key(BOT, USER)
    try:
        screen = Screen(ScreenId.COMBAT, ("Бой.", "А" * 5000, "Конец итога."))
        first = await reading.prepare(BOT, USER, screen, False)
        assert "Часть 1 из" in first.body()
        restored = PostgresGameplayState(pool, InMemoryStateCache())
        raw = await restored.get(saved_key)
        assert raw and "Конец итога." in raw
        await restored.delete(saved_key)
        assert not await cache.get(saved_key)
    finally:
        reading.reader_cache.reset(token)
        await pool.execute("DELETE FROM gameplay_state WHERE key=$1", saved_key)
