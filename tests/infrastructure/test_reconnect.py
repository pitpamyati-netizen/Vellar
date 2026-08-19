"""A database that went away and came back, and what the game did meanwhile.

The whole file is about one question: which calls are made a second time. Reads
always; writes only while it is certain that nothing was sent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from mmorpg.infrastructure.persistence.reconnect import (
    ReconnectingPool,
    lost_connection,
    repeatable,
)
from mmorpg.retry import RetryPolicy

SELECT = "SELECT gold FROM characters WHERE id = $1"
UPDATE = "UPDATE characters SET gold = gold - $2 WHERE id = $1 AND gold >= $2"

#: No real waiting in the tests; the arithmetic of the waits is checked on its own.
QUICK = RetryPolicy(attempts=3, delay=0.0, max_delay=0.0)


def dropped() -> Exception:
    return asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")


class FakePool:
    """A pool that breaks where a real one breaks: on acquire, or on the query."""

    def __init__(self, *, acquire_failures: int = 0, query_failures: int = 0) -> None:
        self._acquire_failures = acquire_failures
        self._query_failures = query_failures
        self.acquired = 0
        self.calls: list[str] = []
        self.closed = False

    def acquire(self):
        return self._connection()

    @asynccontextmanager
    async def _connection(self):
        if self._acquire_failures > 0:
            self._acquire_failures -= 1
            raise dropped()
        self.acquired += 1
        yield self

    async def _call(self, query: str) -> str:
        self.calls.append(query)
        if self._query_failures > 0:
            self._query_failures -= 1
            raise dropped()
        return "done"

    async def fetch(self, query: str, *args: object) -> str:
        return await self._call(query)

    fetchrow = fetch
    fetchval = fetch
    execute = fetch

    async def close(self) -> None:
        self.closed = True


async def test_a_lost_connection_is_replaced_and_the_query_is_made_again() -> None:
    """Two dead connections in the pool, and the player still gets their answer."""
    pool = FakePool(acquire_failures=2)

    result = await ReconnectingPool(pool, QUICK).fetchrow(SELECT, 1)

    assert result == "done"
    assert pool.calls == [SELECT]


async def test_a_read_is_repeated_even_when_the_link_died_mid_query() -> None:
    pool = FakePool(query_failures=1)

    result = await ReconnectingPool(pool, QUICK).fetch(SELECT, 1)

    assert result == "done"
    assert pool.calls == [SELECT, SELECT]


async def test_a_write_that_may_have_landed_is_never_made_twice() -> None:
    """The gold might already be gone: PostgreSQL could have committed and lost
    only the answer on the way back. A second attempt would take it again."""
    pool = FakePool(query_failures=1)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await ReconnectingPool(pool, QUICK).execute(UPDATE, 1, 50)

    assert pool.calls == [UPDATE]


async def test_a_write_is_repeated_while_nothing_has_been_sent() -> None:
    """No connection was obtained, so PostgreSQL never heard of the statement."""
    pool = FakePool(acquire_failures=2)

    await ReconnectingPool(pool, QUICK).execute(UPDATE, 1, 50)

    assert pool.calls == [UPDATE]
    assert pool.acquired == 1


async def test_a_broken_query_is_not_repeated() -> None:
    """A syntax error is an answer from the database, not a broken link."""

    class Rejecting(FakePool):
        async def _call(self, query: str) -> str:
            self.calls.append(query)
            raise asyncpg.exceptions.PostgresSyntaxError("syntax error")

    pool = Rejecting()
    with pytest.raises(asyncpg.exceptions.PostgresSyntaxError):
        await ReconnectingPool(pool, QUICK).fetch(SELECT, 1)

    assert pool.calls == [SELECT]


async def test_the_repeats_are_bounded() -> None:
    """A database that is really down is reported, not waited for for ever."""
    pool = FakePool(acquire_failures=99)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await ReconnectingPool(pool, QUICK).fetch(SELECT, 1)

    assert pool.calls == []


async def test_closing_goes_through_to_the_pool() -> None:
    pool = FakePool()
    await ReconnectingPool(pool, QUICK).close()
    assert pool.closed


def test_the_wait_doubles_up_to_the_ceiling() -> None:
    policy = RetryPolicy(attempts=5, delay=0.2, max_delay=1.0)
    assert [policy.pause(attempt) for attempt in (1, 2, 3, 4, 5)] == [0.2, 0.4, 0.8, 1.0, 1.0]


@pytest.mark.parametrize(
    ("query", "twice_is_the_same"),
    [
        (SELECT, True),
        ("\n    SELECT 1\n", True),
        ("-- who is here\nSELECT 1", True),
        (UPDATE, False),
        ("INSERT INTO users (id) VALUES ($1)", False),
        ("WITH gone AS (DELETE FROM trades RETURNING id) SELECT count(*) FROM gone", False),
    ],
)
def test_only_a_read_may_be_run_twice(query: str, twice_is_the_same: bool) -> None:
    assert repeatable(query) is twice_is_the_same


async def test_redis_is_told_to_reconnect_and_send_the_command_again() -> None:
    """redis-py can do this itself; what matters is that it is asked to.

    Building the client opens nothing, so this needs no Redis running.
    """
    from mmorpg.config import AppEnv, Settings
    from mmorpg.infrastructure.persistence.pool import (
        REDIS_HEALTH_CHECK_SECONDS,
        create_redis_client,
    )

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=AppEnv.DEV,
        reconnect_attempts=4,
    )
    client = create_redis_client(settings)
    try:
        retry = client.get_retry()
        assert retry is not None
        assert retry.get_retries() == 4
        kwargs = client.get_connection_kwargs()
        assert kwargs["health_check_interval"] == REDIS_HEALTH_CHECK_SECONDS
        assert kwargs["socket_keepalive"] is True
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("error", "is_a_broken_link"),
    [
        (asyncpg.exceptions.ConnectionDoesNotExistError("gone"), True),
        (asyncpg.exceptions.CannotConnectNowError("starting up"), True),
        (asyncpg.exceptions.AdminShutdownError("shutting down"), True),
        (asyncpg.exceptions.TooManyConnectionsError("too many"), True),
        (asyncpg.exceptions.InterfaceError("connection is closed"), True),
        (ConnectionResetError("reset by peer"), True),
        (TimeoutError(), True),
        (asyncpg.exceptions.InterfaceError("the server expects 2 arguments"), False),
        (asyncpg.exceptions.PostgresSyntaxError("syntax error"), False),
        (asyncpg.exceptions.UniqueViolationError("duplicate key"), False),
        (ValueError("nothing to do with the database"), False),
    ],
)
def test_a_broken_link_is_told_from_a_broken_query(
    error: BaseException, is_a_broken_link: bool
) -> None:
    assert lost_connection(error) is is_a_broken_link
