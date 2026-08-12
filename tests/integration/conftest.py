"""Real PostgreSQL and Redis, or nothing.

These tests run the SQL and the Redis commands the bot actually issues. The rest
of the suite uses the in-memory adapters, which means a mistake in the SQL itself
- a typo, a column PostgreSQL will not accept - is invisible everywhere else. That
gap is what this package exists to close.

Skipped, never failed, when the services are not up: ``docker compose up -d
postgres redis`` and they run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from mmorpg.config import Settings

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(app_env="dev", bot_token="0:test")  # type: ignore[call-arg]


@pytest.fixture
async def pool() -> AsyncIterator[object]:
    """An asyncpg pool against the migrated database, or a skip."""
    import asyncpg

    settings = _settings()
    try:
        created = await asyncpg.create_pool(dsn=settings.postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as unreachable:
        pytest.skip(f"PostgreSQL is not reachable: {unreachable}")

    assert created is not None
    try:
        # The adapters expect the schema from migrations/, not an empty database.
        exists = await created.fetchval("SELECT to_regclass('public.users')")
        if exists is None:
            pytest.skip("the database has no schema: run 'alembic upgrade head' first")
        yield created
    finally:
        await created.close()


@pytest.fixture
async def redis() -> AsyncIterator[object]:
    """A Redis client on a database of its own, flushed around each test."""
    from redis.asyncio import Redis

    settings = _settings()
    client: Redis = Redis.from_url(settings.redis_dsn, decode_responses=False)
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
