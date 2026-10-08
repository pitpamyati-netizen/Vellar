"""Чтение частей экрана внутри той же постоянной операции, что команды."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from mmorpg.domain.ports.repositories import StateCache
from mmorpg.presentation.telegram import reading
from mmorpg.presentation.telegram.keyboards.reply import keyboard_for
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId


class ReadingMiddleware(BaseMiddleware):
    def __init__(self, cache: StateCache) -> None:
        self.cache = cache

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        token = reading.reader_cache.set(self.cache)
        try:
            if isinstance(event, Message):
                message = event
                text = (message.text or "").strip()
                command = reading.READ_COMMAND.fullmatch(text)
                if message.chat.type == ChatType.PRIVATE and (
                    command or text in {reading.NEXT, reading.PREVIOUS}
                ):
                    saved_key = reading.key(data["bot"].id, message.chat.id)
                    raw = await self.cache.get(saved_key)
                    if raw:
                        saved = json.loads(raw)
                        screen = reading.SCREEN_CODEC.validate_python(saved["screen"])
                        number = (
                            reading.requested_number(command[1])
                            if command
                            else saved["number"] + (1 if text == reading.NEXT else -1)
                        )
                        number = min(max(1, number), len(reading.parts(screen)))
                        saved["number"] = number
                        await self.cache.set(
                            saved_key, json.dumps(saved, ensure_ascii=False), ttl=365 * 24 * 60 * 60
                        )
                        screen = reading.page(screen, number)
                        emoji = saved["emoji"]
                    else:
                        screen = Screen(
                            ScreenId.START,
                            (
                                "Прежний длинный экран уже сменился.",
                                "Наберите /осмотреться для текущего экрана "
                                "или /меню для главного меню.",
                            ),
                        )
                        emoji = False
                    await message.answer(
                        screen.body(),
                        reply_markup=keyboard_for(screen, emoji=emoji),
                        parse_mode=None,
                    )
                    return None
            return await handler(event, data)
        finally:
            reading.reader_cache.reset(token)
