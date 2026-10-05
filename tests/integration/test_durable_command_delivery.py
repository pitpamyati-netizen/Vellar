"""Сохранённая команда и ответ восстанавливаются вместе без новой выплаты."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import pytest_asyncio
from aiogram import Bot

from mmorpg.application.delivery import DeliveryPolicy, DeliveryStatus
from mmorpg.infrastructure.persistence.delivery import PostgresDeliveryQueue
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.delivery import DeliveryWorker, DurableSendMiddleware
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_command_journal import RecordingSession, dependencies, update

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def delivered_command(economy):  # noqa: F811
    e = economy
    e.session = RecordingSession()
    e.bot = Bot(f"{800_000 + e.heroes[0].id}:ABCDEF-test", session=e.session)
    e.queue = PostgresDeliveryQueue(e.pool)
    e.reaper = MessageReaper()
    e.policy = DeliveryPolicy(private_limit=100, group_limit=100)
    e.worker = DeliveryWorker(e.queue, e.bot, policy=e.policy, reaper=e.reaper)
    e.bot.session.middleware(DurableSendMiddleware(e.queue, e.worker))
    e.journal = CommandMiddleware(
        dependencies(e.characters), e.reaper, delivery=e.queue, wake_delivery=e.worker.wake
    )
    e.event = update(e.bot, number=e.heroes[0].id, chat_id=e.ids[0], text="передать 50 золота")
    e.key = f"command:telegram:{e.bot.id}:{e.ids[0]}:{e.heroes[0].id}"
    e.calls = 0

    async def action(event, data):
        e.calls += 1
        hero = await e.characters.get(e.heroes[0].id)
        await e.characters.save(replace(hero, gold=hero.gold + 50))
        await event.message.answer("Получено 50 золота.", parse_mode=None)
        # Ни SQL-эффект, ни ответ не видны внешнему отправителю до сохранения.
        assert not e.session.sent

    e.action = action
    try:
        yield e
    finally:
        await e.worker.aclose()
        await e.reaper.aclose()
        await e.bot.session.close()
        await e.pool.execute("DELETE FROM message_delivery WHERE bot_id=$1", e.bot.id)
        await e.pool.execute("DELETE FROM message_delivery_attempts WHERE bot_id=$1", e.bot.id)
        await e.pool.execute("DELETE FROM message_delivery_budget WHERE bot_id=$1", e.bot.id)


async def test_restart_delivers_saved_reply_without_another_update_or_reward(delivered_command):
    e = delivered_command
    e.session.fail = True
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert e.calls == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    assert await e.queue.pending_for_operation(e.key, e.bot.id, e.ids[0])
    # Повтор при ожидающем ответе не ставит вторую копию в очередь.
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert e.calls == 1
    assert (
        await e.pool.fetchval("SELECT count(*) FROM message_delivery WHERE bot_id=$1", e.bot.id)
        == 1
    )
    await e.redis.flushdb()
    restarted_session = RecordingSession()
    restarted_bot = Bot(e.bot.token, session=restarted_session)
    restarted_queue = PostgresDeliveryQueue(e.pool)
    restarted = DeliveryWorker(restarted_queue, restarted_bot, policy=e.policy)
    restarted.start()
    try:
        async with asyncio.timeout(5):
            # Состояние внешнего отправителя проверяем в SQL, общего Event у них нет.
            while await restarted_queue.pending_for_operation(e.key, e.bot.id, e.ids[0]):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        assert len(restarted_session.sent) == 1
        assert restarted_session.sent[0].text == "Получено 50 золота."
        assert restarted_session.sent[0].parse_mode is None
        assert (await e.characters.get(e.heroes[0].id)).gold == 550
        assert (
            await e.pool.fetchval("SELECT status FROM message_delivery WHERE bot_id=$1", e.bot.id)
            == DeliveryStatus.SENT
        )
    finally:
        await restarted.aclose()
        await restarted_bot.session.close()


async def test_failed_command_does_not_leave_a_reply_or_reward(delivered_command):
    e = delivered_command

    async def broken(event, data):
        await e.action(event, data)
        raise RuntimeError("before save")

    with pytest.raises(RuntimeError, match="before save"):
        await e.journal(broken, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert not e.session.sent
    assert (
        await e.pool.fetchval("SELECT count(*) FROM message_delivery WHERE bot_id=$1", e.bot.id)
        == 0
    )
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    assert len(e.session.sent) == 1


async def test_failed_announcement_enqueue_aborts_command_and_values(
    delivered_command, monkeypatch
):
    from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster, service

    e = delivered_command
    original = e.queue.enqueue

    async def broken_enqueue(**kwargs):
        await original(**kwargs)
        raise RuntimeError("after enqueue")

    monkeypatch.setattr(e.queue, "enqueue", broken_enqueue)
    broadcaster = ChannelBroadcaster(sink=e.bot, chat_id="@channel")

    async def action(event, data):
        hero = await e.characters.get(e.heroes[0].id)
        await e.characters.save(replace(hero, gold=hero.gold + 50))
        await broadcaster.announce(service("Открылась новая дорога."))

    with pytest.raises(RuntimeError, match="after enqueue"):
        await e.journal(action, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert not e.session.sent
    assert (
        await e.pool.fetchval("SELECT count(*) FROM message_delivery WHERE bot_id=$1", e.bot.id)
        == 0
    )
