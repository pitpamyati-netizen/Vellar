"""Копия сохраняет время и дробь отдыха; следующий расчёт совпадает с непрерывным."""

import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from scripts.test_stand import load_test_settings

from mmorpg.application.services.health_recovery import HealthRecovery
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.health_recovery import recover
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresUserRepository,
)

ACCOUNT = -99990399


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only isolated test databases are allowed")
    dsn = urlunsplit(urlsplit(settings.postgres_dsn)._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        content = load_content(Path("content"))
        characters = PostgresCharacterRepository(pool)
        service = HealthRecovery(
            content, characters, PostgresGameplayState(pool, InMemoryStateCache())
        )
        original = Character(
            0, ACCOUNT, "Отдых контроль", "human", "paladin", health=1, health_recovered_at=1000
        )
        if seed:
            await PostgresUserRepository(pool).upsert(User(telegram_id=ACCOUNT))
            hero = await characters.create(original)
            await service.apply(hero.id, now=1001)
        else:
            restored = await characters.get_active(ACCOUNT)
            assert restored is not None
            expected = recover(content, original, now=1001)
            assert (
                restored.health,
                restored.health_recovered_at,
                restored.health_recovery_remainder,
            ) == (expected.health, expected.health_recovered_at, expected.health_recovery_remainder)
            continued = await service.apply(restored.id, now=1060)
            assert continued is not None
            assert continued.health == recover(content, original, now=1060).health
            assert content.character_class(restored.class_id).resource.name == "Выносливость"
        print("Health recovery time, fraction and continuation verified")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
