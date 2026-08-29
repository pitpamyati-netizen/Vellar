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

Мьют мягче: замолчавший играет как обычно везде, кроме игровой группы, где бот
удаляет сказанное им и не пускает дальше. Ничего ему при этом не отвечают — ни
в личке (там он не замолчан), ни в группе.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyKeyboardRemove, TelegramObject

from mmorpg.domain.ports.repositories import UserRepository
from mmorpg.domain.rules import moderation as rules
from mmorpg.presentation.telegram.middlewares.audit import note_of
from mmorpg.presentation.telegram.screens.moderation import banned_text

#: Исход, под которым закрытая дверь попадает в журнал действий.
BANNED = "banned"
#: Исход стёртого в группе сообщения замолчавшего.
MUTED = "muted"

_GROUPS = frozenset({ChatType.GROUP, ChatType.SUPERGROUP})


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

        if user is not None and rules.is_banned(user.ban, now=now):
            note = note_of(data)
            if note is not None:
                note.done(BANNED)
            if message.chat.type == ChatType.PRIVATE:
                await message.answer(
                    banned_text(user.ban, now=now),
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode=None,
                )
            return None

        # Замолчавший играет как обычно; стирается только сказанное им в группе.
        if user is not None and rules.is_muted(user.mute, now=now) and message.chat.type in _GROUPS:
            note = note_of(data)
            if note is not None:
                note.done(MUTED)
            with suppress(TelegramAPIError):
                await message.delete()
            return None

        return await handler(event, data)
