"""Connection pools.

Both pools are created once at startup and closed on shutdown - never per request
(``docs/architecture.md``, "Latency budget").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mmorpg.config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg
    from redis.asyncio import Redis


async def create_postgres_pool(settings: Settings) -> asyncpg.Pool:
    """Open the asyncpg pool with the configured bounds."""
    import asyncpg

    pool = await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=settings.postgres_pool_min,
        max_size=settings.postgres_pool_max,
        command_timeout=5.0,
    )
    if pool is None:  # pragma: no cover - asyncpg only returns None on misuse
        msg = "could not create the PostgreSQL pool"
        raise RuntimeError(msg)
    return pool


def create_redis_client(settings: Settings) -> Redis:
    """Build the Redis client; its connection pool is managed by redis-py."""
    from redis.asyncio import Redis

    return Redis.from_url(settings.redis_dsn, decode_responses=False)
