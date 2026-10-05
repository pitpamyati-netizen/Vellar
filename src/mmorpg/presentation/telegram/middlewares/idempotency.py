"""Отсев повторных обновлений.

В приложении completed_only ставит короткую отметку только после сохранения
команды и отправки ответа. Потеря отметки передаёт повтор постоянному журналу;
сбой до сохранения позволяет немедленно повторить доставку. Старый seen/SET NX
оставлен для совместимости отдельных вызовов без журнала.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from mmorpg.domain.ports.repositories import IdempotencyStore
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.middlewares.audit import note_of

DEFAULT_TTL = 300

#: Исход, под которым отброшенное обновление попадает в журнал действий.
DUPLICATE = "duplicate"
logger = get_logger(__name__)


class IdempotencyMiddleware(BaseMiddleware):
    """Отбрасывает обновление, которое уже обработано."""

    def __init__(
        self, store: IdempotencyStore, ttl: int = DEFAULT_TTL, *, completed_only: bool = False
    ) -> None:
        self._store = store
        self._ttl = ttl
        self._completed_only = completed_only

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self._completed_only and isinstance(event, Update) and event.message is not None:
            from mmorpg.presentation.telegram.middlewares.commands import command_fingerprint

            key = f"{data['bot'].id}:{event.update_id}:{command_fingerprint(event)}"
            try:
                cached = await self._store.completed(key)
            except Exception:
                # Кэш — ускорение; отсутствие отметки проверит постоянный журнал.
                cached = False
                logger.warning("command_repeat_cache_unavailable")
            if cached:
                note = note_of(data)
                if note is not None:
                    note.done(DUPLICATE)
                return None
            result = await handler(event, data)
            try:
                await self._store.remember(key, self._ttl)
            except Exception:
                logger.warning("command_repeat_cache_unavailable")
            return result
        if isinstance(event, Update) and await self._store.seen(event.update_id, self._ttl):
            # Своей строки в журнале это не пишет: одно нажатие - одна строка, и
            # исход в ней скажет ровно то же самое (``middlewares.audit``).
            note = note_of(data)
            if note is not None:
                note.done(DUPLICATE)
            return None
        return await handler(event, data)
