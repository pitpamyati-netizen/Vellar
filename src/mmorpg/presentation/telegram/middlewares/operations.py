"""Ответ об успешном действии отправляется после сохранения ценностей."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, TelegramObject, Update

from mmorpg.application.operations import current_operation
from mmorpg.logging import get_logger

logger = get_logger(__name__)


class EconomicRecoveryMiddleware(BaseMiddleware):
    """Сначала довести сохранённый экран, затем выбрать обработчик команды."""

    def __init__(self, recover: Callable[[], Awaitable[None]] | None) -> None:
        self.recover = recover

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self.recover is not None:
            try:
                await self.recover()
            except Exception:
                logger.exception("economic_recovery_failed")
                message = event.message if isinstance(event, Update) else event
                if isinstance(message, Message):
                    await message.answer(
                        "Сейчас не удалось проверить сохранённое состояние. "
                        "Новое действие не начато. Попробуйте ещё раз позже.",
                        parse_mode=None,
                    )
                return None
        return await handler(event, data)


class EconomicSendMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: Callable[[Bot, TelegramMethod[Any]], Awaitable[Any]],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        operation = current_operation()
        if operation is None or not isinstance(method, SendMessage):
            return await make_request(bot, method)

        async def deliver() -> None:
            try:
                await make_request(bot, method)
            except TelegramAPIError:
                # Недоступный второй участник не отменяет уже сохранённую сделку
                # и не прерывает ответы остальным. Постоянная очередь — M02.
                logger.warning("operation_message_undelivered", operation_id=operation.id)

        operation.after_commit.append(deliver)
        # Личные ответы не используют номер отправленного сообщения. Групповой
        # обработчик отправляет ответ вне операции сервиса и получает настоящий id.
        return Message(
            message_id=0,
            date=datetime.fromtimestamp(0, UTC),
            chat=Chat(id=method.chat_id if isinstance(method.chat_id, int) else 0, type="private"),
            text=method.text,
        )
