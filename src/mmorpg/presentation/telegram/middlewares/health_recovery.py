"""Новая команда видит здоровье после отдыха, повтор — сохранённый прежний ответ."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from mmorpg.application.services.health_recovery import HealthRecovery


class HealthRecoveryMiddleware(BaseMiddleware):
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            # Сброс подтверждается по версии героя; отдых не меняет её во время выбора.
            state = data.get("state")
            if state is not None and (await state.get_state() or "").startswith("Reset:"):
                return await handler(event, data)
            characters = data["characters"]
            character = await characters.get_active(event.from_user.id)
            if character is not None:
                await HealthRecovery(data["content"], characters, data["state_cache"]).apply(
                    character.id, now=int(self.clock())
                )
        return await handler(event, data)
