"""Real PostgreSQL and Redis, or nothing.

These tests run the SQL and the Redis commands the bot actually issues. The rest
of the suite uses the in-memory adapters, which means a mistake in the SQL itself
- a typo, a column PostgreSQL will not accept - is invisible everywhere else. That
gap is what this package exists to close.

Skipped, never failed, when the services are not up: ``docker compose up -d
postgres redis`` and they run.

The connections are opened **once for the whole run**, not once per test. Opening
them costs about two seconds apiece here - name resolution, not the database -
and forty-six of those made this package sixteen times slower than the entire
rest of the suite. Sharing is safe because nothing is shared *through* them: each
test owns its rows and cleans them up, and a pool is a pool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mmorpg.config import Settings

# Общий на прогон цикл событий: пул asyncpg и клиент Redis привязаны к тому
# циклу, в котором созданы, поэтому «на прогон» должно быть и то, и другое.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _settings() -> Settings:
    return Settings(app_env="dev", bot_token="0:test")  # type: ignore[call-arg]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool() -> AsyncIterator[object]:
    """An asyncpg pool against the migrated database, or a skip.

    Wrapped exactly as the running game wraps it (``ReconnectingPool``), so the
    SQL here goes through the same proxy the players' queries go through.
    """
    import asyncpg

    from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool
    from mmorpg.retry import RetryPolicy

    settings = _settings()
    try:
        created = await asyncpg.create_pool(dsn=settings.postgres_dsn, min_size=1, max_size=4)
    except (OSError, asyncpg.PostgresError) as unreachable:
        pytest.skip(f"PostgreSQL is not reachable: {unreachable}")

    assert created is not None
    try:
        # The adapters expect the schema from migrations/, not an empty database.
        exists = await created.fetchval("SELECT to_regclass('public.users')")
        if exists is None:
            pytest.skip("the database has no schema: run 'alembic upgrade head' first")
        yield ReconnectingPool(created, RetryPolicy.from_settings(settings))
    finally:
        await created.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis() -> AsyncIterator[object]:
    """A Redis client on a database of its own, built the way the game builds it."""
    from redis.asyncio import Redis

    from mmorpg.infrastructure.persistence.pool import create_redis_client

    settings = _settings()
    client: Redis = create_redis_client(settings)
    try:
        await client.ping()
    # redis-py raises several unrelated types for "the server is not there".
    except Exception as unreachable:
        await client.aclose()
        pytest.skip(f"Redis is not reachable: {unreachable}")

    try:
        yield client
    finally:
        await client.aclose()
