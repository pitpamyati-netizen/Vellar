"""Порядок, приоритеты, бюджет и возврат незаконченной отправки."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from mmorpg.application.delivery import (
    DeliveryKeyConflictError,
    DeliveryPolicy,
    DeliveryPriority,
    DeliveryStatus,
)
from mmorpg.application.operations import Operation
from mmorpg.infrastructure.persistence.memory_delivery import MemoryDeliveryQueue
from mmorpg.infrastructure.persistence.operations import MemoryOperations


@pytest.fixture
def queue_env():
    clock = SimpleNamespace(now=1000.0)
    return SimpleNamespace(queue=MemoryDeliveryQueue(lambda: clock.now), clock=clock)


async def enqueue(queue, key, chat=42, priority=DeliveryPriority.REPLY, bot=1):
    return await queue.enqueue(
        key=key,
        bot_id=bot,
        chat_id=chat,
        payload={"chat_id": chat, "text": key, "parse_mode": None},
        priority=priority,
    )


async def test_priority_selects_between_chats_without_overtaking_same_chat(queue_env):
    q = queue_env.queue
    await enqueue(q, "announcement", 42, DeliveryPriority.ANNOUNCEMENT)
    await enqueue(q, "reply-same-chat", 42)
    await enqueue(q, "reply-other-chat", 43)
    first = await q.claim(1, DeliveryPolicy())
    assert first.key == "reply-other-chat"
    second = await q.claim(1, DeliveryPolicy())
    assert second.key == "announcement"
    assert await q.claim(1, DeliveryPolicy()) is None
    await q.complete(second.key, second.lease_token, {"message_id": 1})
    assert (await q.claim(1, DeliveryPolicy())).key == "reply-same-chat"


async def test_concurrent_workers_claim_each_chat_head_once(queue_env):
    q = queue_env.queue
    for key in range(10):
        await enqueue(q, str(key), 42)
    claimed = await asyncio.gather(*(q.claim(1, DeliveryPolicy()) for _ in range(8)))
    assert len([row for row in claimed if row is not None]) == 1
    assert claimed[0].key == "0"


async def test_restart_after_lease_expiry_reclaims_head_and_fences_old_completion(queue_env):
    q = queue_env.queue
    await enqueue(q, "first")
    await enqueue(q, "second")
    first = await q.claim(1, DeliveryPolicy())
    queue_env.clock.now += 91
    restarted = await q.claim(1, DeliveryPolicy())
    assert restarted.key == first.key
    assert restarted.attempts == 2
    assert restarted.lease_token != first.lease_token
    assert not await q.complete(first.key, first.lease_token, {"message_id": 1})
    assert not await q.retry(first.key, first.lease_token, delay=60, error="stale")
    assert not await q.fail(first.key, first.lease_token, error="stale")
    assert await q.complete(restarted.key, restarted.lease_token, {"message_id": 2})
    assert (await q.claim(1, DeliveryPolicy())).key == "second"


async def test_budget_is_shared_between_priorities_workers_and_chat_types(queue_env):
    q = queue_env.queue
    policy = DeliveryPolicy(sends_per_second=2)
    await enqueue(q, "reply", 42)
    await enqueue(q, "announcement", "@channel", DeliveryPriority.ANNOUNCEMENT)
    await enqueue(q, "third", -42)
    rows = await asyncio.gather(*(q.claim(1, policy) for _ in range(4)))
    assert len([row for row in rows if row is not None]) == 2
    assert await q.claim(1, policy) is None
    queue_env.clock.now += 1
    assert await q.claim(1, policy) is not None
    await enqueue(q, "other-bot", 44, bot=2)
    assert (await q.claim(2, policy)).key == "other-bot"


@pytest.mark.parametrize("chat,limit,window", [(42, 3, 3.0), (-42, 20, 60.0)])
async def test_per_chat_budget_releases_at_window_boundary(queue_env, chat, limit, window):
    q = queue_env.queue
    for key in range(limit + 1):
        await enqueue(q, str(key), chat)
    for _ in range(limit):
        row = await q.claim(1, DeliveryPolicy())
        assert row is not None
        await q.complete(row.key, row.lease_token, {"message_id": 1})
    assert await q.claim(1, DeliveryPolicy()) is None
    await enqueue(q, "other-chat", 43)
    assert (await q.claim(1, DeliveryPolicy())).key == "other-chat"
    queue_env.clock.now += window
    assert (await q.claim(1, DeliveryPolicy())).key == str(limit)


async def test_429_preserves_message_and_stops_whole_bot_after_restart(queue_env):
    q = queue_env.queue
    await enqueue(q, "first")
    await enqueue(q, "other-chat", 43)
    row = await q.claim(1, DeliveryPolicy())
    assert await q.retry(row.key, row.lease_token, delay=300, error="429", cooldown=True)
    assert (await q.get("first")).status == DeliveryStatus.QUEUED
    assert await q.claim(1, DeliveryPolicy()) is None
    queue_env.clock.now += 300
    assert (await q.claim(1, DeliveryPolicy())).key == "first"


async def test_network_retry_holds_chat_order_but_not_other_chats(queue_env):
    q = queue_env.queue
    await enqueue(q, "first")
    await enqueue(q, "second")
    await enqueue(q, "other-chat", 43)
    row = await q.claim(1, DeliveryPolicy())
    await q.retry(row.key, row.lease_token, delay=60, error="network")
    assert (await q.claim(1, DeliveryPolicy())).key == "other-chat"
    assert await q.claim(1, DeliveryPolicy()) is None
    queue_env.clock.now += 60
    assert (await q.claim(1, DeliveryPolicy())).key == "first"


async def test_permanent_failure_releases_next_message_and_keeps_record(queue_env):
    q = queue_env.queue
    await enqueue(q, "first")
    await enqueue(q, "second")
    row = await q.claim(1, DeliveryPolicy())
    await q.fail(row.key, row.lease_token, error="forbidden")
    assert (await q.get("first")).status == DeliveryStatus.FAILED
    assert (await q.claim(1, DeliveryPolicy())).key == "second"


async def test_repeated_key_does_not_create_second_delivery_and_conflict_is_rejected(queue_env):
    q = queue_env.queue
    first = await enqueue(q, "first")
    assert await enqueue(q, "first") == first
    with pytest.raises(DeliveryKeyConflictError):
        await enqueue(q, "first", 43)


async def test_queue_is_invisible_before_commit_and_rolled_back_with_action(queue_env):
    q = queue_env.queue
    boundary = MemoryOperations()

    async def action():
        await enqueue(q, "first")
        assert (await q.get("first")).key == "first"
        assert await asyncio.create_task(q.get("first")) is None
        raise RuntimeError("controlled rollback")

    with pytest.raises(RuntimeError):
        await boundary.run(
            action,
            operation=Operation(id="rollback", kind="test"),
            fingerprint="test",
            codec=TypeAdapter(type(None)),
            participants=(),
        )
    assert await q.get("first") is None


async def test_commit_merges_new_rows_without_erasing_worker_progress(queue_env):
    q = queue_env.queue
    await enqueue(q, "old")
    claimed = await q.claim(1, DeliveryPolicy())
    boundary = MemoryOperations()

    async def action():
        await enqueue(q, "new", 43)
        await asyncio.create_task(q.complete("old", claimed.lease_token, {"message_id": 1}))

    await boundary.run(
        action,
        operation=Operation(id="commit", kind="test"),
        fingerprint="test",
        codec=TypeAdapter(type(None)),
        participants=(),
    )
    assert (await q.get("old")).status == DeliveryStatus.SENT
    assert (await q.get("new")).status == DeliveryStatus.QUEUED


@pytest.mark.parametrize(
    "kwargs", [{"sends_per_second": 0}, {"private_window": 0}, {"lease_seconds": -1}]
)
def test_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        DeliveryPolicy(**kwargs)
