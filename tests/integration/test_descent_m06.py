"""Индивидуальная выдача, общий зачёт и возврат после сбоя на настоящем SQL."""

import asyncio
from dataclasses import replace

import pytest

from mmorpg.application.services.battle import BattleKind, BattleStore
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
from mmorpg.presentation.telegram.flows.state import Descent, PlayState
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from tests.integration.test_durable_battle_m03 import (
    battle_env,  # noqa: F401
    press,
    state_for,
)
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_command_journal import dependencies

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def prepare(e, *, idle=False, late=False, dead=False, left=False):
    for index, hero in enumerate(e.heroes[:2]):
        e.heroes[index] = await e.characters.save(
            replace(hero, quests=hero.quests.take("farhold_pump_house"))
        )
    credits = [(hero.id, 3) for hero in e.heroes[:2] if not late or hero == e.heroes[0]]
    e.flow = PlayState(
        descent=Descent(
            city_id="farhold",
            dungeon_id="farhold_flooded_drift",
            level=15,
            layer=3,
            room="lair",
            started_at=100,
            encounter_id=e.session.id,
            credits=tuple(credits),
            participation_rule=1,
        )
    )
    combatants = tuple(
        replace(
            one,
            actions=0 if idle and one.character_id == e.heroes[1].id else 1,
            health=0 if dead and one.character_id == e.heroes[1].id else one.health,
            left=left and one.character_id == e.heroes[1].id,
        )
        if one.is_hero
        else replace(one, health=0)
        for one in e.session.state.combatants
    )
    e.session = await e.store.save(
        replace(
            e.session,
            kind=BattleKind.DESCENT,
            depth=4,
            participation_rule=1,
            play_snapshot=e.flow.serialise(),
            state=replace(e.session.state, combatants=combatants, experience=100, gold=100),
        )
    )
    for actor in range(2):
        await state_for(e, actor).update_data(
            {"play": e.flow.serialise(), "battle_version": e.session.version}
        )


async def accept(_):
    pass


@pytest.mark.parametrize(
    "point", ["after_character", "after_world", "after_result", "after_screens", "after_replies"]
)
async def test_all_individual_awards_and_entitlements_roll_back_then_recover_once(
    battle_env,  # noqa: F811
    point,
):
    e = battle_env
    await prepare(e)
    before = await e.store.load(e.session.id)

    async def fail(name):
        if name == point:
            raise RuntimeError("m06 fault")

    with pytest.raises(RuntimeError, match="m06 fault"):
        await press(e, "итог спуска", hook=fail)
    assert await e.store.load(e.session.id) == before
    for hero in e.heroes[:2]:
        saved = await e.characters.get(hero.id)
        assert saved.gold == 500 and saved.quests.progress("farhold_pump_house") == 0
        assert (
            await e.cache.get(f"descent-paid:{e.flow.descent.encounter(e.session.owner)}:{hero.id}")
            is None
        )
    assert not e.recording.sent

    await press(e, "итог спуска", hook=accept)
    balances = [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]]
    assert all(value > 500 for value in balances)
    for hero in e.heroes[:2]:
        assert (await e.characters.get(hero.id)).quests.progress("farhold_pump_house") == 1
        assert (
            await e.cache.get(f"descent-paid:{e.flow.descent.encounter(e.session.owner)}:{hero.id}")
            == "1"
        )
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM durable_effects WHERE key LIKE $1",
            f"descent-paid:{e.flow.descent.encounter(e.session.owner)}:%",
        )
        == 2
    )
    await e.redis.flushdb()
    e.cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, RedisStateCache(e.redis)))
    e.store = BattleStore(e.cache)
    e.storage = PostgresStorage(e.pool)
    e.journal = CommandMiddleware(
        dependencies(
            e.characters,
            cache=e.cache,
            world=e.world,
            guilds=e.guilds,
            parties=e.parties,
            inventory=e.inventory,
        ),
        e.reaper,
    )
    await press(e, "повтор итога новым сообщением", hook=accept)
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]] == balances
    assert (await e.store.load(e.session.id)).settled


