"""Копия сохраняет проект, общий выбор, цели, очко и защиту аккаунта."""

import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from scripts.test_stand import load_test_settings

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.application.services.war_score import WarScoring
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.craft import CraftLog
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules import crafts
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.guild_contract import ContractKind
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresGuildRepository,
    PostgresInventoryRepository,
    PostgresUserRepository,
)

ACCOUNTS = (-99990900, -99990901, -99990902)


@atomic_action
async def claim(service: LongGoals, author_id: int, command: str, *, operation_id: str) -> str:
    return await service.claim(author_id, command)


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
        inventory = PostgresInventoryRepository(pool, content)
        cache = PostgresGameplayState(pool, InMemoryStateCache())
        guilds = GuildStore(PostgresGuildRepository(pool), cache)
        service = LongGoals(content, cache, characters, inventory, guilds)
        scoring = WarScoring(guilds, characters)
        if seed:
            users = PostgresUserRepository(pool)
            people = []
            work = CraftLog().with_experience("smithing", crafts.earned_at(content.craft_rules, 10))
            for index, account in enumerate(ACCOUNTS):
                await users.upsert(User(telegram_id=account))
                people.append(
                    await characters.create(
                        Character(
                            id=0,
                            user_id=account,
                            name=f"Путник{index}",
                            race_id="human",
                            class_id="warrior",
                            city_id=content.long_goals.city_id,
                            level=150,
                            gold=500,
                            quests=QuestLog(done=content.long_goals.story_quests),
                            crafts=work,
                            allocated=StatBlock(STR=200),
                        )
                    )
                )
            first, second, opponent = people
            mine = await guilds.create("Копия путевого двора", first.id)
            await guilds.save(mine.with_member(second.id, GuildRank.MEMBER))
            foe = await guilds.create("Копия маяка", opponent.id)
            await service.start_project(first.id)
            for kind, amount in (
                (ContractKind.CULL, 12),
                (ContractKind.DELVE, 3),
                (ContractKind.TITHE, 500),
            ):
                await guilds.work_on_project(
                    content, mine.id, kind, amount, (first.id,), kind.value
                )
            await service.choose(first.id, "archive")
            await service.choose(first.id, "archive", confirm=True)
            await service.study(first.id)
            assert "600" in await claim(
                service, first.id, "chronicle", operation_id="m09-probe:claim"
            )
            await service.record_descent(
                (first.id, second.id), content.long_goals.dungeon_id, "complete-run"
            )
            for one in service.goal_recipes()[0].inputs:
                await inventory.add(first.id, one.item_id, one.count * 3)
            assert "Изготовлено" in await service.craft(first.id, 1, "first-work")
            war = await guilds.open_war(
                challenger_id=mine.id,
                defender_id=foe.id,
                stake=0,
                started=0,
                ends=999999,
                clock_seconds=True,
            )
            await scoring.begin(war)
            assert "одно очко" in await scoring.score(
                war,
                mine.id,
                (first.id,),
                (opponent.id,),
                "probe-fight",
                now=100,
                account_seconds=604800,
            )
        else:
            actors = [await characters.get_active(one) for one in ACCOUNTS]
            assert all(actors)
            first, second, opponent = actors
            assert first is not None and second is not None and opponent is not None
            mine = await guilds.of(first.id)
            assert mine is not None and mine.deeds == 20
            assert (project := await service.project(mine.id)) is not None and project.complete
            assert (story := await service.story()) is not None and story.outcome.id == "archive"
            state = await service.goals(first.id)
            assert (
                state.crafted == 1
                and state.trial
                and state.studied
                and state.claimed == ("chronicle",)
            )
            assert first.level == 150 and first.gold == 1100
            assert first.quests.done == content.long_goals.story_quests
            assert (await service.goals(second.id)).trial
            assert "600" in await claim(
                service, first.id, "chronicle", operation_id="m09-probe:claim"
            )
            assert "уже" in await service.claim(first.id, "chronicle")
            assert (again := await characters.get(first.id)) is not None and again.gold == 1100
            war = await guilds.war_of(mine.id)
            assert war is not None and war.challenger_score == 1
            assert "уже учтён" in await scoring.score(
                war,
                mine.id,
                (first.id,),
                (opponent.id,),
                "probe-fight",
                now=200,
                account_seconds=604800,
            )
            assert "уже участвовал" in await scoring.score(
                war,
                mine.id,
                (first.id,),
                (opponent.id,),
                "new-fight",
                now=200,
                account_seconds=604800,
            )
            assert (saved := await scoring.load(war.id)) is not None and len(saved.battles) == 2
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
    print("M09: project, story, goals, reward replay and account score survived restore")


if __name__ == "__main__":
    main()
