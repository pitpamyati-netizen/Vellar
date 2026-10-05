"""Постоянный результат сообщения до выбора текущего экрана и обработчика."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from aiogram import BaseMiddleware, Bot
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ChatType
from aiogram.methods import SendMessage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, TelegramObject, Update
from pydantic import TypeAdapter

from mmorpg.application.delivery import DeliveryQueue
from mmorpg.application.operations import (
    Operation,
    OperationBoundary,
    OperationCommittedError,
    _repositories,
)
from mmorpg.domain.rules.group_commands import parse_group_command
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.middlewares.audit import note_of
from mmorpg.presentation.telegram.middlewares.dependencies import Dependencies
from mmorpg.presentation.telegram.middlewares.idempotency import DUPLICATE
from mmorpg.presentation.telegram.screens.format import HARD_LIMIT

COMMAND_KIND = "telegram.command.v1"


@dataclass
class CommandResult:
    status: str
    replies: list[dict[str, Any]] = field(default_factory=list)


def command_fingerprint(event: Update) -> str:
    assert event.message is not None
    # Личный текст используется только для хеша и не записывается в журнал.
    return hashlib.sha256(event.message.model_dump_json(exclude_none=True).encode()).hexdigest()


class CommandMiddleware(BaseMiddleware):
    def __init__(
        self,
        dependencies: Dependencies,
        reaper: MessageReaper,
        *,
        delivery: DeliveryQueue | None = None,
        wake_delivery: Callable[[], None] | None = None,
    ) -> None:
        self._participants = _repositories(list(dependencies.as_data().values()))
        self._boundary = cast(OperationBoundary, cast(Any, dependencies.characters).operations)
        self._codec = TypeAdapter(CommandResult)
        self._reaper = reaper
        self._registry = getattr(dependencies, "registry", None)
        self._overlays = getattr(dependencies, "overlays", None)
        self._delivery = delivery
        self._wake_delivery = wake_delivery

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update) or event.message is None:
            return await handler(event, data)
        if (
            event.message.chat.type != ChatType.PRIVATE
            and parse_group_command(event.message.text or "") is None
        ):
            return await handler(event, data)
        bot: Bot = data["bot"]
        message = event.message
        legacy_id = f"telegram:{bot.id}:{message.chat.id}:{message.message_id}"
        operation = Operation(
            id=f"command:{legacy_id}",
            kind=COMMAND_KIND,
            participants=self._participants,
            command=True,
            reply_chat_id=message.chat.id,
            legacy_id=legacy_id,
        )

        async def execute() -> CommandResult:
            # Подтянуть правку, чей SQL уже сохранён, а перенос кэша прервался.
            # До новой команды _boundary сначала доведёт сохранённый Redis.
            if self._registry is not None and self._overlays is not None:
                await self._registry.reload(self._overlays)
            result = await handler(event, data)
            note = note_of(data)
            status = "ignored" if result is UNHANDLED else (note.result if note else "ok")
            return CommandResult(status=status, replies=operation.replies)

        saved = await self._boundary.run(
            execute,
            operation=operation,
            fingerprint=command_fingerprint(event),
            codec=self._codec,
            participants=self._participants,
        )
        if operation.replayed:
            note = note_of(data)
            if note is not None:
                note.done(DUPLICATE)
            if self._delivery is not None and await self._delivery.pending_for_operation(
                operation.id, bot.id, message.chat.id
            ):
                if self._wake_delivery is not None:
                    self._wake_delivery()
                return UNHANDLED if saved.status == "ignored" else None
            for payload in saved.replies:
                # Повтор видит прежний результат, но не откатывает нынешний экран.
                reply = SendMessage.model_validate(payload)
                if message.chat.type == ChatType.PRIVATE:
                    notice = (
                        "\n\nЭто сохранённый ответ на прежнее действие. "
                        "Нажмите «Главное меню», чтобы открыть текущее состояние."
                    )
                    if len(reply.text) + len(notice) <= HARD_LIMIT:
                        reply.text += notice
                    reply.reply_markup = ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="Главное меню")]], resize_keyboard=True
                    )
                else:
                    reply.reply_markup = None
                reply.parse_mode = None
                try:
                    sent = await bot(reply)
                except Exception as error:
                    raise OperationCommittedError(operation.id) from error
                if message.chat.type != ChatType.PRIVATE:
                    self._reaper.schedule(bot.delete_message, message.chat.id, sent.message_id)
            if saved.status == "previously_completed":
                await message.answer(
                    "Это действие уже сохранено. Нажмите «Главное меню», чтобы продолжить.",
                    parse_mode=None,
                )
        return UNHANDLED if saved.status == "ignored" else None
