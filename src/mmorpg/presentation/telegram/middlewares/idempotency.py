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
from mmorpg.presentation.telegram.middlewares.audit import note_of

DEFAULT_TTL = 300

#: Исход, под которым отброшенное обновление попадает в журнал действий.
DUPLICATE = "duplicate"


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
            # Своей строки в журнале это не пишет: одно нажатие - одна строка, и
            # исход в ней скажет ровно то же самое (``middlewares.audit``).
            note = note_of(data)
            if note is not None:
                note.done(DUPLICATE)
            return None
        return await handler(event, data)
