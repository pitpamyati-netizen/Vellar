"""Удаление собственных сообщений бота в группе через пять минут.

Группа принадлежит тем, кто в ней разговаривает. Бот, оставляющий каждый свой
ответ на месте, превращает разговор в протокол, а для того, кто читает на слух,
такой протокол хуже беспорядка: прокрутить его назад стоит настоящего времени.
Поэтому сообщение в группе временное - его доставили, прочитали и убрали
(``Narrative.md``, раздел 9).

**Только в группе.** В личной переписке не удаляется ничто: там сообщение *и
есть* экран, и игрок перечитывает его столько, сколько нужно
(``docs/accessibility.md``, правило 3).

Удаление - фоновая задача, результата которой никто не ждёт. Трогать обновление,
её породившее, ей нельзя ни при каких обстоятельствах: сообщение, удалённое
раньше модератором, отобранное право администратора, сбой сети - всё это тихо
отбрасывается, ровно как несостоявшийся пост в канал.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mmorpg.logging import get_logger

logger = get_logger(__name__)

# Те же пять минут, что живёт предложение, и по той же причине: столько сообщение
# остаётся достойным чтения.
GROUP_MESSAGE_TTL_SECONDS = 300.0

Deleter = Callable[[int, int], Awaitable[object]]


@dataclass(slots=True)
class MessageReaper:
    """Откладывает удаления и владеет задачами, которые их выполняют."""

    delay: float = GROUP_MESSAGE_TTL_SECONDS
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def schedule(self, deleter: Deleter, chat_id: int, message_id: int) -> None:
        """Удалить одно сообщение, когда пройдёт отсрочка."""
        task = asyncio.create_task(self._reap(deleter, chat_id, message_id))
        # asyncio держит на работающую задачу только слабую ссылку, поэтому множество,
        # живущее дольше вызова, - это разница между удалением и потерянным удалением.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reap(self, deleter: Deleter, chat_id: int, message_id: int) -> None:
        try:
            await asyncio.sleep(self.delay)
            await deleter(chat_id, message_id)
        except asyncio.CancelledError:
            raise
        # Широко нарочно: неудаляемое сообщение - это неопрятно, но не смертельно.
        except Exception as error:
            logger.debug("group_message_not_deleted", message_id=message_id, error=str(error))

    async def aclose(self) -> None:
        """Отменить всё, что ещё ждёт. Зовётся из стека остановки."""
        pending = tuple(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
