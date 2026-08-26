"""Не отправлять быстрее, чем Telegram принимает.

У Telegram есть счёт исходящим: около тридцати сообщений в секунду на бота
целиком, сколько бы игроков ни нажимало кнопки. Превышение — это не ошибка сети,
а ответ ``429`` с просьбой подождать, и просьба эта относится к боту, а не к
тому, кто её вызвал: один всплеск задерживает ответы всем.

Поэтому очередь стоит здесь, перед отправкой, а не после отказа. Разница
существенная: пауза в несколько десятков миллисекунд, растянутая по всплеску,
никем не замечается, а ``429`` — это ответ, который не пришёл, и для того, кто
слушает экран, он неотличим от сломанной игры (``docs/accessibility.md``,
правило 3).

Считаются только отправки. ``getUpdates`` и всё, что читает, счётом не
ограничено, и занимать им место в окне значило бы придерживать ответы игрокам
ради опроса, который никому ничего не должен.

Окно скользящее и общее: у Telegram счёт один на бота. Часы и сон — параметры,
поэтому тест двигает время, а не ждёт секунду.

Что осталось за пределом этого модуля, названо честно: у групп счёт свой
(двадцать сообщений в минуту на группу), и его держит не очередь, а повтор после
``TelegramRetryAfter`` (``middlewares/retry.py``). Игра пишет в группу по одному
сообщению на команду и в этот счёт не упирается.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods.base import TelegramMethod, TelegramType

from mmorpg.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from aiogram import Bot
    from aiogram.methods import Response

logger = get_logger(__name__)

#: Сколько отправок в секунду Telegram принимает от одного бота.
SENDS_PER_SECOND = 30
WINDOW_SECONDS = 1.0

#: Ждать дольше этого нет смысла: столько ждущих отправок означает, что игра
#: упёрлась в счёт Telegram всерьёз, и об этом надо сказать в журнал, а не
#: молча растянуть очередь.
CROWDED = SENDS_PER_SECOND


def is_send(method: TelegramMethod[TelegramType]) -> bool:
    """Тратит ли этот вызов место в окне.

    По имени, а не по списку классов: новый метод отправки в aiogram появляется
    раньше, чем список успевают дописать, а пропущенная отправка — это ``429``
    у игрока.
    """
    name = type(method).__name__
    return name.startswith(("Send", "Forward", "Copy"))


class SendWindow:
    """Скользящее окно отправок. Ничего не знает ни о Telegram, ни о aiogram."""

    def __init__(
        self,
        limit: int = SENDS_PER_SECOND,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._sent: deque[float] = deque()
        # Одна отправка за раз проходит через ворота: без этого два всплеска,
        # сошедшихся на одном окне, посчитали бы одно и то же место дважды.
        self._gate = asyncio.Lock()

    @property
    def waiting(self) -> int:
        """Сколько отправок уже стоит в текущем окне."""
        return len(self._sent)

    async def take(self) -> float:
        """Занять место в окне, дождавшись, если мест нет. Возвращает, сколько ждали."""
        async with self._gate:
            waited = 0.0
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self._window:
                    self._sent.popleft()
                if len(self._sent) < self._limit:
                    self._sent.append(now)
                    return waited
                pause = self._window - (now - self._sent[0])
                waited += pause
                await self._sleep(pause)


class SendRateMiddleware(BaseRequestMiddleware):
    """Придерживает отправку, пока в окне Telegram нет места."""

    def __init__(self, window: SendWindow | None = None) -> None:
        self._window = window or SendWindow()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if is_send(method):
            crowded = self._window.waiting >= CROWDED
            waited = await self._window.take()
            if crowded and waited:
                logger.warning(
                    "telegram_send_queued",
                    method=type(method).__name__,
                    waited=round(waited, 3),
                )
        return await make_request(bot, method)
