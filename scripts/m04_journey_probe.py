"""Сохранность истории, этапов и полного текста в копии отдельной базы."""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from scripts.test_stand import load_test_settings

from mmorpg.application.journey import Activity, Visit
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.journey import PostgresJourneyRepository
from mmorpg.presentation.telegram import reading
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only an isolated test database is allowed")
    dsn = urlunsplit(urlsplit(settings.postgres_dsn)._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        journey = PostgresJourneyRepository(pool)
        cache = PostgresGameplayState(pool, InMemoryStateCache())
        bot, user = 1, -99990001
        if seed:
            await journey.observe(Visit(bot, user, "name", 1), None, "m04-backup-control")
            await journey.observe(
                Visit(bot, user, "main_menu", 2, 1, 63),
                Activity(2, gold=8, tutorial_steps=1),
                "m04-backup-control",
            )
            token = reading.reader_cache.set(cache)
            try:
                await reading.prepare(
                    bot,
                    user,
                    Screen(ScreenId.COMBAT, ("Сохранённый бой.", "А" * 5000, "Конец отчёта.")),
                    False,
                )
            finally:
                reading.reader_cache.reset(token)
        else:
            found = await journey.get(bot, user)
            assert found and found.completed_at == 2 and "name" in found.reached
            history = await journey.history(bot, user)
            assert len(history) == 1 and history[0].gold == 8
            report = await journey.stats(bot, inactive_before=3)
            assert (report.started, report.completed) == (1, 1)
            text = await cache.get(reading.key(bot, user))
            assert text and "Конец отчёта." in text and "А" * 5000 in text
    finally:
        await pool.close()
    print("M04 journey, history and complete reading survived backup restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
