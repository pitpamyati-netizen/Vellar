"""Connection pools.

Both pools are created once at startup and closed on shutdown - never per request
(``docs/architecture.md``, "Latency budget").

Neither is created *once and for all*, though: a link that breaks under a running
game is replaced without the game stopping, and the call that was in the air when
it broke is made again. PostgreSQL gets that from :class:`ReconnectingPool`,
Redis has it built in and is only told to use it, and startup waits for both
instead of exiting when a stack comes up out of order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mmorpg.config import Settings
from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool, lost_connection
from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy, keep_trying

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis

logger = get_logger(__name__)

#: How often an idle Redis connection proves it is still alive. A connection that
#: died quietly is then found by the health check instead of by a player.
REDIS_HEALTH_CHECK_SECONDS = 30
REDIS_CONNECT_TIMEOUT_SECONDS = 5.0


async def create_postgres_pool(settings: Settings) -> ReconnectingPool:
    """Open the asyncpg pool with the configured bounds, and keep it open.

    Startup waits for the database rather than exiting: the container may well be
    ahead of PostgreSQL, and the operator does not care in which order Docker
    started them.
    """
    policy = RetryPolicy.from_settings(settings)
    pool = await keep_trying(
        lambda: _open_postgres(settings),
        policy=policy,
        seconds=settings.startup_wait_seconds,
        what="postgres",
        recoverable=lost_connection,
    )
    return ReconnectingPool(pool, policy)


async def _open_postgres(settings: Settings) -> Any:
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
    """Build the Redis client; its connection pool is managed by redis-py.

    redis-py can reconnect and send the command again on its own, and is told to
    here rather than left on its defaults. Repeating is safe for everything the
    game keeps in Redis: a screen, a fight, a location, a shop roll are all
    written whole, so writing them twice writes the same thing.
    """
    from redis.asyncio import Redis
    from redis.asyncio.retry import Retry
    from redis.backoff import ExponentialBackoff
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return Redis.from_url(
        settings.redis_dsn,
        decode_responses=False,
        retry=Retry(
            ExponentialBackoff(
                base=settings.reconnect_delay_seconds,
                cap=settings.reconnect_max_delay_seconds,
            ),
            settings.reconnect_attempts,
        ),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
        health_check_interval=REDIS_HEALTH_CHECK_SECONDS,
        socket_keepalive=True,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
    )


async def wait_for_redis(client: Redis, settings: Settings) -> None:
    """Ping until Redis answers. Same reason as the PostgreSQL wait above."""
    await keep_trying(
        client.ping,
        policy=RetryPolicy.from_settings(settings),
        seconds=settings.startup_wait_seconds,
        what="redis",
        recoverable=_redis_is_down,
    )


def _redis_is_down(error: BaseException) -> bool:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return isinstance(error, RedisConnectionError | RedisTimeoutError | OSError)
