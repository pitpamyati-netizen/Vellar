"""Команда, эффект, сохранённый ответ и быстрый отсев успешных повторов."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from mmorpg.application.operations import OperationCommittedError, OperationKeyConflictError
from mmorpg.domain.entities.character import Character
from mmorpg.infrastructure.cache import InMemoryIdempotencyStore
from mmorpg.infrastructure.persistence import InMemoryCharacterRepository
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from mmorpg.presentation.telegram.middlewares.errors import ErrorMiddleware
from mmorpg.presentation.telegram.middlewares.idempotency import IdempotencyMiddleware
from mmorpg.presentation.telegram.middlewares.operations import EconomicSendMiddleware


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.fail = False

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        if self.fail:
            raise TelegramNetworkError(method, "controlled outage")
        self.sent.append(method)
        return Message(
            message_id=len(self.sent),
            date=datetime.fromtimestamp(1, UTC),
            chat=Chat(id=method.chat_id, type="private"),
            text=method.text,
        )

    async def stream_content(self, *args, **kwargs):
        yield b""


def update(bot, *, number=10, text="Прибавить золото", chat_id=42, message_id=None):
    message = Message(
        message_id=number if message_id is None else message_id,
        date=datetime.fromtimestamp(1, UTC),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=User(id=42, is_bot=False, first_name="Игрок"),
        text=text,
    ).as_(bot)
    return Update(update_id=number, message=message).as_(bot)


def dependencies(characters, **other):
    return SimpleNamespace(
        characters=characters,
        **other,
        as_data=lambda: {"characters": characters, **other},
    )


@pytest.fixture
async def environment():
    characters = InMemoryCharacterRepository()
    hero = await characters.create(
        Character(id=0, user_id=42, name="Аргус", race_id="human", class_id="warrior", gold=500)
    )
    session = RecordingSession()
    bot = Bot("123456:ABCDEF-test", session=session)
    bot.session.middleware(EconomicSendMiddleware())
    reaper = MessageReaper()
    journal = CommandMiddleware(dependencies(characters), reaper)
    env = SimpleNamespace(
        characters=characters,
        hero=hero,
        session=session,
        bot=bot,
        reaper=reaper,
        journal=journal,
        calls=0,
    )

    async def action(event, data):
        env.calls += 1
        current = await characters.get(hero.id)
        await characters.save(replace(current, gold=current.gold + 50))
        await event.message.answer("Получено 50 золота.", parse_mode=None)
        assert not session.sent

    env.action = action
    try:
        yield env
    finally:
        await reaper.aclose()
        await bot.session.close()


async def test_replay_returns_saved_reply_without_running_handler(environment):
    e = environment
    event = update(e.bot)
    await e.journal(e.action, event, {"bot": e.bot})

    # Новый обработчик мог бы уже принять нажатие за другое действие.
    async def changed_route(*args):
        raise AssertionError("a replay must not reach the current router")

    await e.journal(changed_route, event, {"bot": e.bot})
    assert e.calls == 1
    assert (await e.characters.get(e.hero.id)).gold == 550
    assert len(e.session.sent) == 2
    replay = e.session.sent[-1]
    assert replay.text.startswith("Получено 50 золота.")
    assert "сохранённый ответ" in replay.text
    assert replay.reply_markup.keyboard[0][0].text == "Главное меню"
    assert replay.parse_mode is None


async def test_changed_message_under_same_number_is_rejected(environment):
    e = environment
    await e.journal(e.action, update(e.bot), {"bot": e.bot})
    with pytest.raises(OperationKeyConflictError):
        await e.journal(e.action, update(e.bot, text="Другое действие"), {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 550


async def test_failed_command_can_retry_immediately_through_fast_filter(environment):
    e = environment
    store = InMemoryIdempotencyStore()
    fast = IdempotencyMiddleware(store, completed_only=True)
    event = update(e.bot)

    async def failing(event, data):
        await e.action(event, data)
        raise RuntimeError("controlled failure before commit")

    async def failed_journal(event, data):
        return await e.journal(failing, event, data)

    with pytest.raises(RuntimeError):
        await fast(failed_journal, event, {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 500
    assert not e.session.sent

    async def successful_journal(event, data):
        return await e.journal(e.action, event, data)

    await fast(successful_journal, event, {"bot": e.bot})
    await fast(successful_journal, event, {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 550
    assert e.calls == 2
    assert len(e.session.sent) == 1


async def test_rollback_happens_before_error_answer(environment):
    e = environment

    async def failing(event, data):
        await e.action(event, data)
        raise RuntimeError("controlled failure")

    async def route(event, data):
        return await e.journal(failing, event, data)

    await ErrorMiddleware()(route, update(e.bot), {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 500
    assert len(e.session.sent) == 1
    assert "действие не выполнено" in e.session.sent[0].text
    assert not e.characters.operations.completed


async def test_lost_answer_replays_saved_result_without_second_effect(environment):
    e = environment
    e.session.fail = True
    event = update(e.bot)
    with pytest.raises(OperationCommittedError):
        await e.journal(e.action, event, {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 550
    e.session.fail = False
    await e.journal(e.action, event, {"bot": e.bot})
    assert e.calls == 1
    assert len(e.session.sent) == 1


async def test_expired_fast_filter_uses_journal(environment):
    e = environment
    now = 0
    store = InMemoryIdempotencyStore(clock=lambda: now)
    fast = IdempotencyMiddleware(store, completed_only=True)
    event = update(e.bot)

    async def route(event, data):
        return await e.journal(e.action, event, data)

    await fast(route, event, {"bot": e.bot})
    now = 301
    await fast(route, event, {"bot": e.bot})
    assert e.calls == 1
    assert len(e.session.sent) == 2


async def test_concurrent_replays_only_apply_once(environment):
    e = environment
    event = update(e.bot)
    await asyncio.gather(*[e.journal(e.action, event, {"bot": e.bot}) for _ in range(2)])
    assert e.calls == 1
    assert (await e.characters.get(e.hero.id)).gold == 550


async def test_group_reply_uses_real_message_id_for_cleanup(environment):
    from mmorpg.presentation.telegram.messaging import send_group_reply
    from mmorpg.presentation.telegram.screens.group import GroupReply

    e = environment
    cleaned = []

    async def action(event, data):
        await send_group_reply(
            e.bot,
            chat_id=event.message.chat.id,
            reply=GroupReply(text="Сделка завершена."),
            answering=10,
            on_sent=cleaned.append,
        )
        assert not cleaned

    await e.journal(action, update(e.bot, chat_id=-42, text="передать 50 золота"), {"bot": e.bot})
    assert cleaned == [1]


async def test_unavailable_fast_cache_does_not_stop_journal(environment):
    e = environment

    class BrokenCache:
        async def completed(self, key):
            raise ConnectionError("controlled cache outage")

        async def remember(self, key, ttl):
            raise ConnectionError("controlled cache outage")

    fast = IdempotencyMiddleware(BrokenCache(), completed_only=True)

    async def route(event, data):
        return await e.journal(e.action, event, data)

    await fast(route, update(e.bot), {"bot": e.bot})
    assert (await e.characters.get(e.hero.id)).gold == 550


async def test_replay_does_not_resend_other_players_notices(environment):
    e = environment

    async def action(event, data):
        await event.message.answer("Готово.", parse_mode=None)
        await e.bot(SendMessage(chat_id=43, text="Чужое уведомление.", parse_mode=None))

    event = update(e.bot)
    await e.journal(action, event, {"bot": e.bot})
    await e.journal(action, event, {"bot": e.bot})
    assert [one.chat_id for one in e.session.sent] == [42, 43, 42]


async def test_ordinary_group_chat_is_not_stored(environment):
    e = environment

    async def ignored(*args):
        return None

    await e.journal(ignored, update(e.bot, chat_id=-42, text="Привет всем"), {"bot": e.bot})
    assert not e.characters.operations.completed
    assert not e.session.sent


async def test_failed_replay_still_reports_a_saved_action(environment):
    e = environment
    event = update(e.bot)
    await e.journal(e.action, event, {"bot": e.bot})
    e.session.fail = True
    with pytest.raises(OperationCommittedError):
        await e.journal(e.action, event, {"bot": e.bot})
    assert e.calls == 1


async def test_rollback_restores_keeper_content(environment, content):
    from mmorpg.application.services.content import ContentRegistry
    from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
    from mmorpg.infrastructure.persistence import InMemoryContentOverlayRepository

    e = environment
    overlays = InMemoryContentOverlayRepository()
    registry = ContentRegistry(content)
    journal = CommandMiddleware(dependencies(e.characters, overlays=overlays), e.reaper)
    edit = OverlayRecord(
        kind=OverlayKind.NPC,
        entity_id="m02_npc",
        fields={"name": "Довен", "city": "farhold", "role": "писарь заставы"},
    )

    async def failing(*args):
        await overlays.put(edit)
        await registry.reload(overlays)
        assert registry.current.has_npc(edit.entity_id)

        async def outside():
            assert not registry.current.has_npc(edit.entity_id)

        await asyncio.create_task(outside())
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError):
        await journal(failing, update(e.bot), {"bot": e.bot})
    assert registry.current is content
    assert registry.records == ()
    assert await overlays.all() == ()


async def test_dispatcher_replay_is_resolved_before_reading_fsm(monkeypatch):
    from mmorpg.main import build_application
    from tests.test_main import local_settings

    app = await build_application(local_settings())
    old_session = app.bot.session
    session = RecordingSession()
    app.bot.session = session
    session.middleware(EconomicSendMiddleware())
    event = update(app.bot, text="/start")
    try:
        await app.dispatcher.feed_update(app.bot, event)
        assert len(session.sent) == 2
        for middleware in app.dispatcher.update.outer_middleware:
            if isinstance(middleware, IdempotencyMiddleware):
                middleware._store._completed.clear()

        async def stale_route(*args, **kwargs):
            raise AssertionError("a replay must be resolved before reading FSM")

        monkeypatch.setattr(app.dispatcher.storage, "get_state", stale_route)
        await app.dispatcher.feed_update(app.bot, event)
        assert len(session.sent) == 4
        assert all("сохранённый ответ" in one.text for one in session.sent[2:])
    finally:
        await old_session.close()
        await session.close()
        await app.stack.aclose()


async def test_full_size_saved_reply_is_not_truncated_or_overfilled(environment):
    e = environment
    text = "я" * 4096

    async def action(event, data):
        await event.message.answer(text, parse_mode=None)

    event = update(e.bot)
    await e.journal(action, event, {"bot": e.bot})
    await e.journal(action, event, {"bot": e.bot})
    assert e.session.sent[-1].text == text
    assert e.session.sent[-1].reply_markup.keyboard[0][0].text == "Главное меню"
