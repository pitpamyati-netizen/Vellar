"""Отсев повторных обновлений.

Telegram доставляет обновление заново, когда ответ медленный или оборвалась
связь. Без этой мидлвари доставленное заново обновление разыграло бы второй ход
боя или купило бы вещь дважды. Хранилище ведётся по ``update_id`` с коротким
сроком, а проверка в Redis неделима (``SET NX``).
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
    """Отбрасывает обновление, которое уже обработано."""

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
