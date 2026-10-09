"""M08.5: личные доли PvP и связанный журнал настоящего PostgreSQL."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from mmorpg.application.services.battle import BattleKind, BattleStore, begin
from mmorpg.application.services.guild import GuildStore
from mmorpg.config import Settings
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
from mmorpg.infrastructure.persistence.postgres import PostgresGuildRepository
from mmorpg.infrastructure.persistence.world import PostgresLocationState
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.handlers import combat
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from mmorpg.presentation.telegram.middlewares.operations import EconomicSendMiddleware
from mmorpg.presentation.telegram.states.screens import Play
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_command_journal import RecordingSession, dependencies

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
SETTINGS = Settings(_env_file=None)


@pytest_asyncio.fixture(loop_scope="session")
async def duel_env(economy, content, request):  # noqa: F811
    e = economy
    e.content = content
    e.heroes = [
        await e.characters.save(replace(hero, gold=gold, bank_gold=900))
        for hero, gold in zip(e.heroes, (100, 200, request.param), strict=True)
    ]
    e.cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, RedisStateCache(e.redis)))
    e.world = PostgresLocationState(e.pool)
    e.storage = PostgresStorage(e.pool)
    e.store = BattleStore(e.cache)
    e.guilds = GuildStore(PostgresGuildRepository(e.pool), e.cache)
    e.recording = RecordingSession()
    e.bot = Bot("765432:ABCDEF-test", session=e.recording)
    e.bot.session.middleware(EconomicSendMiddleware())
    e.reaper = MessageReaper()
    e.journal = CommandMiddleware(
        dependencies(
            e.characters,
            cache=e.cache,
            world=e.world,
            guilds=e.guilds,
            inventory=e.inventory,
        ),
        e.reaper,
    )
    e.flow = PlayState()
    e.session, _ = begin(
        content,
        battle_id=f"m08-duel:{uuid4()}",
        attackers=[(one, True) for one in e.heroes[:2]],
        defenders=[(e.heroes[2], True)],
        owner=e.heroes[0].id,
        kind=BattleKind.DUEL,
        seed=b"m08-duel",
    )
    e.session = replace(
        e.session, state=replace(e.session.state, outcome=BattleOutcome.DECIDED, winner=0)
    )
    e.session = await e.store.save(e.session)
    for hero in e.heroes:
        state = state_for(e, hero)
        await state.set_state(Play.combat)
        await state.update_data({"battle": e.session.id, "play": e.flow.serialise()})
    try:
        yield e
    finally:
        await e.reaper.aclose()
        await e.bot.session.close()
        await e.pool.execute(
            "DELETE FROM gameplay_state WHERE key=$1 OR key LIKE 'session:%'",
            e.store.key_of(e.session.id),
        )
        for hero in e.heroes:
            await e.pool.execute(
                "DELETE FROM gameplay_state WHERE key=$1", e.store.key_for_character(hero.id)
            )


def state_for(e, hero):
    return FSMContext(
        storage=e.storage,
        key=StorageKey(bot_id=e.bot.id, chat_id=hero.user_id, user_id=hero.user_id),
    )


async def finish(e, *, number=101, fault_hook=None):
    hero = e.heroes[0]
    number += hero.id * 1000
    message = Message(
        message_id=number,
        date=datetime.fromtimestamp(1, UTC),
        chat=Chat(id=hero.user_id, type="private"),
        from_user=User(id=hero.user_id, is_bot=False, first_name=hero.name),
        text="Завершить поединок",
    ).as_(e.bot)
    event = Update(update_id=number, message=message).as_(e.bot)

    async def handle(event, data):
        await combat._finish(
            event.message,
            state_for(e, hero),
            e.content,
            SETTINGS,
            e.session,
            {},
            hero,
            e.flow,
            e.characters,
            e.inventory,
            e.world,
            e.cache,
            e.guilds,
            fault_hook=fault_hook,
        )

    await e.journal(handle, event, {"bot": e.bot})


async def recorded_deltas(e):
    rows = await e.pool.fetch(
        "SELECT character_id, amount, operation_id FROM gold_flow"
        " WHERE character_id=ANY($1::bigint[]) AND flow='duel' ORDER BY character_id",
        [one.id for one in e.heroes],
    )
    entries = await e.pool.fetch(
        "SELECT owner_id, amount, operation_id FROM economic_entries"
        " WHERE operation_id=ANY($1::text[]) AND resource='gold' AND container='gold'"
        " ORDER BY owner_id",
        list({one["operation_id"] for one in rows}),
    )
    return rows, entries


@pytest.mark.parametrize("duel_env,shares", [(110, (6, 5)), (9, (0, 0))], indirect=["duel_env"])
async def test_wallets_saved_screens_and_both_journals_agree_and_repeat_once(duel_env, shares):
    e = duel_env
    await finish(e)
    saved = await e.store.load(e.session.id)
    assert saved.settled
    results = json.loads(saved.results)
    for hero, share in zip(e.heroes[:2], shares, strict=True):
        assert (await e.characters.get(hero.id)).gold == hero.gold + share
        text = "\n".join(results[str(hero.id)]["lines"])
        assert f"Золото: {share}." in text and f"Ваша доля: {share}." in text
        delivered = [one.text for one in e.recording.sent if one.chat_id == hero.user_id]
        assert delivered and f"Ваша доля: {share}." in delivered[-1]
    rows, entries = await recorded_deltas(e)
    expected = [*shares, -sum(shares)] if any(shares) else []
    assert [one["amount"] for one in rows] == expected
    assert [(one["character_id"], one["amount"], one["operation_id"]) for one in rows] == [
        (one["owner_id"], one["amount"], one["operation_id"]) for one in entries
    ]
    assert sum(one["amount"] for one in rows) == 0
    loser = await e.characters.get(e.heroes[2].id)
    assert loser.gold == e.heroes[2].gold - sum(shares)
    assert all([(await e.characters.get(hero.id)).bank_gold == 900 for hero in e.heroes])

    # Повтор той же команды и новая команда для уже законченного боя не платят снова.
    before = [(await e.characters.get(hero.id)).gold for hero in e.heroes]
    await finish(e)
    await finish(e, number=102)
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes] == before
    assert await recorded_deltas(e) == (rows, entries)
    assert f"Ваша доля: {shares[0]}." in e.recording.sent[-1].text


@pytest.mark.parametrize("duel_env", [110], indirect=True)
async def test_failed_settlement_rolls_back_personal_shares_and_retries_with_same_command(duel_env):
    e = duel_env

    async def fail_after_character(point):
        if point == "after_character":
            raise RuntimeError("controlled failure before commit")

    with pytest.raises(RuntimeError, match="controlled failure"):
        await finish(e, fault_hook=fail_after_character)
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes] == [100, 200, 110]
    assert await recorded_deltas(e) == ([], [])
    assert not (await e.store.load(e.session.id)).settled
    assert not e.recording.sent
    await finish(e)
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes] == [106, 205, 99]
    rows, entries = await recorded_deltas(e)
    assert [one["amount"] for one in rows] == [6, 5, -11]
    assert [one["amount"] for one in entries] == [6, 5, -11]