@pytest.mark.parametrize("case", ["idle", "late", "dead", "left"])
async def test_inactive_late_and_fallen_participants_have_their_own_result(battle_env, case):  # noqa: F811
    e = battle_env
    await prepare(e, **{case: True})
    await press(e, "итог спуска", hook=accept)
    first, second = [(await e.characters.get(hero.id)) for hero in e.heroes[:2]]
    assert first.quests.progress("farhold_pump_house") == 1
    assert second.quests.progress("farhold_pump_house") == (1 if case == "dead" else 0)
    if case in {"idle", "left"}:
        assert second.gold == 500
    else:
        assert second.gold > 500
    result = await e.store.load(e.session.id)
    assert result.state.outcome is BattleOutcome.DECIDED
    assert str(second.id) in result.results


async def test_two_finalisers_do_not_pay_the_same_descent_twice(battle_env):  # noqa: F811
    e = battle_env
    await prepare(e)
    await asyncio.gather(
        press(e, "итог один", hook=accept), press(e, "итог два", actor=1, hook=accept)
    )
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM durable_effects WHERE key LIKE $1",
            f"descent-paid:{e.flow.descent.encounter(e.session.owner)}:%",
        )
        == 2
    )
    assert (await e.store.load(e.session.id)).settled


async def test_connection_outage_keeps_each_reply_and_does_not_repeat_awards(battle_env):  # noqa: F811
    from mmorpg.application.delivery import DeliveryPolicy
    from mmorpg.infrastructure.persistence.delivery import PostgresDeliveryQueue
    from mmorpg.presentation.telegram.delivery import DeliveryWorker, DurableSendMiddleware
    from tests.presentation.test_command_journal import RecordingSession

    e = battle_env
    await prepare(e)
    # Постоянный отправитель подключён так же, как в main; старый тестовый
    # отправитель после операции заменяем, чтобы не перехватывать ответ дважды.
    await e.bot.session.close()
    e.recording = RecordingSession()
    e.bot.session = e.recording
    queue = PostgresDeliveryQueue(e.pool)
    worker = DeliveryWorker(queue, e.bot, policy=DeliveryPolicy(private_limit=100, group_limit=100))
    e.bot.session.middleware(DurableSendMiddleware(queue, worker))
    e.journal = CommandMiddleware(
        dependencies(
            e.characters,
            cache=e.cache,
            world=e.world,
            guilds=e.guilds,
            parties=e.parties,
            inventory=e.inventory,
        ),
        e.reaper,
        delivery=queue,
        wake_delivery=worker.wake,
    )
    try:
        e.recording.fail = True
        await press(e, "итог при обрыве связи", hook=accept)
        balances = [(await e.characters.get(one.id)).gold for one in e.heroes[:2]]
        assert all(one > 500 for one in balances)
        assert not e.recording.sent
        assert (
            await e.pool.fetchval(
                "SELECT count(*) FROM message_delivery WHERE bot_id=$1 AND status <> 'sent'",
                e.bot.id,
            )
            >= 2
        )
        await e.redis.flushdb()
        e.recording.fail = False
        worker.start()
        async with asyncio.timeout(10):
            # У внешнего отправителя нет общего Event: проверяем сохранённый исход в SQL.
            while await e.pool.fetchval(  # noqa: ASYNC110
                "SELECT count(*) FROM message_delivery WHERE bot_id=$1 AND status <> 'sent'",
                e.bot.id,
            ):
                await asyncio.sleep(0.05)
        for one in e.heroes[:2]:
            assert any(
                int(sent.chat_id) == one.user_id and "Дно спуска:" in sent.text
                for sent in e.recording.sent
            )
        assert all(sent.parse_mode is None for sent in e.recording.sent)
        await press(e, "повтор после связи", hook=accept)
        assert [(await e.characters.get(one.id)).gold for one in e.heroes[:2]] == balances
    finally:
        await worker.aclose()
        await e.pool.execute("DELETE FROM message_delivery WHERE bot_id=$1", e.bot.id)
        await e.pool.execute("DELETE FROM message_delivery_attempts WHERE bot_id=$1", e.bot.id)
        await e.pool.execute("DELETE FROM message_delivery_budget WHERE bot_id=$1", e.bot.id)


