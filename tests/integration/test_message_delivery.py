"""Та же очередь на PostgreSQL: реальные блокировки, отмена и возобновление."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from pydantic import TypeAdapter

from mmorpg.application.delivery import (
    DeliveryKeyConflictError,
    DeliveryPolicy,
    DeliveryPriority,
    DeliveryStatus,
)
from mmorpg.application.operations import Operation
from mmorpg.infrastructure.persistence.delivery import PostgresDeliveryQueue
from mmorpg.infrastructure.persistence.operations import OperationPool, PostgresOperations
from mmorpg.presentation.telegram.delivery import DeliveryWorker
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_delivery import QueueSession

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def queue_env(pool):
    bot_id = 100_000_000 + uuid4().int % 800_000_000
    queue = PostgresDeliveryQueue(pool)
    session = QueueSession()
    bot = Bot(f"{bot_id}:ABCDEF-test", session=session)
    worker = DeliveryWorker(queue, bot)
    env = SimpleNamespace(queue=queue, pool=pool, bot=bot, worker=worker, session=session)
    try:
        yield env
    finally:
        await worker.aclose()
        await session.close()
        await pool.execute("DELETE FROM message_delivery WHERE bot_id=$1", bot_id)
        await pool.execute("DELETE FROM message_delivery_attempts WHERE bot_id=$1", bot_id)
        await pool.execute("DELETE FROM message_delivery_budget WHERE bot_id=$1", bot_id)


async def enqueue(e, key, chat=42, priority=DeliveryPriority.REPLY, queue=None):
    return await (queue or e.queue).enqueue(
        key=f"{e.bot.id}:{key}",
        bot_id=e.bot.id,
        chat_id=chat,
        payload={"chat_id": chat, "text": key, "parse_mode": None},
        priority=priority,
    )


async def test_order_and_priority_on_real_postgres(queue_env):
    e = queue_env
    await enqueue(e, "announcement", 42, DeliveryPriority.ANNOUNCEMENT)
    await enqueue(e, "reply-same", 42)
    await enqueue(e, "reply-other", 43)
    first = await e.queue.claim(e.bot.id, DeliveryPolicy())
    assert first.payload["text"] == "reply-other"
    second = await e.queue.claim(e.bot.id, DeliveryPolicy())
    assert second.payload["text"] == "announcement"
    assert await e.queue.claim(e.bot.id, DeliveryPolicy()) is None
    await e.queue.complete(second.key, second.lease_token, {"message_id": 1})
    assert (await e.queue.claim(e.bot.id, DeliveryPolicy())).payload["text"] == "reply-same"


async def test_concurrent_workers_cannot_claim_same_chat_twice(queue_env):
    e = queue_env
    for key in range(10):
        await enqueue(e, str(key))
    adapters = [PostgresDeliveryQueue(e.pool) for _ in range(8)]
    rows = await asyncio.gather(*(q.claim(e.bot.id, DeliveryPolicy()) for q in adapters))
    claimed = [row for row in rows if row is not None]
    assert len(claimed) == 1
    assert claimed[0].payload["text"] == "0"


async def test_shared_bot_budget_limits_other_workers_and_priorities(queue_env):
    e = queue_env
    policy = DeliveryPolicy(sends_per_second=2)
    for key in range(8):
        await enqueue(
            e,
            str(key),
            42 + key,
            DeliveryPriority.REPLY if key % 2 else DeliveryPriority.ANNOUNCEMENT,
        )
    rows = await asyncio.gather(
        *(PostgresDeliveryQueue(e.pool).claim(e.bot.id, policy) for _ in range(8))
    )
    assert len([row for row in rows if row is not None]) == 2
    restarted = PostgresDeliveryQueue(e.pool)
    assert await restarted.claim(e.bot.id, policy) is None
    await e.pool.execute("UPDATE message_delivery_attempts SET at=at-2 WHERE bot_id=$1", e.bot.id)
    assert await restarted.claim(e.bot.id, policy) is not None


@pytest.mark.parametrize("chat,limit,window", [(42, 3, 3.0), (-42, 20, 60.0)])
async def test_shared_chat_budget_survives_new_adapter(queue_env, chat, limit, window):
    e = queue_env
    for key in range(limit + 1):
        await enqueue(e, str(key), chat)
    for _ in range(limit):
        row = await e.queue.claim(e.bot.id, DeliveryPolicy())
        assert row is not None
        await e.queue.complete(row.key, row.lease_token, {"message_id": 1})
    restarted = PostgresDeliveryQueue(e.pool)
    assert await restarted.claim(e.bot.id, DeliveryPolicy()) is None
    await e.pool.execute(
        "UPDATE message_delivery_attempts SET at=at-$2 WHERE bot_id=$1", e.bot.id, window
    )
    assert (await restarted.claim(e.bot.id, DeliveryPolicy())).payload["text"] == str(limit)


async def test_new_worker_reclaims_expired_lease_and_old_owner_cannot_ack(queue_env):
    e = queue_env
    await enqueue(e, "first")
    await enqueue(e, "second")
    first = await e.queue.claim(e.bot.id, DeliveryPolicy())
    await e.pool.execute("UPDATE message_delivery SET lease_until=0 WHERE key=$1", first.key)
    restarted = PostgresDeliveryQueue(e.pool)
    retry = await restarted.claim(e.bot.id, DeliveryPolicy())
    assert retry.key == first.key and retry.lease_token != first.lease_token
    assert not await e.queue.complete(first.key, first.lease_token, {"message_id": 1})
    assert not await e.queue.retry(first.key, first.lease_token, delay=300, error="stale")
    assert not await e.queue.fail(first.key, first.lease_token, error="stale")
    assert await restarted.complete(retry.key, retry.lease_token, {"message_id": 2})
    assert (await restarted.claim(e.bot.id, DeliveryPolicy())).payload["text"] == "second"


async def test_429_cooldown_is_shared_and_persists_after_restart(queue_env):
    e = queue_env
    first = await enqueue(e, "first")
    await enqueue(e, "other", 43)
    e.session.errors = [TelegramRetryAfter(SendMessage(chat_id=42, text="x"), "flood", 300)]
    assert await e.worker.run_once()
    restarted = DeliveryWorker(PostgresDeliveryQueue(e.pool), e.bot)
    assert not await restarted.run_once()
    row = await e.queue.get(first.key)
    assert row.status == DeliveryStatus.QUEUED
    assert row.last_error == "TelegramRetryAfter"
    await e.pool.execute(
        "UPDATE message_delivery_budget SET cooldown_until=0 WHERE bot_id=$1", e.bot.id
    )
    await e.pool.execute("UPDATE message_delivery SET available_at=0 WHERE bot_id=$1", e.bot.id)
    assert await restarted.run_once()
    assert e.session.sent[0].text == "first"


async def test_concurrent_duplicate_enqueue_is_one_row_and_conflict_is_rejected(queue_env):
    e = queue_env
    rows = await asyncio.gather(
        *(enqueue(e, "first", queue=PostgresDeliveryQueue(e.pool)) for _ in range(6))
    )
    assert len({row.sequence for row in rows}) == 1
    assert (
        await e.pool.fetchval("SELECT count(*) FROM message_delivery WHERE bot_id=$1", e.bot.id)
        == 1
    )
    with pytest.raises(DeliveryKeyConflictError):
        await enqueue(e, "first", 43)


async def test_enqueue_rolls_back_and_commits_with_real_gold_action(queue_env, economy):  # noqa: F811
    e = queue_env
    game = economy
    operation_id = f"delivery-test:{uuid4()}"

    async def action():
        await game.characters.save(replace(game.heroes[0], gold=550))
        await e.queue.enqueue(
            key=f"{e.bot.id}:atomic",
            bot_id=e.bot.id,
            chat_id=42,
            payload={"chat_id": 42, "text": "Получено 50 золота."},
            operation_id=operation_id,
        )
        assert (await e.queue.get(f"{e.bot.id}:atomic")).operation_id == operation_id
        # Другое соединение и новый worker не видят незавершённый enqueue.
        assert (
            await game.pool.fetchval(
                "SELECT count(*) FROM message_delivery WHERE bot_id=$1", e.bot.id
            )
            == 0
        )

    async def failing():
        await action()
        raise RuntimeError("rollback after enqueue")

    codec = TypeAdapter(type(None))
    try:
        with pytest.raises(RuntimeError):
            await game.characters.operations.run(
                failing,
                operation=Operation(id=operation_id, kind="test", participants=(game.characters,)),
                fingerprint="test",
                codec=codec,
                participants=(game.characters,),
            )
        assert await e.queue.get(f"{e.bot.id}:atomic") is None
        assert (await game.characters.get(game.heroes[0].id)).gold == 500
        await game.characters.operations.run(
            action,
            operation=Operation(id=operation_id, kind="test", participants=(game.characters,)),
            fingerprint="test",
            codec=codec,
            participants=(game.characters,),
        )
        assert (await game.characters.get(game.heroes[0].id)).gold == 550
        assert await e.queue.pending_for_operation(operation_id, e.bot.id, 42)
        assert await DeliveryWorker(PostgresDeliveryQueue(game.pool), e.bot).run_once()
        assert not await e.queue.pending_for_operation(operation_id, e.bot.id, 42)
        assert e.session.sent[0].text == "Получено 50 золота."
    finally:
        await e.pool.execute("DELETE FROM message_delivery WHERE bot_id=$1", e.bot.id)
        await game.pool.execute("DELETE FROM economic_entries WHERE operation_id=$1", operation_id)
        await game.pool.execute("DELETE FROM economic_operations WHERE id=$1", operation_id)


async def test_lost_send_response_may_duplicate_but_next_message_waits(queue_env):
    e = queue_env
    first = await enqueue(e, "first")
    await enqueue(e, "second")
    accepted = []
    original = e.session.make_request

    async def loss(bot, method, timeout=None):  # noqa: ASYNC109
        accepted.append(method.text)
        result = await original(bot, method, timeout)
        if len(accepted) == 1:
            raise TelegramNetworkError(method, "accepted but response lost")
        return result

    e.session.make_request = loss
    assert await e.worker.run_once()
    assert not await e.worker.run_once()
    await e.pool.execute("UPDATE message_delivery SET available_at=0 WHERE key=$1", first.key)
    assert await DeliveryWorker(PostgresDeliveryQueue(e.pool), e.bot).run_once()
    assert await e.worker.run_once()
    assert accepted == ["first", "first", "second"]


async def test_ack_lost_after_telegram_acceptance_recovers_after_lease(queue_env, monkeypatch):
    e = queue_env
    first = await enqueue(e, "first")
    original = e.queue.complete

    async def lost_ack(*args, **kwargs):
        raise ConnectionError("SQL unavailable after send")

    monkeypatch.setattr(e.queue, "complete", lost_ack)
    with pytest.raises(ConnectionError):
        await e.worker.run_once()
    assert e.session.sent[0].text == "first"
    assert (await e.queue.get(first.key)).status == DeliveryStatus.SENDING
    monkeypatch.setattr(e.queue, "complete", original)
    await e.pool.execute("UPDATE message_delivery SET lease_until=0 WHERE key=$1", first.key)
    assert await DeliveryWorker(PostgresDeliveryQueue(e.pool), e.bot).run_once()
    assert len(e.session.sent) == 2
    assert (await e.queue.get(first.key)).status == DeliveryStatus.SENT


async def test_queue_waits_for_cache_and_worker_recovers_without_next_command(
    queue_env,
    economy,  # noqa: F811
):
    e = queue_env
    operation_id = f"delivery-cache:{uuid4()}"
    await e.pool.execute(
        "INSERT INTO economic_operations(id, kind, completed, cache_applied)"
        " VALUES($1, 'test', true, false)",
        operation_id,
    )
    try:
        await e.queue.enqueue(
            key=f"{e.bot.id}:cache",
            bot_id=e.bot.id,
            chat_id=42,
            payload={"chat_id": 42, "text": "Экран сохранён."},
            operation_id=operation_id,
        )
        assert not await e.worker.run_once()
        assert not e.session.sent
        operations = PostgresOperations(OperationPool(e.pool))
        resumed = DeliveryWorker(PostgresDeliveryQueue(e.pool), e.bot, recover=operations.recover)
        assert await resumed.run_once()
        assert e.session.sent[0].text == "Экран сохранён."
        assert await e.pool.fetchval(
            "SELECT cache_applied FROM economic_operations WHERE id=$1", operation_id
        )
    finally:
        await e.pool.execute("DELETE FROM message_delivery WHERE operation_id=$1", operation_id)
        await e.pool.execute("DELETE FROM economic_operations WHERE id=$1", operation_id)
