"""Пулы соединений.

Оба пула создаются один раз на старте и закрываются при остановке - никогда не
на запрос (``docs/architecture.md``, «Бюджет задержки»).

При этом ни один из них не создаётся *раз и навсегда*: связь, оборвавшаяся под
работающей игрой, заменяется, не останавливая игру, а вызов, бывший в воздухе,
делается заново. PostgreSQL получает это от :class:`ReconnectingPool`, у Redis
это встроено и ему лишь велят этим пользоваться, а старт ждёт обоих вместо того,
чтобы выйти, когда стек поднялся не по порядку.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mmorpg.config import Settings
from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool, lost_connection
from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy, keep_trying

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from redis.asyncio import Redis

logger = get_logger(__name__)

#: Как часто простаивающее соединение Redis доказывает, что оно живо. Тихо умершее
#: соединение находит тогда проверка здоровья, а не игрок.
REDIS_HEALTH_CHECK_SECONDS = 30
REDIS_CONNECT_TIMEOUT_SECONDS = 5.0


async def create_postgres_pool(settings: Settings) -> ReconnectingPool:
    """Открыть пул asyncpg с настроенными границами и держать его открытым.

    Старт ждёт базу, а не выходит: контейнер вполне может опередить PostgreSQL, а
    тому, кто запускал, всё равно, в каком порядке их поднял Docker.
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
    if pool is None:  # pragma: no cover - asyncpg возвращает None только при неверном обращении
        msg = "could not create the PostgreSQL pool"
        raise RuntimeError(msg)
    return pool


def create_redis_client(settings: Settings) -> Redis:
    """Собрать клиент Redis; его пулом соединений управляет redis-py.

    redis-py умеет переподключиться и послать команду заново сам, и здесь ему это
    велят, а не оставляют на значениях по умолчанию. Повторять безопасно для всего,
    что игра держит в Redis: экран, бой, локация и прилавок пишутся целиком, поэтому
    записать их дважды - записать то же самое.
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
    """Стучаться, пока Redis не ответит. Причина та же, что у ожидания PostgreSQL выше."""
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
