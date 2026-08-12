"""Duplicate update filter.

Telegram redelivers updates when an answer is slow or a connection drops. Without
this middleware a redelivered update would resolve a second combat turn or buy an
item twice. The store is keyed by ``update_id`` with a short TTL, and the check is
atomic in Redis (``SET NX``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from mmorpg.domain.ports.repositories import IdempotencyStore
from mmorpg.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TTL = 300


class IdempotencyMiddleware(BaseMiddleware):
    """Drops an update that was already handled."""

    def __init__(self, store: IdempotencyStore, ttl: int = DEFAULT_TTL) -> None:
        self._store = store
        self._ttl = ttl

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and await self._store.seen(event.update_id, self._ttl):
            logger.info("duplicate_update_dropped", update_id=event.update_id)
            return None
        return await handler(event, data)
