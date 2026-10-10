import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.application.services.war_score import WarScoring
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.craft import CraftLog
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.rules import crafts
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import long_goal as rules
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.guild_contract import ContractKind
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
)


def advanced(content, actor):
    log = CraftLog()
    for craft in content.crafts:
        log = log.with_experience(craft.id, crafts.earned_at(content.craft_rules, 10))
    return replace(
        actor,
        level=150,
        city_id=content.long_goals.city_id,
        quests=QuestLog(done=content.long_goals.story_quests),
        crafts=log,
        allocated=StatBlock(STR=200, AGI=200, INT=200, WIS=200, CHA=200),
    )


@pytest.fixture
async def goals(content):
    characters = InMemoryCharacterRepository()
    inventory = InMemoryInventoryRepository()
    cache = InMemoryStateCache()
    guilds = GuildStore(InMemoryGuildRepository(), cache)
    heroes = [
        await characters.create(
            advanced(
                content,
                Character(
                    id=0,
                    user_id=800 + index,
                    name=f"Мастер{index}",
                    race_id="human",
                    class_id="warrior",
                    gold=1000,
                ),
            )
        )
        for index in range(10)
    ]
    mine = await guilds.create("Путники", heroes[0].id)
    foe = await guilds.create("Маяк", heroes[5].id)
    for actor in heroes[1:5]:
        mine = mine.with_member(actor.id, GuildRank.MEMBER)
    for actor in heroes[6:]:
        foe = foe.with_member(actor.id, GuildRank.MEMBER)
    await guilds.save(mine)
    await guilds.save(foe)
    return SimpleNamespace(
        content=content,
        characters=characters,
        inventory=inventory,
        cache=cache,
        guilds=guilds,
        heroes=heroes,
        mine=mine,
        foe=foe,
        service=LongGoals(content, cache, characters, inventory, guilds),
        scoring=WarScoring(guilds, characters),
    )


async def contribute(e, kind, amount=1, receipt=None, member=None):
    return await e.guilds.work_on_contract(
        e.content,
        guild_id=e.mine.id,
        place=guild_rules.standing(e.content, e.mine),
        kind=kind,
        amount=amount,
        world_seed="m09",
        now=10,
        rotation_seconds=100,
        contributors=(member or e.heroes[0].id,),
        receipt=receipt or uuid4().hex,
    )


async def test_project_permissions_snapshot_membership_and_finish_once(goals):
    e = goals
    assert "основатель" in await e.service.start_project(e.heroes[1].id)
    assert "начат" in await e.service.start_project(e.heroes[0].id)
    assert "уже" in await e.service.start_project(e.heroes[0].id)
    await contribute(e, ContractKind.CULL, 999, "fight")
    await contribute(e, ContractKind.DELVE, 999, "descent")
    state = await e.service.project(e.mine.id)
    assert not state.complete and rules.progress(state, "cull") == 12
    before = (await e.guilds.by_id(e.mine.id)).deeds
    await asyncio.gather(*(contribute(e, ContractKind.TITHE, 500, "gold") for _ in range(3)))
    assert (await e.service.project(e.mine.id)).complete
    assert (await e.guilds.by_id(e.mine.id)).deeds - before == state.rules.project_deeds + 8
    # Finished weekly tithe contributes its original 8 deeds too; no project replay.
    again = (await e.guilds.by_id(e.mine.id)).deeds
    await contribute(e, ContractKind.TITHE, 500, "other")
    assert (await e.guilds.by_id(e.mine.id)).deeds == again


