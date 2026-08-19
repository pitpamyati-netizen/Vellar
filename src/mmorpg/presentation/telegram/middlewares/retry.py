"""An answer that never left, sent again.

A player action produces exactly one message, and that message *is* the answer:
a screen reader user who gets nothing has no way to tell a broken socket from a
game that ignored them. So a request that died on the way to Telegram is made
again once the link is back, instead of turning into silence and an apology.

Only a lost link is repeated. Anything Telegram answered - a bad request, a
player who blocked the bot, a wrong token - is an answer and not a break, and
repeating it would produce the same answer more slowly.

The cost is honest and worth naming: a request that reached Telegram and lost
only its reply on the way back is sent twice, so a player may see one screen
twice. For a blind player a repeated screen is noise; silence is a dead end.

``getUpdates`` is deliberately left alone: aiogram's polling loop has its own
endless backoff around it, and two layers of waiting would only make the first
answer after an outage slower.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import (
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import GetUpdates
from aiogram.methods.base import TelegramMethod, TelegramType

from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiogram import Bot
    from aiogram.methods import Response

logger = get_logger(__name__)


class RetryRequestMiddleware(BaseRequestMiddleware):
    """Repeat a Telegram call that lost its connection."""

    def __init__(self, policy: RetryPolicy) -> None:
        self._policy = policy

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        # ``getUpdates`` is left to aiogram's own endless backoff, see the module
        # docstring. Read as a flag rather than as an early exit: the request is
        # still made here, it is only never made twice.
        polling = isinstance(method, GetUpdates)
        name = type(method).__name__
        repeats = 0
        while True:
            try:
                answer = await make_request(bot, method)
            except Exception as error:
                wait = None if polling else self._wait_after(error)
                if wait is None:
                    raise
                if repeats >= self._policy.attempts:
                    logger.error(
                        "telegram_call_lost",
                        method=name,
                        repeats=repeats,
                        error=type(error).__name__,
                    )
                    raise
                repeats += 1
                pause = max(wait, self._policy.pause(repeats))
                logger.warning(
                    "telegram_repeating",
                    method=name,
                    attempt=repeats,
                    wait=pause,
                    error=type(error).__name__,
                )
                await asyncio.sleep(pause)
            else:
                if repeats:
                    logger.info("telegram_recovered", method=name, repeats=repeats)
                return answer

    def _wait_after(self, error: BaseException) -> float | None:
        """How long to wait before repeating, or ``None`` for "do not repeat"."""
        if isinstance(error, TelegramRetryAfter):
            # Flood control is not a broken link, but it is a request that has to
            # be made again. A short wait is absorbed here; a long one belongs to
            # whoever is sending that much, so it is reported instead of slept
            # through with a player waiting on the other end.
            wait = float(error.retry_after)
            return wait if wait <= self._policy.max_delay else None
        if isinstance(error, TelegramEntityTooLarge):
            # A subclass of the network error, but a permanent one: the message
            # is too big and will be too big next time.
            return None
        if isinstance(error, TelegramNetworkError | TelegramServerError):
            return 0.0
        return None
