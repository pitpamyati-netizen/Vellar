"""Контрольный незавершённый бой для копии отдельной базы M00."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from aiogram.fsm.storage.base import StorageKey
from scripts.test_stand import load_test_settings

from mmorpg.application.battle_snapshots import encode
from mmorpg.application.services.battle import BattleStore, begin
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.content.loader import load_content
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
from mmorpg.infrastructure.persistence.postgres import PostgresCharacterRepository
from mmorpg.infrastructure.persistence.world import PostgresLocationState
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.states.screens import Play


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only an isolated test database is allowed")
    url = urlsplit(settings.postgres_dsn)
    dsn = urlunsplit(url._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        characters = PostgresCharacterRepository(pool)
        hero = await characters.get_active(-99990001)
        assert hero is not None
        cache = PostgresGameplayState(pool, InMemoryStateCache())
        store = BattleStore(cache)
        storage = PostgresStorage(pool)
        key = StorageKey(bot_id=1, chat_id=hero.user_id, user_id=hero.user_id)
        world = PostgresLocationState(pool)
        content = load_content(Path("content"))
        if seed:
            from dataclasses import replace

            flow = PlayState(session=LocationSession("farhold", 1, 1, 0))
            battle, roster = begin(
                content,
                battle_id="m03-backup-control",
                attackers=[(hero, True)],
                enemies=[
                    Enemy(
                        archetype_id="grey_wolf",
                        name="Волк",
                        kind=EnemyKind.BEAST,
                        level=1,
                        max_health=9999,
                        damage=1,
                        armor=0,
                        initiative=0,
                        loot=(),
                        gold=10,
                    )
                ],
                seed=b"m03-backup",
                owner=hero.id,
            )
            battle = replace(
                battle, roster_snapshot=json.dumps(encode(roster)), play_snapshot=flow.serialise()
            )
            battle = await store.pin(battle, content)
            battle = await store.save(battle)
            assert not battle.state.is_over
            await storage.set_state(key, Play.combat)
            await storage.set_data(key, {"battle": battle.id, "play": flow.serialise()})
            await world.engage(
                "farhold",
                1,
                1,
                wave=0,
                place=0,
                battle_id=battle.id,
                name=hero.name,
                character_id=hero.id,
                now=1,
                ttl=1,
            )
        else:
            battle = await store.load("m03-backup-control")
            assert battle is not None and battle.version == 1 and battle.content_version
            assert not battle.state.is_over
            assert (await store.content(battle, content)).skills == content.skills
            assert await store.busy(hero.id) == battle.id
            assert await storage.get_state(key) == Play.combat.state
            assert (await storage.get_data(key))["battle"] == battle.id
            assert (await world.engaged_at("farhold", 1, 1, wave=0, now=99999, ttl=1))[
                0
            ].battle_id == battle.id
    finally:
        await pool.close()
    print("M03 persistent battle, content, occupation and screen: verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
