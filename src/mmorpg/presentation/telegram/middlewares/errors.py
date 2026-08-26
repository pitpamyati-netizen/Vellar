"""Граница отказа.

Падение не должно превращаться в тишину: пропавшего ответа игрок не видит,
поэтому необработанное исключение пишется в журнал, а игроку отвечают простой
фразой и работающей клавиатурой (правило доступности 12).

Пойманный отказ ещё и считается (``mmorpg.metrics``) и пишется в запись об
обновлении (``middlewares.audit``): извинение, которое читает игрок и не видит
никто больше, - это отказ, о котором оператор так и не узнал.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from mmorpg.logging import get_logger
from mmorpg.metrics import Metrics
from mmorpg.presentation.telegram.middlewares.audit import FAILED, note_of

logger = get_logger(__name__)

APOLOGY = "Что-то пошло не так, действие не выполнено. Нажмите «Назад» или «Главное меню»."


class ErrorMiddleware(BaseMiddleware):
    def __init__(self, metrics: Metrics | None = None) -> None:
        self._metrics = metrics

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("handler_failed", event_type=type(event).__name__)
            if self._metrics is not None:
                self._metrics.failed()
            note = note_of(data)
            if note is not None:
                note.done(FAILED)
            message = _message_of(event)
            if message is not None:
                await message.answer(APOLOGY, parse_mode=None)
            return None


def _message_of(event: TelegramObject) -> Message | None:
    if isinstance(event, Message):
        return event
    if isinstance(event, Update):
        return event.message
    return None
