"""Error boundary.

A crash must never turn into silence: the player is blind to a missing answer, so
an unhandled exception is logged and answered with a plain sentence and a working
keyboard (accessibility rule 12).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from mmorpg.logging import get_logger

logger = get_logger(__name__)

APOLOGY = "Что-то пошло не так, действие не выполнено. Нажмите «Назад» или «Главное меню»."


class ErrorMiddleware(BaseMiddleware):
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
