"""Как пережидают пропавшую связь.

Под работающей игрой рвутся три разные связи - PostgreSQL, Redis и Telegram, -
и рвутся все три одинаково: вызов, который был в воздухе, когда умер сокет,
возвращается ошибкой, а сама связь секундой позже уже здорова. Правило на все
три одно, поэтому арифметика пауз живёт здесь, в одном месте, а слои, знающие о
сокетах, ею пользуются.

Здесь не решается, *что* можно повторять: это дело вызывающего, потому что
только он знает, изменит ли повтор мир дважды
(``docs/adr/0009-repeating-a-lost-query.md``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mmorpg.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from mmorpg.config import Settings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Сколько раз повторяется пропавший вызов и какие между повторами паузы.

    Паузы удваиваются и перестают расти: первый повтор быстрый, потому что
    большинство обрывов - это упавшее соединение, которое заменяют мгновенно, а
    поздние медленные, потому что база, всё ещё лежащая через секунду, не мигает, а
    перезапускается.
    """

    attempts: int = 5
    delay: float = 0.2
    max_delay: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            attempts=settings.reconnect_attempts,
            delay=settings.reconnect_delay_seconds,
            max_delay=settings.reconnect_max_delay_seconds,
        )

    def pause(self, attempt: int) -> float:
        """Сколько ждать перед повтором номер ``attempt``; первый - единица."""
        return min(self.delay * 2.0 ** (attempt - 1), self.max_delay)


async def keep_trying[T](
    call: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    seconds: float,
    what: str,
    recoverable: Callable[[BaseException], bool],
) -> T:
    """Звать, пока не сработает, или пока не выйдут ``seconds`` ожидания.

    Это путь старта, и он нарочно терпелив, а не сосчитан: стек, который
    поднимается разом, поднимается не по порядку, и PostgreSQL, которому нужно ещё
    пять секунд, чтобы открыть сокет, - не повод боту выходить. Под работающей
    игрой берутся сосчитанные повторы: игрок, ждущий экрана, не должен ждать его
    минуту.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    attempt = 0
    while True:
        try:
            result = await call()
        except Exception as error:
            if not recoverable(error) or loop.time() >= deadline:
                raise
            attempt += 1
            wait = policy.pause(attempt)
            logger.warning(
                "waiting_for_service",
                service=what,
                attempt=attempt,
                wait=wait,
                error=type(error).__name__,
            )
            await asyncio.sleep(wait)
        else:
            if attempt:
                logger.info("service_answered", service=what, attempts=attempt)
            return result
