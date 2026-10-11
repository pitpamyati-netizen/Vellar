"""Вход в бой через собранное приложение и постоянное хранилище экранов."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram.types import User as TelegramUser
from pydantic import SecretStr
from scripts.test_stand import load_test_settings

from mmorpg import economy_log
from mmorpg.application.services.battle import BattleStore
from mmorpg.config import AppEnv
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.nodes import location_epoch
from mmorpg.main import build_application
from mmorpg.presentation.telegram.flows.play import build_location, render
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.play import NODE_ACTIONS
from mmorpg.presentation.telegram.states.screens import STATE_FOR_SCREEN, Play
from tests.presentation.test_command_journal import RecordingSession, update
from tests.test_main import _dependencies

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session", params=[AppEnv.SOLO, AppEnv.DEV])
async def combat_app(request, monkeypatch, pool):
    monkeypatch.setattr(economy_log, "_sink", economy_log._sink)
    account = 6_000_000 + uuid4().int % 1_000_000_000
    settings = load_test_settings().model_copy(
        update={"app_env": request.param, "bot_token": SecretStr(f"{account}:ABCDEF-test")}
    )
    app = await build_application(settings)
    recording = RecordingSession()
    monkeypatch.setattr(app.bot.session, "make_request", recording.make_request)
    dependencies = _dependencies(app)
    try:
        await dependencies.users.upsert(User(telegram_id=account))
        hero = await dependencies.characters.create(
            Character(
                id=0,
                user_id=account,
                name=f"Контроль{account}",
                race_id="human",
                class_id="warrior",
                city_id="farhold",
                level=10,
                gold=500,
            )
        )
        state = app.dispatcher.fsm.get_context(app.bot, account, account)
        yield app, dependencies, hero, state, recording
    finally:
        await app.stack.aclose()
        await app.bot.session.close()
        await pool.execute("DELETE FROM users WHERE telegram_id=$1", account)
        for table in ("message_delivery", "message_delivery_attempts", "message_delivery_budget"):
            await pool.execute(f"DELETE FROM {table} WHERE bot_id=$1", app.bot.id)


async def feed(app, recording, hero, text, number):
    event = update(app.bot, number=number, chat_id=hero.user_id, text=text)
    event = event.model_copy(
        update={
            "message": event.message.model_copy(
                update={
                    "from_user": TelegramUser(id=hero.user_id, is_bot=False, first_name="Игрок")
                }
            )
        }
    )
    before = len(recording.sent)
    await app.dispatcher.feed_update(app.bot, event)
    async with asyncio.timeout(5):
        while len(recording.sent) == before:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    return event, recording.sent[-1]


@pytest.mark.parametrize("entry", ["dungeon", "location"])
async def test_enter_battle_take_turn_and_replay_through_actual_application(combat_app, entry):
    app, dependencies, hero, state, recording = combat_app
    if entry == "dungeon":
        flow = PlayState(
            screen=ScreenId.DUNGEON_PICK,
            city_id="farhold",
            dungeon_pick="farhold_first_adit",
        )
        text = "Разведка"
    else:
        await dependencies.locations.reset("farhold", 1)
        world = await dependencies.locations.state("farhold", 1, now=int(time.time()))
        epoch = location_epoch(world)
        session = LocationSession("farhold", 1, epoch=epoch)
        location = build_location(app.content, app.settings.world_seed, session, epoch=epoch)
        node = next(one for one in location.nodes if one.kind.is_combat)
        flow = PlayState(screen=ScreenId.LOCATION, session=replace(session, node=node.index))
        screen = render(
            app.content, hero, flow, world_seed=app.settings.world_seed, location_state=world
        )
        text = next(
            button.text
            for row in screen.rows
            for button in row
            if button.text.startswith(NODE_ACTIONS[node.kind])
        )
    await state.set_state(STATE_FOR_SCREEN[flow.screen])
    await state.set_data({"play": flow.serialise()})

    event, reply = await feed(app, recording, hero, text, 1)
    assert "Бой. Круг" in reply.text
    assert await state.get_state() == Play.combat.state
    store = BattleStore(dependencies.state_cache)
    battle_id = (await state.get_data())["battle"]
    opened = await store.load(battle_id)
    assert opened is not None
    assert await store.busy(hero.id) == battle_id

    # Тот же вход не создаёт второй бой и не меняет его версию.
    await app.dispatcher.feed_update(app.bot, event)
    assert await store.load(battle_id) == opened

    for number, command in enumerate(("Атака", "Защититься"), start=2):
        event, reply = await feed(app, recording, hero, command, number)
        assert "Бой. Круг" in reply.text
        after = await store.load(battle_id)
        assert after.version == opened.version + 1
        assert after.state != opened.state
        await app.dispatcher.feed_update(app.bot, event)
        assert await store.load(battle_id) == after
        opened = after

    _, reply = await feed(app, recording, hero, "/пауза", 4)
    assert "пауза" in reply.text.lower()
    assert await store.load(battle_id) == opened
    _, reply = await feed(app, recording, hero, "/продолжить", 5)
    assert "Бой. Круг" in reply.text
    assert await store.load(battle_id) == opened


async def test_failure_after_opening_rolls_back_battle_and_screen_then_retry_succeeds(
    combat_app, monkeypatch
):
    from mmorpg.presentation.telegram.handlers import play
    from mmorpg.presentation.telegram.middlewares.errors import APOLOGY

    app, dependencies, hero, state, recording = combat_app
    flow = PlayState(
        screen=ScreenId.DUNGEON_PICK, city_id="farhold", dungeon_pick="farhold_first_adit"
    )
    await state.set_state(Play.dungeon_pick)
    await state.set_data({"play": flow.serialise()})
    before = await dependencies.characters.get(hero.id)
    original = play.open_fight

    async def fail_after_opening(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("controlled failure after opening battle")

    monkeypatch.setattr(play, "open_fight", fail_after_opening)
    _, reply = await feed(app, recording, hero, "Разведка", 1)
    assert reply.text == APOLOGY
    assert await state.get_state() == Play.dungeon_pick.state
    assert await state.get_data() == {"play": flow.serialise()}
    assert await dependencies.characters.get(hero.id) == before
    assert await BattleStore(dependencies.state_cache).busy(hero.id) is None
    assert not any("Бой. Круг" in one.text for one in recording.sent)

    monkeypatch.setattr(play, "open_fight", original)
    _, reply = await feed(app, recording, hero, "Разведка", 1)
    assert "Бой. Круг" in reply.text
    assert await state.get_state() == Play.combat.state
