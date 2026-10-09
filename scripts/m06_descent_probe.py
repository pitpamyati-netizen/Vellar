"""Участие, правила встреч и отметки выдачи в восстановленной копии стенда."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from scripts.test_stand import load_test_settings

from mmorpg.application.services.battle import BattleKind, BattleStore, begin
from mmorpg.domain.entities import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules import expedition
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import Descent, PlayState

ACCOUNT = -99990600
BATTLE = "m06-backup-control"


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only an isolated test database is allowed")
    dsn = urlunsplit(urlsplit(settings.postgres_dsn)._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        content = load_content(Path("content"))
        characters = PostgresCharacterRepository(pool)
        cache = PostgresGameplayState(pool, PostgresEffectState(pool, InMemoryStateCache()))
        store = BattleStore(cache)
        if seed:
            await PostgresUserRepository(pool).upsert(User(telegram_id=ACCOUNT))
            hero = await characters.create(
                Character(
                    id=0,
                    user_id=ACCOUNT,
                    name="Помпа",
                    race_id="human",
                    class_id="warrior",
                    level=15,
                )
            )
            meeting = content.city("farhold").deep_dungeon.encounters[2]
            flow = PlayState(
                descent=Descent(
                    city_id="farhold",
                    dungeon_id="farhold_flooded_drift",
                    layer=2,
                    credits=((hero.id, 2),),
                    excluded=(999999,),
                    participation_rule=1,
                    encounter_id=BATTLE,
                )
            )
            battle, _ = begin(
                content,
                battle_id=BATTLE,
                attackers=[(hero, True)],
                enemies=expedition.foes(
                    content, meeting, seed=b"backup", level=15, stakes=1.0, bounty=1.0
                ),
                seed=b"backup",
                owner=hero.id,
                kind=BattleKind.DESCENT,
                participation_rule=1,
                briefing=meeting.briefing,
            )
            battle = replace(
                battle,
                play_snapshot=flow.serialise(),
                state=replace(
                    battle.state,
                    combatants=tuple(
                        replace(one, actions=2) if one.is_hero else one
                        for one in battle.state.combatants
                    ),
                ),
            )
            await store.save(await store.pin(battle, content))
            await cache.set(f"descent-paid:{BATTLE}:{hero.id}", "1", 1)
            await cache.set(f"battle-credit:{BATTLE}:42:cull", "1", 1)
        else:
            hero = await characters.get_active(ACCOUNT)
            battle = await store.load(BATTLE)
            assert hero and battle and battle.participation_rule == 1
            assert battle.state.heroes()[0].actions == 2
            descent = PlayState.deserialise(battle.play_snapshot).descent
            assert descent.credits == ((hero.id, 2),) and descent.excluded == (999999,)
            assert descent.participation_rule == 1 and descent.encounter_id == BATTLE
            pinned = await store.content(battle, replace(content, cities=()))
            assert (
                pinned.city("farhold").deep_dungeon.encounters
                == content.city("farhold").deep_dungeon.encounters
            )
            assert await cache.get(f"descent-paid:{BATTLE}:{hero.id}") == "1"
            assert await cache.get(f"battle-credit:{BATTLE}:42:cull") == "1"
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
    print("M06: descent participation, catalogue and reward marks verified")


if __name__ == "__main__":
    main()
