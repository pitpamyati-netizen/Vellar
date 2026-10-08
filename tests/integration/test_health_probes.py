"""Остановка SQL, Redis и очереди обнаруживается на отдельных службах."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from scripts.test_stand import load_test_settings

from mmorpg.health import probe_services
from mmorpg.infrastructure.persistence.operations import ECONOMY_LOCK
from mmorpg.presentation.telegram.delivery import DeliveryWorker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_real_storage_probe_and_unavailable_sql(pool, redis):
    await probe_services(pool, redis, str(uuid4()))
    created = await asyncpg.create_pool(load_test_settings().postgres_dsn, min_size=1, max_size=1)
    await created.close()
    with pytest.raises(asyncpg.InterfaceError):
        await probe_services(created, redis, str(uuid4()))


async def test_blocked_command_lock_is_detected_and_recovers(pool, redis):
    held = await asyncpg.connect(load_test_settings().postgres_dsn)
    await held.execute("SELECT pg_advisory_lock($1)", ECONOMY_LOCK)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await probe_services(pool, redis, str(uuid4()))
    finally:
        await held.close()
    async with asyncio.timeout(3):
        await probe_services(pool, redis, str(uuid4()))


async def test_unavailable_redis_is_detected_and_recovers(pool, redis):
    await redis.execute_command("CLIENT", "PAUSE", 300, "ALL")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await probe_services(pool, redis, str(uuid4()))
    async with asyncio.timeout(3):
        await probe_services(pool, redis, str(uuid4()))


async def test_stopped_delivery_worker_is_unhealthy():
    from types import SimpleNamespace

    from mmorpg.infrastructure.persistence.memory_delivery import MemoryDeliveryQueue

    worker = DeliveryWorker(MemoryDeliveryQueue(), SimpleNamespace(id=987651), sends_per_second=10)
    assert not worker.healthy(30)
    worker.start()
    await asyncio.sleep(0.02)
    assert worker.healthy(30)
    await worker.aclose()
    assert not worker.healthy(30)
