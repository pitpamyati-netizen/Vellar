"""Counting and timing every update.

The outermost middleware there is: what it measures is the whole cost of an
update as the player experiences it - the duplicate check, the repositories, the
flow, the answer - and not the part of it a handler happens to be responsible
for.

It measures and nothing else. An exception passes through untouched, because the
answer to a broken update belongs to ``ErrorMiddleware``, and a middleware that
swallowed one to keep its counters tidy would turn a crash into silence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from mmorpg.metrics import Metrics, Stopwatch


class MetricsMiddleware(BaseMiddleware):
    """Records how long each update took, and whether it blew up."""

    def __init__(self, metrics: Metrics) -> None:
        self._metrics = metrics

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        watch = Stopwatch()
        failed = False
        try:
            return await handler(event, data)
        except Exception:
            failed = True
            raise
        finally:
            self._metrics.observe(watch.seconds, failed=failed)
