"""Не отправлять быстрее, чем Telegram принимает.

У Telegram счёт двойной: около тридцати сообщений в секунду на бота целиком и
примерно одно в секунду в один и тот же диалог. Превышение любого - не ошибка
сети, а ответ ``429`` с просьбой подождать, и для того, кто слушает экран, он
неотличим от сломанной игры (``docs/accessibility.md``, правило 3).

Второй счёт и ловит игрока, барабанящего по кнопкам боя, поэтому здесь два
окна: общее (``SendWindow``) и по чату (``PerChatWindow``). Всплеск в несколько
сообщений окно по чату впитывает; барабанную дробь растягивает до одного ответа
в секунду.

Очередь стоит перед отправкой, а не после отказа: пауза, растянутая по всплеску,
никем не замечается, а ``429`` - это ответ, который не пришёл.

Считаются только отправки: ``getUpdates`` и всё, что читает, счётом не
ограничено. Окна скользящие, часы и сон - параметры, поэтому тест двигает время,
а не ждёт секунду.

У групп счёт свой (двадцать сообщений в минуту), и его держит не очередь, а
повтор после ``TelegramRetryAfter`` (``middlewares/retry.py``).
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

#: Сколько сообщений подряд Telegram принимает в один частный чат и за какой
#: срок. Три за три секунды — это устойчивый один ответ в секунду плюс запас на
#: всплеск: экран, а следом весть о взятом уровне (``screens/play``).
SENDS_PER_CHAT = 3
PER_CHAT_WINDOW_SECONDS = 3.0

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


def chat_of(method: TelegramMethod[TelegramType]) -> int | None:
    """Номер частного чата, куда идёт отправка, или ``None``.

    Счёт по чату держится только для частных диалогов: у групп и каналов он свой
    (``middlewares/throttle.py``, ``middlewares/retry.py``), а строковый
    ``@username`` вместо номера — это канал.
    """
    chat = getattr(method, "chat_id", None)
    # Номер частного чата положителен и равен номеру игрока; у групп и каналов
    # он отрицателен, а канал бывает и строкой ``@username``.
    return chat if isinstance(chat, int) and chat > 0 else None


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


class PerChatWindow:
    """Скользящее окно на каждый частный чат.

    Держит по окну на чат и по замку на чат: пока один игрок барабанит по
    кнопкам и его ответы растянуты до одного в секунду, чужие чаты идут своим
    ходом. Чаты, замолчавшие на целое окно, забываются, иначе бот держал бы
    очередь на каждый когда-либо виденный диалог (ср. ``throttle._sweep``).
    """

    def __init__(
        self,
        limit: int = SENDS_PER_CHAT,
        window: float = PER_CHAT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._sent: dict[int, deque[float]] = {}
        self._gates: dict[int, asyncio.Lock] = {}
        self._swept_at = 0.0

    def waiting(self, chat: int) -> int:
        return len(self._sent.get(chat, ()))

    async def take(self, chat: int) -> float:
        """Занять место в окне этого чата, дождавшись, если мест нет."""
        gate = self._gates.setdefault(chat, asyncio.Lock())
        async with gate:
            sent = self._sent.setdefault(chat, deque())
            waited = 0.0
            while True:
                now = self._clock()
                while sent and now - sent[0] >= self._window:
                    sent.popleft()
                if len(sent) < self._limit:
                    sent.append(now)
                    self._sweep(now)
                    return waited
                pause = self._window - (now - sent[0])
                waited += pause
                await self._sleep(pause)

    def _sweep(self, now: float) -> None:
        if now - self._swept_at < self._window:
            return
        self._swept_at = now
        stale = [
            chat for chat, sent in self._sent.items() if not sent or now - sent[-1] >= self._window
        ]
        for chat in stale:
            self._sent.pop(chat, None)
            gate = self._gates.get(chat)
            if gate is not None and not gate.locked():
                self._gates.pop(chat, None)


class SendRateMiddleware(BaseRequestMiddleware):
    """Придерживает отправку, пока в окне Telegram нет места — общем и по чату."""

    def __init__(
        self, window: SendWindow | None = None, per_chat: PerChatWindow | None = None
    ) -> None:
        self._window = window or SendWindow()
        self._per_chat = per_chat or PerChatWindow()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if is_send(method):
            # Счёт по чату — первым: он растягивает барабанную дробь одного
            # игрока, не занимая мест в общем окне, пока ждёт.
            chat = chat_of(method)
            if chat is not None:
                waited_here = await self._per_chat.take(chat)
                if waited_here:
                    logger.warning(
                        "telegram_chat_queued",
                        method=type(method).__name__,
                        chat=chat,
                        waited=round(waited_here, 3),
                    )
            crowded = self._window.waiting >= CROWDED
            waited = await self._window.take()
            if crowded and waited:
                logger.warning(
                    "telegram_send_queued",
                    method=type(method).__name__,
                    waited=round(waited, 3),
                )
        return await make_request(bot, method)
