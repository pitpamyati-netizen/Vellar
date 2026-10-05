"""Отправка после записи, возобновление, 429 и потерянный ответ Telegram."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetMe, SendMessage
from aiogram.types import Chat, KeyboardButton, Message, ReplyKeyboardMarkup

from mmorpg.application.delivery import DeliveryFailedError, DeliveryPolicy, DeliveryStatus
from mmorpg.infrastructure.persistence import InMemoryCharacterRepository
from mmorpg.infrastructure.persistence.memory_delivery import MemoryDeliveryQueue
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster, news
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.delivery import (
    DeliveryPendingError,
    DeliveryWorker,
    DurableSendMiddleware,
)
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from tests.presentation.test_command_journal import RecordingSession, dependencies, update


class QueueSession(RecordingSession):
    def __init__(self):
        super().__init__()
        self.errors = []
        self.received = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        self.received.append(method)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if self.errors:
                raise self.errors.pop(0)
            if self.fail:
                raise TelegramNetworkError(method, "controlled outage")
            self.sent.append(method)
            return Message(
                message_id=len(self.sent),
                date=datetime.fromtimestamp(1, UTC),
                chat=Chat(
                    id=method.chat_id if isinstance(method.chat_id, int) else -42,
                    type="private" if isinstance(method.chat_id, int) else "channel",
                ),
                text=method.text,
            )
        finally:
            self.in_flight -= 1


@pytest.fixture
async def delivery_env():
    clock = SimpleNamespace(now=1000.0)
    queue = MemoryDeliveryQueue(lambda: clock.now)
    session = QueueSession()
    bot = Bot("456789:ABCDEF-test", session=session)
    worker = DeliveryWorker(queue, bot)
    bot.session.middleware(DurableSendMiddleware(queue, worker))
    try:
        yield SimpleNamespace(queue=queue, session=session, bot=bot, worker=worker, clock=clock)
    finally:
        await worker.aclose()
        await session.close()


async def enqueue(e, key, chat=42):
    return await e.queue.enqueue(
        key=key,
        bot_id=e.bot.id,
        chat_id=chat,
        payload={"chat_id": chat, "text": key, "parse_mode": None},
    )


async def test_direct_send_uses_queue_and_returns_real_message_number(delivery_env):
    e = delivery_env
    sent = await e.bot.send_message(42, "Открыта сумка.", parse_mode=None)
    assert sent.message_id == 1
    assert len(e.session.received) == 1
    row = next(iter(e.queue._rows.values()))
    assert row.status == DeliveryStatus.SENT
    assert row.response["message_id"] == sent.message_id
    assert row.payload["parse_mode"] is None


async def test_operation_enqueues_atomically_then_sends_after_commit(delivery_env):
    from mmorpg.domain.entities.character import Character

    e = delivery_env
    characters = InMemoryCharacterRepository()
    hero = await characters.create(
        Character(id=0, user_id=42, name="Аргус", race_id="human", class_id="warrior", gold=500)
    )
    reaper = MessageReaper()
    journal = CommandMiddleware(dependencies(characters), reaper)
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Главное меню")]])

    async def action(event, data):
        await characters.save(replace(hero, gold=550))
        sent = await event.message.answer("Получено 50 золота.", reply_markup=keyboard)
        assert sent.message_id < 0
        assert not e.session.received
        assert not e.queue._rows

    try:
        await journal(action, update(e.bot), {"bot": e.bot})
        row = next(iter(e.queue._rows.values()))
        assert row.operation_id.startswith("command:telegram:")
        assert row.status == DeliveryStatus.SENT
        assert row.payload["reply_markup"]["keyboard"][0][0]["text"] == "Главное меню"
        assert (await characters.get(hero.id)).gold == 550
    finally:
        await reaper.aclose()


async def test_restart_delivers_primary_and_secondary_without_new_action(delivery_env):
    e = delivery_env
    characters = InMemoryCharacterRepository()
    reaper = MessageReaper()
    journal = CommandMiddleware(dependencies(characters), reaper)
    calls = 0
    e.session.errors = [TelegramNetworkError(SendMessage(chat_id=42, text="x"), "outage")]

    async def action(event, data):
        nonlocal calls
        calls += 1
        await event.message.answer("Ваше действие сохранено.")
        await e.bot.send_message(43, "Другой игрок завершил обмен.")

    try:
        event = update(e.bot)
        await journal(action, event, {"bot": e.bot})
        rows = list(e.queue._rows.values())
        assert rows[0].status == DeliveryStatus.QUEUED
        assert rows[1].status == DeliveryStatus.SENT
        restarted = DeliveryWorker(e.queue, e.bot)
        e.clock.now += 2
        assert await restarted.run_once()
        assert not await restarted.run_once()
        await journal(action, event, {"bot": e.bot})
        assert calls == 1
        assert len([method for method in e.session.sent if method.chat_id == 43]) == 1
        assert len([method for method in e.session.sent if method.chat_id == 42]) == 2
    finally:
        await reaper.aclose()


async def test_429_is_durable_and_cools_other_worker_and_other_chat(delivery_env):
    e = delivery_env
    await enqueue(e, "first")
    await enqueue(e, "other-chat", 43)
    e.session.errors = [TelegramRetryAfter(SendMessage(chat_id=42, text="x"), "flood", 300)]
    assert await e.worker.run_once()
    other = DeliveryWorker(e.queue, e.bot)
    assert not await other.run_once()
    assert (await e.queue.get("first")).last_error == "TelegramRetryAfter"
    e.clock.now += 300
    assert await other.run_once()
    assert e.session.sent[0].text == "first"


async def test_all_network_attempts_consume_shared_budget(delivery_env):
    e = delivery_env
    e.worker.policy = DeliveryPolicy(sends_per_second=1)
    await enqueue(e, "first")
    await enqueue(e, "other-chat", 43)
    e.session.errors = [TelegramNetworkError(SendMessage(chat_id=42, text="x"), "outage")]
    assert await e.worker.run_once()
    assert not await e.worker.run_once()
    assert len(e.session.received) == 1
    e.clock.now += 1
    assert await e.worker.run_once()
    assert len(e.session.received) == 2


async def test_lost_telegram_response_can_duplicate_text_but_keeps_next_in_order(delivery_env):
    e = delivery_env
    await enqueue(e, "first")
    await enqueue(e, "second")
    actual = []
    original = e.session.make_request

    async def lost_response(bot, method, timeout=None):  # noqa: ASYNC109
        actual.append(method.text)
        result = await original(bot, method, timeout)
        if len(actual) == 1:
            raise TelegramNetworkError(method, "response lost after acceptance")
        return result

    e.session.make_request = lost_response
    assert await e.worker.run_once()
    assert not await e.worker.run_once()
    e.clock.now += 1
    assert await e.worker.run_once()
    assert await e.worker.run_once()
    assert actual == ["first", "first", "second"]
    assert (await e.queue.get("first")).attempts == 2


async def test_permanent_refusal_keeps_failure_and_allows_following_message(delivery_env):
    e = delivery_env
    await enqueue(e, "first")
    await enqueue(e, "second")
    e.session.errors = [TelegramForbiddenError(SendMessage(chat_id=42, text="x"), "blocked")]
    assert await e.worker.run_once()
    with pytest.raises(DeliveryFailedError):
        await e.worker.deliver("first")
    assert await e.worker.run_once()
    assert (await e.queue.get("first")).status == DeliveryStatus.FAILED
    assert e.session.sent[0].text == "second"


async def test_direct_send_network_failure_keeps_queue(delivery_env):
    e = delivery_env
    e.session.errors = [TelegramNetworkError(SendMessage(chat_id=42, text="x"), "outage")]
    with pytest.raises(DeliveryPendingError):
        await e.bot.send_message(42, "Ответ сохранён.")
    row = next(iter(e.queue._rows.values()))
    assert row.status == DeliveryStatus.QUEUED
    e.clock.now += 1
    assert await e.worker.run_once()
    assert e.session.sent[0].text == "Ответ сохранён."


async def test_worker_restarts_pending_message_after_cancellation(delivery_env):
    e = delivery_env
    await enqueue(e, "first")
    entered = asyncio.Event()

    async def stuck(bot, method, timeout=None):  # noqa: ASYNC109
        entered.set()
        await asyncio.Event().wait()

    original = e.session.make_request
    e.session.make_request = stuck
    task = asyncio.create_task(e.worker.run_once())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await e.queue.get("first")).status == DeliveryStatus.SENDING
    e.session.make_request = original
    e.clock.now += 91
    assert await DeliveryWorker(e.queue, e.bot).run_once()
    assert (await e.queue.get("first")).status == DeliveryStatus.SENT


async def test_announcements_are_enqueued_before_any_api_call(delivery_env):
    e = delivery_env
    broadcaster = ChannelBroadcaster(sink=e.bot, chat_id="@channel")
    assert await broadcaster.announce(news("Открылась новая дорога."))
    assert not e.session.received
    await enqueue(e, "reply")
    assert await e.worker.run_once()
    assert e.session.sent[0].text == "reply"
    assert await e.worker.run_once()
    assert e.session.sent[1].chat_id == "@channel"


async def test_non_sending_request_bypasses_queue(delivery_env):
    e = delivery_env
    called = []

    async def request(bot, method):
        called.append(method)
        return "ok"

    assert await DurableSendMiddleware(e.queue, e.worker)(request, e.bot, GetMe()) == "ok"
    assert len(called) == 1
    assert not e.queue._rows


async def test_background_worker_delivers_and_stops(delivery_env):
    e = delivery_env
    await enqueue(e, "first")
    e.worker.start()
    e.worker.start()
    for _ in range(20):
        if (await e.queue.get("first")).status == DeliveryStatus.SENT:
            break
        await asyncio.sleep(0)
    assert (await e.queue.get("first")).status == DeliveryStatus.SENT
    await e.worker.aclose()
    assert e.worker._task is None


async def test_short_caller_wait_does_not_cancel_in_progress_telegram_send(delivery_env):
    e = delivery_env
    row = await enqueue(e, "slow")
    entered = asyncio.Event()
    release = asyncio.Event()
    original = e.session.make_request

    async def slow(bot, method, timeout=None):  # noqa: ASYNC109
        entered.set()
        await release.wait()
        return await original(bot, method, timeout)

    e.session.make_request = slow
    with pytest.raises(TimeoutError):
        await e.worker.deliver(row.key, wait_seconds=0.01)
    assert entered.is_set()
    assert (await e.queue.get(row.key)).status == DeliveryStatus.SENDING
    release.set()
    for _ in range(30):
        if (await e.queue.get(row.key)).status == DeliveryStatus.SENT:
            break
        await asyncio.sleep(0)
    assert (await e.queue.get(row.key)).status == DeliveryStatus.SENT
    assert len(e.session.sent) == 1


async def test_http_timeout_preserves_retry_without_waiting_forever(delivery_env):
    e = delivery_env
    row = await enqueue(e, "slow")
    worker = DeliveryWorker(e.queue, e.bot, send_timeout=0.01)

    async def stuck(bot, method, timeout=None):  # noqa: ASYNC109
        await asyncio.Event().wait()

    e.session.make_request = stuck
    assert await worker.run_once()
    assert (await e.queue.get(row.key)).status == DeliveryStatus.QUEUED
    assert (await e.queue.get(row.key)).last_error == "TimeoutError"
    assert await worker.deliver(row.key) is None


async def test_long_429_direct_reply_returns_with_saved_queue(delivery_env):
    e = delivery_env
    e.session.errors = [TelegramRetryAfter(SendMessage(chat_id=42, text="x"), "flood", 300)]
    with pytest.raises(DeliveryPendingError):
        await e.bot.send_message(42, "Ответ сохранён.")
    assert len(e.session.received) == 1
    row = next(iter(e.queue._rows.values()))
    assert row.status == DeliveryStatus.QUEUED
    assert row.last_error == "TelegramRetryAfter"
    assert not await e.worker.run_once()