async def test_story_requires_old_quests_and_preview_and_wins_one_shared_race(goals):
    e = goals
    actor = e.heroes[0]
    assert "Сначала прочитайте" in await e.service.choose(actor.id, "archive", confirm=True)
    await e.service.choose(actor.id, "archive")
    await e.service.choose(e.heroes[1].id, "signal")
    answers = await asyncio.gather(
        e.service.choose(actor.id, "archive", confirm=True),
        e.service.choose(e.heroes[1].id, "signal", confirm=True),
    )
    assert sum("принял" in one for one in answers) == 1
    assert (await e.characters.get(actor.id)).quests == actor.quests
    await e.service.study(actor.id)
    assert "600" in await e.service.claim(actor.id, "chronicle")
    assert "уже" in await e.service.claim(actor.id, "chronicle")
    assert (await e.characters.get(actor.id)).gold == 1600
    visitor = await e.characters.save(replace(e.heroes[2], quests=QuestLog()))
    assert "завершите" in await e.service.choose(visitor.id, "signal")
    assert "ещё" in await e.service.claim(visitor.id, "chronicle")


@atomic_action
async def broken(service, author_id, command, *, operation_id=None) -> str:
    answer = await service.craft(author_id, 1, command)
    if command == "break":
        raise RuntimeError("after craft")
    return answer


async def materials(e, actor, batches=4):
    for one in e.service.goal_recipes()[0].inputs:
        await e.inventory.add(actor.id, one.item_id, one.count * batches)


async def test_craft_keeps_output_checks_rank_and_rolls_back_then_pays_once(goals):
    e = goals
    actor = e.heroes[0]
    await materials(e, actor)
    before = await e.inventory.list_items(actor.id)
    with pytest.raises(RuntimeError):
        await broken(e.service, actor.id, "break")
    assert await e.inventory.list_items(actor.id) == before
    assert await e.characters.get(actor.id) == actor
    assert not (await e.service.goals(actor.id)).crafted
    for index in range(3):
        assert "Изготовлено" in await e.service.craft(actor.id, 1, str(index))
    assert "зачтена" in await e.service.craft(actor.id, 1, "fourth")
    bag = await e.inventory.list_items(actor.id)
    assert sum(one.quantity for one in bag if "head@140" in one.item_id) == 3
    await asyncio.gather(*(e.service.claim(actor.id, "craft") for _ in range(3)))
    assert (await e.characters.get(actor.id)).gold == 1600
    assert "номер" in await e.service.craft(actor.id, 999, "x")
    beginner = await e.characters.save(replace(e.heroes[1], crafts=CraftLog()))
    assert "ранг" in (await e.service.craft(beginner.id, 1, "low")).casefold()


async def test_trial_requires_named_complete_group_of_distinct_accounts(goals):
    e = goals
    a, b = e.heroes[:2]
    await e.service.record_descent((a.id,), e.content.long_goals.dungeon_id, "solo")
    await e.service.record_descent((a.id, b.id), "other", "other")
    assert not (await e.service.goals(a.id)).trial
    await e.characters.save(replace(b, user_id=a.user_id))
    await e.service.record_descent((a.id, b.id), e.content.long_goals.dungeon_id, "alt")
    assert not (await e.service.goals(a.id)).trial
    await e.characters.save(replace(await e.characters.get(b.id), user_id=b.user_id))
    await e.service.record_descent((a.id, b.id), e.content.long_goals.dungeon_id, "full")
    assert (await e.service.goals(a.id)).trial and (await e.service.goals(b.id)).trial
    assert "600" in await e.service.claim(a.id, "trial")


async def war(e, *, started=0, ends=9999999):
    opened = await e.guilds.open_war(
        challenger_id=e.mine.id,
        defender_id=e.foe.id,
        stake=0,
        started=started,
        ends=ends,
        clock_seconds=True,
    )
    await e.scoring.begin(opened)
    return opened


