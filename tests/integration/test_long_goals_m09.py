import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.long_goal import LongGoals
from mmorpg.application.services.war_score import WarScoring
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.guild_contract import ContractKind
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import PostgresGuildRepository
from tests.application.test_long_goals_m09 import advanced, materials
from tests.integration.test_durable_battle_m03 import battle_env as battle_env
from tests.integration.test_durable_battle_m03 import press, state_for
from tests.integration.test_economic_operations import economy as economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def goals_sql(economy, content):
    e = economy
    await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'long-goal:%'")
    e.content = content
    e.cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, InMemoryStateCache()))
    e.guilds = GuildStore(PostgresGuildRepository(e.pool), e.cache)
    e.heroes = [await e.characters.save(advanced(content, one)) for one in e.heroes]
    e.mine = await e.guilds.create("Путь " + uuid4().hex[:10], e.heroes[0].id)
    e.mine = e.mine.with_member(e.heroes[1].id, GuildRank.MEMBER)
    await e.guilds.save(e.mine)
    e.foe = await e.guilds.create("Маяк " + uuid4().hex[:10], e.heroes[2].id)
    e.service = LongGoals(content, e.cache, e.characters, e.inventory, e.guilds)
    e.scoring = WarScoring(e.guilds, e.characters)
    try:
        yield e
    finally:
        if war := await e.guilds.war_of(e.mine.id):
            await e.guilds.settle_war(content, war, 10**15)
        await e.guilds.disband(e.mine)
        await e.guilds.disband(e.foe)
        await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'long-goal:%'")


@atomic_action
async def command(service, author_id, command, *, operation_id=None) -> str:
    if command.startswith("craft"):
        answer = await service.craft(author_id, 1, command)
    elif command.startswith("story"):
        answer = await service.choose(author_id, "archive", confirm=True)
    else:
        answer = await service.claim(author_id, "chronicle")
    if command.endswith("break"):
        raise RuntimeError("after persistent goal")
    return answer


def fresh(e):
    cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, InMemoryStateCache()))
    guilds = GuildStore(PostgresGuildRepository(e.pool), cache)
    return LongGoals(e.content, cache, e.characters, e.inventory, guilds)


async def test_craft_atomic_failure_replay_new_adapter_and_different_message(goals_sql):
    e = goals_sql
    actor = e.heroes[0]
    await materials(e, actor)
    before = await e.inventory.list_items(actor.id)
    key = "m09:" + uuid4().hex
    with pytest.raises(RuntimeError):
        await command(e.service, actor.id, "craft-break", operation_id=key)
    assert await e.inventory.list_items(actor.id) == before
    assert (await e.characters.get(actor.id)).crafts == actor.crafts
    assert not (await e.service.goals(actor.id)).crafted
    assert not await e.pool.fetchval("SELECT 1 FROM economic_operations WHERE id=$1", key)
    key = "m09:" + uuid4().hex
    result = await command(e.service, actor.id, "craft-ok", operation_id=key)
    assert "Изготовлено" in result
    remaining = await e.inventory.list_items(actor.id)
    await e.redis.flushdb()
    assert await command(fresh(e), actor.id, "craft-ok", operation_id=key) == result
    assert await e.inventory.list_items(actor.id) == remaining
    assert "зачтена" in await fresh(e).craft(actor.id, 1, "craft-ok")
    assert (await fresh(e).goals(actor.id)).crafted == 1


async def test_shared_story_and_entitlement_survive_failure_race_and_cache_loss(goals_sql):
    e = goals_sql
    actor = e.heroes[0]
    await e.service.choose(actor.id, "archive")
    with pytest.raises(RuntimeError):
        await command(e.service, actor.id, "story-break", operation_id="m09:" + uuid4().hex)
    assert await e.service.story() is None
    await e.service.choose(e.heroes[1].id, "signal")
    results = await asyncio.gather(
        e.service.choose(actor.id, "archive", confirm=True),
        e.service.choose(e.heroes[1].id, "signal", confirm=True),
    )
    assert sum("принял" in result for result in results) == 1
    await e.service.study(actor.id)
    with pytest.raises(RuntimeError):
        await command(e.service, actor.id, "claim-break", operation_id="m09:" + uuid4().hex)
    assert (await e.characters.get(actor.id)).gold == 500
    assert not (await e.service.goals(actor.id)).claimed
    await asyncio.gather(*(e.service.claim(actor.id, "chronicle") for _ in range(4)))
    await e.redis.flushdb()
    assert (await e.characters.get(actor.id)).gold == 1100
    assert "уже" in await fresh(e).claim(actor.id, "chronicle")
    assert (await fresh(e).story()).outcome.id in {"archive", "signal"}
    assert (await e.characters.get(actor.id)).quests == actor.quests


