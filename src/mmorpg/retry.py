"""Waiting out a connection that went away.

Three different links can break under a running game - PostgreSQL, Redis and
Telegram - and all three break the same way: a call that was in the air when the
socket died comes back as an error, while the link itself is healthy again a
second later. The rule for all three is one rule, so the arithmetic of the waits
lives here, in one place, and the layers that know about sockets use it.

Nothing here decides *what* may be repeated: that is the caller's business,
because only the caller knows whether repeating changes the world twice
(``docs/adr/0009-repeating-a-lost-query.md``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mmorpg.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mmorpg.config import Settings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times a lost call is repeated, and how long the waits are.

    The waits double and then stop growing: the first repeat is quick, because
    most breaks are a dropped connection that is replaced instantly, and the
    later ones are slow, because a database that is still down after a second
    is restarting rather than blinking.
    """

    attempts: int = 5
    delay: float = 0.2
    max_delay: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            attempts=settings.reconnect_attempts,
            delay=settings.reconnect_delay_seconds,
            max_delay=settings.reconnect_max_delay_seconds,
        )

    def pause(self, attempt: int) -> float:
        """How long to wait before repeat number ``attempt``; the first one is 1."""
        return min(self.delay * 2.0 ** (attempt - 1), self.max_delay)


async def keep_trying[T](
    call: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    seconds: float,
    what: str,
    recoverable: Callable[[BaseException], bool],
) -> T:
    """Call until it works, or until ``seconds`` of waiting have passed.

    This is the startup path, and it is deliberately patient rather than
    counted: a stack that comes up together does not come up in order, and a
    PostgreSQL that needs five more seconds to open its socket is not a reason
    for the bot to exit. Under a running game the counted repeats are used
    instead - a player waiting for a screen must not wait a minute for it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    attempt = 0
    while True:
        try:
            result = await call()
        except Exception as error:
            if not recoverable(error) or loop.time() >= deadline:
                raise
            attempt += 1
            wait = policy.pause(attempt)
            logger.warning(
                "waiting_for_service",
                service=what,
                attempt=attempt,
                wait=wait,
                error=type(error).__name__,
            )
            await asyncio.sleep(wait)
        else:
            if attempt:
                logger.info("service_answered", service=what, attempts=attempt)
            return result