async def test_five_against_five_is_one_point_for_accounts_and_no_period_reset(goals):
    e = goals
    opened = await war(e)
    winners = tuple(one.id for one in e.heroes[:5])
    losers = tuple(one.id for one in e.heroes[5:])
    assert "одно очко" in await e.scoring.score(
        opened, e.mine.id, winners, losers, "5x5", now=100, account_seconds=1000
    )
    assert (await e.guilds.war_of(e.mine.id)).challenger_score == 1
    assert len((await e.scoring.load(opened.id)).used_accounts) == 10
    assert "уже учтён" in await e.scoring.score(
        opened, e.mine.id, winners, losers, "5x5", now=2000, account_seconds=1000
    )
    assert "уже участвовал" in await e.scoring.score(
        opened, e.mine.id, winners, losers, "repeat", now=2000, account_seconds=1000
    )
    assert not await e.guilds.score_war(
        opened,
        guild_id=e.mine.id,
        winner_id=winners[0],
        loser_id=losers[0],
        now=2000,
        rotation_seconds=1,
    )


async def test_alternate_same_account_and_changed_guild_cannot_score(goals):
    e = goals
    opened = await war(e)
    actor = await e.characters.create(replace(e.heroes[0], id=0, name="Другой герой"))
    current = await e.guilds.by_id(e.mine.id)
    await e.guilds.save(current.with_member(actor.id, GuildRank.MEMBER))
    assert "Состав изменился" in await e.scoring.score(
        opened, e.mine.id, (actor.id,), (e.heroes[5].id,), "alt", now=100, account_seconds=1000
    )
    assert (await e.guilds.war_of(e.mine.id)).challenger_score == 0
    assert "должны действовать" in await e.scoring.score(
        opened, e.mine.id, (), (e.heroes[5].id,), "idle", now=100, account_seconds=1000
    )


async def test_old_war_is_closed_to_new_score_on_every_attempt(goals):
    e = goals
    opened = await e.guilds.open_war(
        challenger_id=e.mine.id,
        defender_id=e.foe.id,
        stake=0,
        started=0,
        ends=9999,
        clock_seconds=True,
    )
    for index in range(2):
        assert "прежней войны" in await e.scoring.score(
            opened,
            e.mine.id,
            (e.heroes[0].id,),
            (e.heroes[5].id,),
            str(index),
            now=100,
            account_seconds=1000,
        )
    assert (await e.guilds.war_of(e.mine.id)).challenger_score == 0


async def test_account_cooldown_crosses_wars_and_calendar_boundary(goals):
    e = goals
    opened = await war(e, ends=1000)
    a, b = e.heroes[0], e.heroes[5]
    assert "одно очко" in await e.scoring.score(
        opened, e.mine.id, (a.id,), (b.id,), "first", now=999, account_seconds=1000
    )
    await e.guilds.settle_war(e.content, opened, opened.ends)
    second = await war(e, started=1000, ends=9999)
    assert "перерыв" in await e.scoring.score(
        second, e.mine.id, (a.id,), (b.id,), "next-day", now=1000, account_seconds=1000
    )
    assert "одно очко" in await e.scoring.score(
        second, e.mine.id, (a.id,), (b.id,), "later", now=1999, account_seconds=1000
    )


async def test_same_account_on_both_sides_and_foreign_guild_rejected(goals):
    e = goals
    opponent = await e.characters.save(replace(e.heroes[5], user_id=e.heroes[0].user_id))
    opened = await war(e)
    assert "Один аккаунт" in await e.scoring.score(
        opened,
        e.mine.id,
        (e.heroes[0].id,),
        (opponent.id,),
        "same-account",
        now=100,
        account_seconds=1000,
    )
    assert "не относится" in await e.scoring.score(
        opened,
        999,
        (e.heroes[0].id,),
        (opponent.id,),
        "foreign",
        now=100,
        account_seconds=1000,
    )


async def test_preview_is_revalidated_after_content_change(goals):
    e = goals
    actor = e.heroes[0]
    await e.service.choose(actor.id, "archive")
    config = e.content.long_goals
    outcome = replace(config.outcomes[0], travel_discount=15)
    e.service.content = replace(
        e.content, long_goals=replace(config, outcomes=(outcome, config.outcomes[1]))
    )
    assert "Сначала прочитайте" in await e.service.choose(actor.id, "archive", confirm=True)
    assert await e.service.story() is None
