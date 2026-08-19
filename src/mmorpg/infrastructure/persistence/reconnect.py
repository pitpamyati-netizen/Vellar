"""The query that was in the air when the link went down.

asyncpg's pool already reconnects on its own: a connection it kept and that died
while idle is thrown away on the next ``acquire`` and a fresh one is opened in
its place. What the pool does *not* do is run the query again, so a PostgreSQL
that restarted between two updates costs one player their action - they read
«Что-то пошло не так» about a database that has been healthy for a second.

:class:`ReconnectingPool` sits between the repositories and the pool and repeats
that call. Where it stops repeating is the whole point of the module, and the
line is drawn where certainty ends (``docs/adr/0009-repeating-a-lost-query.md``):

- **no connection was obtained** - nothing was sent, PostgreSQL never heard of
  the statement, so anything may be repeated, a write included;
- **a connection was obtained and the statement failed on it** - a ``SELECT`` is
  run again, because reading twice reads the same thing, while an ``UPDATE`` is
  not: the server may have committed it and lost only the answer on the way
  back, and a repeat would take the gold twice.

The pool is wrapped, not subclassed: the repositories keep calling ``fetch``,
``fetchrow``, ``fetchval`` and ``execute`` exactly as before and know nothing
about any of this.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg

logger = get_logger(__name__)


def lost_connection(error: BaseException) -> bool:
    """Whether this error is a broken link rather than a broken query.

    A syntax error or a violated constraint is an answer from PostgreSQL and
    repeating it would only produce the same answer more slowly.
    """
    import asyncpg

    if isinstance(
        error,
        asyncpg.exceptions.PostgresConnectionError
        | asyncpg.exceptions.CannotConnectNowError
        | asyncpg.exceptions.TooManyConnectionsError
        | asyncpg.exceptions.AdminShutdownError
        | asyncpg.exceptions.CrashShutdownError,
    ):
        return True
    if isinstance(error, asyncpg.exceptions.InterfaceError):
        # The same class covers plain misuse ("wrong number of arguments"), which
        # is a bug in the query and must surface at once.
        return "closed" in str(error).lower()
    # ConnectionResetError and friends are OSError; a command timeout is
    # TimeoutError. Both mean the answer did not arrive.
    return isinstance(error, OSError | TimeoutError)


def repeatable(query: str) -> bool:
    """Whether running this statement twice is the same as running it once."""
    head = query.lstrip()
    while head.startswith("--"):
        _, _, head = head.partition("\n")
        head = head.lstrip()
    return head[:6].upper() == "SELECT"


def _head(query: str) -> str:
    """The first line of a statement - enough to recognise it in the log."""
    return " ".join(query.split())[:60]


class ReconnectingPool:
    """The asyncpg pool with one habit added: it runs a lost call again."""

    __slots__ = ("_policy", "_pool")

    def __init__(self, pool: asyncpg.Pool, policy: RetryPolicy) -> None:
        self._pool = pool
        self._policy = policy

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetch", query, args, kwargs)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchrow", query, args, kwargs)

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("fetchval", query, args, kwargs)

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await self._run("execute", query, args, kwargs)

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        """A connection of one's own, for a caller who runs its own transaction.

        Handed out bare: a transaction is repeated as a whole or not at all, and
        this wrapper cannot know where it began.
        """
        return self._pool.acquire(*args, **kwargs)

    async def close(self) -> None:
        await self._pool.close()

    async def _run(
        self, method: str, query: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        repeats = 0
        while True:
            connected = False
            try:
                async with self._pool.acquire() as connection:
                    connected = True
                    result = await getattr(connection, method)(query, *args, **kwargs)
            except Exception as error:
                if not lost_connection(error):
                    raise
                # Before the connection was obtained nothing was sent, so even a
                # write is safe to repeat; after it, only a read is.
                may_repeat = not connected or repeatable(query)
                if not may_repeat or repeats >= self._policy.attempts:
                    logger.error(
                        "postgres_call_lost",
                        query=_head(query),
                        repeats=repeats,
                        repeated=may_repeat,
                        error=type(error).__name__,
                    )
                    raise
                repeats += 1
                wait = self._policy.pause(repeats)
                logger.warning(
                    "postgres_repeating",
                    query=_head(query),
                    attempt=repeats,
                    wait=wait,
                    error=type(error).__name__,
                )
                await asyncio.sleep(wait)
            else:
                if repeats:
                    logger.info("postgres_recovered", query=_head(query), repeats=repeats)
                return result
