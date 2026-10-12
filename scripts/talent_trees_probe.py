"""Ранги дерева и прежняя сборка переживают восстановление отдельной копии."""

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from scripts.test_stand import load_test_settings

from mmorpg.domain.entities.character import Character, SkillLoadout
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules import talents
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresUserRepository,
)

ACCOUNTS = (-99990401, -99990402)


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
        base = Character(
            0,
            ACCOUNTS[0],
            "Таланты контроль",
            "human",
            "warrior",
            level=150,
            gold=317,
            loadout=SkillLoadout(ranks={"warrior_rassechenie": 3}),
        )
        tree = base
        for row in range(1, 6):
            for side in ("a", "b") if row < 5 else ("a",):
                for _ in range(2):
                    changed = talents.choose(content, tree, f"warrior_path_1_{row}_{side}")
                    assert changed is not None
                    tree = changed
            changed = talents.choose(content, tree, f"warrior_{row}_1")
            assert changed is not None
            tree = changed
        old = replace(
            base,
            user_id=ACCOUNTS[1],
            name="Прежние таланты",
            loadout=replace(base.loadout, talents=("warrior_1_1", "warrior_2_1")),
        )
        for account, expected in zip(ACCOUNTS, (tree, old), strict=True):
            if seed:
                await PostgresUserRepository(pool).upsert(User(telegram_id=account))
                await characters.create(expected)
            else:
                saved = await characters.get_active(account)
                assert saved is not None
                assert saved.loadout == expected.loadout and saved.gold == expected.gold
                assert talents.selected(content, saved) == talents.selected(content, expected)
                if account == ACCOUNTS[0]:
                    assert talents.spent(content, saved) == 23
                    assert talents.choose(content, saved, "warrior_path_1_5_b") is None
                    assert talents.choose(content, saved, "warrior_path_2_1_a") is not None
                else:
                    assert talents.legacy_build(saved)
                    assert talents.choose(content, saved, "warrior_path_1_1_a") is None
        print("Talent ranks, final route, continuation and legacy choices verified")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
