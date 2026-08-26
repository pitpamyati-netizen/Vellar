"""Счёт и замер каждого обновления.

Самая внешняя мидлварь из всех: она меряет полную цену обновления такой, какой
её чувствует игрок, - проверку на повтор, хранилища, флоу, ответ, - а не ту
часть, за которую случайно отвечает хендлер.

Она меряет и больше ничего. Исключение проходит сквозь неё нетронутым, потому
что ответ на сломанное обновление - дело ``ErrorMiddleware``, а мидлварь,
проглотившая его ради опрятных счётчиков, превратила бы падение в тишину.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from mmorpg.metrics import Metrics, Stopwatch


class MetricsMiddleware(BaseMiddleware):
    """Записывает, сколько заняло каждое обновление и не взорвалось ли оно."""

    def __init__(self, metrics: Metrics) -> None:
        self._metrics = metrics

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        watch = Stopwatch()
        failed = False
        try:
            return await handler(event, data)
        except Exception:
            failed = True
            raise
        finally:
            self._metrics.observe(watch.seconds, failed=failed)