async def test_project_last_step_payout_and_marks_rollback_together(goals_sql, monkeypatch):
    e = goals_sql
    await e.service.start_project(e.heroes[0].id)
    await e.guilds.work_on_project(
        e.content, e.mine.id, ContractKind.CULL, 12, (e.heroes[0].id,), "battles"
    )
    await e.guilds.work_on_project(
        e.content, e.mine.id, ContractKind.DELVE, 3, (e.heroes[1].id,), "descents"
    )
    original = e.guilds._roster.add_deeds

    async def broken(guild_id, deeds):
        await original(guild_id, deeds)
        raise RuntimeError("after project reward")

    monkeypatch.setattr(e.guilds._roster, "add_deeds", broken)
    with pytest.raises(RuntimeError):
        await e.guilds.work_on_project(
            e.content, e.mine.id, ContractKind.TITHE, 500, (e.heroes[0].id,), "gold"
        )
    assert not (await e.service.project(e.mine.id)).complete
    assert (await e.guilds.by_id(e.mine.id)).deeds == 0
    monkeypatch.setattr(e.guilds._roster, "add_deeds", original)
    await asyncio.gather(
        *(
            e.guilds.work_on_project(
                e.content, e.mine.id, ContractKind.TITHE, 500, (e.heroes[0].id,), "gold"
            )
            for _ in range(3)
        )
    )
    assert (await e.guilds.by_id(e.mine.id)).deeds == 20
    await e.redis.flushdb()
    assert (await fresh(e).project(e.mine.id)).complete


async def test_war_account_score_receipt_and_cooldown_are_one_operation(goals_sql, monkeypatch):
    e = goals_sql
    war = await e.guilds.open_war(
        challenger_id=e.mine.id,
        defender_id=e.foe.id,
        stake=0,
        started=0,
        ends=99999999,
        clock_seconds=True,
    )
    await e.scoring.begin(war)
    original = e.guilds._roster.score_war

    async def broken(war_id, guild_id):
        await original(war_id, guild_id)
        raise RuntimeError("after point")

    monkeypatch.setattr(e.guilds._roster, "score_war", broken)
    args = (war, e.mine.id, (e.heroes[0].id,), (e.heroes[2].id,), "fight")
    with pytest.raises(RuntimeError):
        await e.scoring.score(*args, now=100, account_seconds=1000)
    assert (await e.guilds.war_of(e.mine.id)).challenger_score == 0
    assert not (await e.scoring.load(war.id)).used_accounts
    monkeypatch.setattr(e.guilds._roster, "score_war", original)
    await asyncio.gather(*(e.scoring.score(*args, now=100, account_seconds=1000) for _ in range(3)))
    assert (await e.guilds.war_of(e.mine.id)).challenger_score == 1
    await e.redis.flushdb()
    restarted = WarScoring(fresh(e).guilds, e.characters)
    assert "уже учтён" in await restarted.score(*args, now=2000, account_seconds=1000)
    assert "уже участвовал" in await restarted.score(
        war,
        e.mine.id,
        (e.heroes[0].id,),
        (e.heroes[2].id,),
        "another-day",
        now=2000,
        account_seconds=1000,
    )


async def test_restored_trial_keeps_each_full_participants_entitlement(goals_sql):
    e = goals_sql
    keys = tuple(one.id for one in e.heroes[:2])
    await e.service.record_descent(keys, e.content.long_goals.dungeon_id, "complete-run")
    await e.redis.flushdb()
    service = fresh(e)
    for key in keys:
        assert (await service.goals(key)).trial
        assert "600" in await service.claim(key, "trial")
        assert "уже" in await service.claim(key, "trial")


@pytest.mark.parametrize("late", [False, True])
async def test_actual_final_battle_credits_full_group_and_rolls_back_with_result(battle_env, late):
    from mmorpg.application.services.battle import BattleKind
    from mmorpg.presentation.telegram.flows.state import Descent, PlayState

    e = battle_env
    await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'long-goal:%'")
    config = e.content.long_goals
    for index, actor in enumerate(e.heroes[:2]):
        e.heroes[index] = await e.characters.save(advanced(e.content, actor))
    e.flow = PlayState(
        city_id=config.city_id,
        descent=Descent(
            city_id=config.city_id,
            dungeon_id=config.dungeon_id,
            level=150,
            layer=3,
            room="lair",
            group=True,
            started_at=100,
            encounter_id=e.session.id,
            credits=tuple(
                (actor.id, 3) for index, actor in enumerate(e.heroes[:2]) if not late or index == 0
            ),
            participation_rule=1,
        ),
    )
    fighters = tuple(
        replace(one, actions=1) if one.is_hero else replace(one, health=0)
        for one in e.session.state.combatants
    )
    e.session = await e.store.save(
        replace(
            e.session,
            kind=BattleKind.DESCENT,
            depth=4,
            participation_rule=1,
            city_id=config.city_id,
            play_snapshot=e.flow.serialise(),
            state=replace(e.session.state, combatants=fighters),
        )
    )
    for index in range(2):
        await state_for(e, index).update_data(
            {"play": e.flow.serialise(), "battle_version": e.session.version}
        )
    service = LongGoals(e.content, e.cache, e.characters, e.inventory, e.guilds)

    async def fail(name):
        if name == "after_world":
            raise RuntimeError("after trial and hero")

    with pytest.raises(RuntimeError):
        await press(e, "итог испытания", hook=fail)
    assert not (await service.goals(e.heroes[0].id)).trial
    assert not (await e.store.load(e.session.id)).settled

    async def accept(_):
        pass

    await press(e, "итог испытания", hook=accept)
    assert (await service.goals(e.heroes[0].id)).trial == (not late)
    assert (await service.goals(e.heroes[1].id)).trial == (not late)
    await e.redis.flushdb()
    recovered = LongGoals(
        e.content,
        PostgresGameplayState(e.pool, InMemoryStateCache()),
        e.characters,
        e.inventory,
        e.guilds,
    )
    assert (await recovered.goals(e.heroes[0].id)).trial == (not late)
    await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'long-goal:%'")
