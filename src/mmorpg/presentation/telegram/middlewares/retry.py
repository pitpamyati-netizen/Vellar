"""Ответ, который не ушёл, отправленный заново.

Действие игрока порождает ровно одно сообщение, и это сообщение *и есть* ответ:
тот, кто слушает экранный диктор и не получил ничего, никак не отличит
оборванный сокет от игры, которая его не заметила. Поэтому запрос, умерший по
дороге в Telegram, делается заново, когда связь вернулась, а не превращается в
тишину и извинение.

Повторяется только пропавшая связь. Всё, на что Telegram ответил, - неверный
запрос, игрок, заблокировавший бота, неверный токен - это ответ, а не обрыв, и
повтор дал бы тот же ответ медленнее.

Цена честная, и её стоит назвать: запрос, дошедший до Telegram и потерявший
только ответ на обратном пути, отправляется дважды, поэтому игрок может увидеть
один экран дважды. Для незрячего повторённый экран - шум, а тишина - тупик.

``getUpdates`` нарочно оставлен в покое: у цикла опроса aiogram есть собственное
бесконечное отступление вокруг него, и два слоя ожидания лишь замедлили бы
первый ответ после простоя.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import (
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import GetUpdates
from aiogram.methods.base import TelegramMethod, TelegramType

from mmorpg.logging import get_logger
from mmorpg.retry import RetryPolicy

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from aiogram import Bot
    from aiogram.methods import Response

logger = get_logger(__name__)


class RetryRequestMiddleware(BaseRequestMiddleware):
    """Повторить вызов Telegram, потерявший соединение."""

    def __init__(self, policy: RetryPolicy) -> None:
        self._policy = policy

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        # ``getUpdates`` оставлен собственному бесконечному отступлению aiogram, см.
        # докстринг модуля. Читается как признак, а не как ранний выход: запрос здесь
        # всё равно делается, просто не делается дважды.
        polling = isinstance(method, GetUpdates)
        name = type(method).__name__
        repeats = 0
        while True:
            try:
                answer = await make_request(bot, method)
            except Exception as error:
                wait = None if polling else self._wait_after(error)
                if wait is None:
                    raise
                if repeats >= self._policy.attempts:
                    logger.error(
                        "telegram_call_lost",
                        method=name,
                        repeats=repeats,
                        error=type(error).__name__,
                    )
                    raise
                repeats += 1
                pause = max(wait, self._policy.pause(repeats))
                logger.warning(
                    "telegram_repeating",
                    method=name,
                    attempt=repeats,
                    wait=pause,
                    error=type(error).__name__,
                )
                await asyncio.sleep(pause)
            else:
                if repeats:
                    logger.info("telegram_recovered", method=name, repeats=repeats)
                return answer

    def _wait_after(self, error: BaseException) -> float | None:
        """Сколько ждать перед повтором или ``None`` - «не повторять»."""
        if isinstance(error, TelegramRetryAfter):
            # Защита от наплыва - не обрыв связи, но это запрос, который придётся
            # сделать заново. Короткое ожидание впитывается здесь; долгое - дело того,
            # кто столько шлёт, поэтому о нём сообщают, а не пересыпают его, пока на том
            # конце ждёт игрок.
            wait = float(error.retry_after)
            return wait if wait <= self._policy.max_delay else None
        if isinstance(error, TelegramEntityTooLarge):
            # Наследник сетевой ошибки, но постоянный: сообщение слишком велико и будет
            # слишком велико и в следующий раз.
            return None
        if isinstance(error, TelegramNetworkError | TelegramServerError):
            return 0.0
        return None