@pytest.mark.parametrize("count", [1, 2, 5])
async def test_one_battle_and_descent_count_once_for_a_guild_but_for_each_member(battle_env, count):  # noqa: F811
    import time

    from mmorpg.application.services.battle import begin
    from mmorpg.domain.ports.repositories import User
    from mmorpg.domain.rules import guild as guild_rules
    from mmorpg.domain.rules.guild_contract import ContractKind
    from tests.integration.test_durable_battle_m03 import SETTINGS

    e = battle_env
    while len(e.heroes) < count:
        account = -989501 - len(e.heroes)
        e.ids.append(account)
        await e.users.upsert(User(telegram_id=account))
        e.heroes.append(
            await e.characters.create(
                replace(e.heroes[0], id=0, user_id=account, name=f"Участник {len(e.heroes)}")
            )
        )
    await prepare(e)
    guild = await e.guilds.create(f"Помпа {e.heroes[0].id}", e.heroes[0].id)
    for one in e.heroes[1:count]:
        await e.guilds.call(guild_id=guild.id, invitee_id=one.id, inviter_id=e.heroes[0].id)
        assert await e.guilds.accept(one.id, e.content)
    actors = e.heroes[:count]
    for one in actors:
        stored = await e.characters.get(one.id)
        if not stored.quests.is_taken("farhold_pump_house"):
            await e.characters.save(
                replace(stored, quests=stored.quests.take("farhold_pump_house"))
            )
    replacement, _ = begin(
        e.content,
        battle_id=e.session.id,
        attackers=[(one, True) for one in actors],
        enemies=(),
        seed=e.session.seed,
    )
    e.flow = replace(
        e.flow, descent=replace(e.flow.descent, credits=tuple((one.id, 3) for one in actors))
    )
    e.session = await e.store.save(
        replace(
            e.session,
            play_snapshot=e.flow.serialise(),
            state=replace(
                replacement.state,
                combatants=tuple(replace(one, actions=1) for one in replacement.state.combatants),
                gold=100,
                experience=100,
            ),
        )
    )
    deals = await e.guilds.current_contracts(
        e.content,
        guild_rules.standing(e.content, guild),
        world_seed=SETTINGS.world_seed,
        guild_id=guild.id,
        now=int(time.time()),
        seconds=SETTINGS.guild_contract_seconds,
    )
    expected_deeds = 1 + sum(
        one.reward_deeds
        for one in deals
        if one.kind in {ContractKind.CULL, ContractKind.DELVE} and one.target == 1
    )
    await press(e, "завершить", hook=accept)
    saved = await e.guilds.by_id(guild.id)
    assert saved.deeds == expected_deeds
    assert all(one.contributed == 1 for one in saved.members)
    progress = await e.guilds.contract_progress(
        guild.id, now=int(time.time()), rotation_seconds=SETTINGS.guild_contract_seconds
    )
    assert progress[ContractKind.CULL] == progress[ContractKind.DELVE] == 1
    for one in actors:
        assert (await e.characters.get(one.id)).quests.progress("farhold_pump_house") == 1
    await press(e, "повторить", hook=accept)
    assert (await e.guilds.by_id(guild.id)).deeds == expected_deeds
    assert (
        await e.guilds.contract_progress(
            guild.id, now=int(time.time()), rotation_seconds=SETTINGS.guild_contract_seconds
        )
        == progress
    )
