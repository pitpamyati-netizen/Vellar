"""Дверь, закрытая для заблокированного.

Проверка стоит здесь, а не в каждом хендлере, по той же причине, по которой
здесь стоит обработчик ошибок: забыть её в одном месте — значит оставить дверь
приоткрытой. Заблокированный не проходит никуда: ни в игру, ни в бой, ни в
группу.

Аккаунт читается один раз и кладётся в данные под именем ``user``, поэтому
хендлеру не приходится читать его второй раз: раньше это делал он сам.

В личке заблокированному отвечают — человек должен знать, что случилось и до
каких пор. В группе молчат: группа не место для разговора о наказаниях, и бот
там отвечает только на обращённое к нему (``Claude.md``, правило 9).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, ReplyKeyboardRemove, TelegramObject

from mmorpg.domain.ports.repositories import UserRepository
from mmorpg.domain.rules import moderation as rules
from mmorpg.logging import get_logger
from mmorpg.presentation.telegram.screens.moderation import banned_text

logger = get_logger(__name__)


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = event if isinstance(event, Message) else None
        users: UserRepository | None = data.get("users")
        if message is None or message.from_user is None or users is None:
            return await handler(event, data)

        user = await users.get(message.from_user.id)
        data["user"] = user
        now = int(time.time())
        if user is None or not rules.is_banned(user.ban, now=now):
            return await handler(event, data)

        logger.info("banned_user_turned_away", telegram_id=user.telegram_id)
        if message.chat.type == ChatType.PRIVATE:
            await message.answer(
                banned_text(user.ban, now=now),
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=None,
            )
        return None
