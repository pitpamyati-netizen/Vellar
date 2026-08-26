"""Что делает работающая игра, числами, которые читает оператор.

Сердцебиение (``mmorpg.health``) отвечает на «жива ли». Это отвечает на
следующий вопрос: «и здорова ли». Строка в минуту, в том же журнале, куда
пишется всё остальное, и в ней четыре числа, говорящие, обслуживают игроков или
держат в ожидании:

    metrics updates=412 failures=0 p50=0.014 p95=0.061 slowest=0.22

Минута, в которую никто ничего не нажал, не говорит ни о чём и не пишется: на
то, что тихая игра всё ещё жива, отвечает сердцебиение (``mmorpg.health``).

Задержка держится в постоянных корзинах, а не списком замеров: сотня игроков,
жмущих кнопки весь день, - это миллионы чисел, а спрашивают у них всегда одно:
«в какую корзину попадает девяносто пятый». Поэтому цена - горсть целых чисел
при любом потоке, а проценты честно называются краями корзин, а не точными
числами.

Окно сбрасывается после каждого отчёта нарочно. Среднее за сутки прячет те
десять минут, когда база задыхалась; минута чисел - нет.

Здесь ничего не решается: ни одно правило это не читает, ни один экран этого не
показывает. На это смотрит тревога о затихшей игре, и это печатает нагрузочный
тест (``scripts/loadtest.py``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from mmorpg.logging import get_logger

logger = get_logger(__name__)

#: Потолки корзин в секундах. Бюджет p95 одного обновления - 100 мс
#: (``docs/architecture.md``), поэтому вокруг него корзины частые, а выше - редкие:
#: после секунды точное число уже не меняет того, что с этим делают.
BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass(slots=True)
class Metrics:
    """Одно окно счётчиков. Дёшево обновлять, дёшево держать."""

    updates: int = 0
    failures: int = 0
    slowest: float = 0.0
    #: По счётчику на корзину плюс последний - на всё, что выше потолка.
    counts: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS) + 1))

    def observe(self, seconds: float, *, failed: bool = False) -> None:
        """Записать одно обслуженное обновление."""
        self.updates += 1
        if failed:
            self.failures += 1
        self.slowest = max(self.slowest, seconds)
        self.counts[_bucket_of(seconds)] += 1

    def failed(self) -> None:
        """Записать отказ, посчитанный там, где поймали исключение, а не замеренный."""
        self.failures += 1

    def quantile(self, share: float) -> float:
        """Потолок корзины, под который укладывается названная доля обновлений.

        Ноль, пока не замерено ничего, и потолок последней корзины для всего, что
        медленнее любой корзины, - сказанный прямо, а не выдуманным числом.

        Никогда не выше самого медленного обслуженного обновления. Потолок корзины -
        это верхняя граница, а самый медленный замер - граница более тесная: без этого
        окно, худшее обновление которого заняло 266 мс, отчитывалось ``p95=0.5``, а
        читается это как полсекунды ожидания, которого не было.
        """
        if not self.updates:
            return 0.0
        wanted = share * self.updates
        seen = 0
        for index, count in enumerate(self.counts):
            seen += count
            if seen >= wanted:
                ceiling = BUCKETS[index] if index < len(BUCKETS) else BUCKETS[-1]
                return round(min(ceiling, self.slowest), 4)
        return BUCKETS[-1]  # pragma: no cover - цикл всегда добирается до доли

    def snapshot(self) -> Mapping[str, float | int]:
        """Окно в том виде, в каком оно попадёт в журнал."""
        return {
            "updates": self.updates,
            "failures": self.failures,
            "p50": self.quantile(0.5),
            "p95": self.quantile(0.95),
            "slowest": round(self.slowest, 4),
        }

    def reset(self) -> None:
        self.updates = 0
        self.failures = 0
        self.slowest = 0.0
        self.counts = [0] * (len(BUCKETS) + 1)


def _bucket_of(seconds: float) -> int:
    for index, ceiling in enumerate(BUCKETS):
        if seconds <= ceiling:
            return index
    return len(BUCKETS)


def report(metrics: Metrics) -> None:
    """Записать одно окно и начать следующее.

    Окно, в котором ничего не обслужили, не пишется. Раньше писалось - на том
    основании, что «минуту никто ничего не нажимал» это новость о живой игре, - но
    живость всегда была делом сердцебиения: ``scripts/watchdog.py`` читает возраст
    файла сердцебиения и на эту строку не смотрит вовсе. А делала пустая строка вот
    что: уводила вверх окно тихой игры вместе со всем, ради чего его открыли.
    """
    if not metrics.updates:
        return
    logger.info("metrics", **metrics.snapshot())
    metrics.reset()


async def _tick(metrics: Metrics, interval: float, stop: asyncio.Event) -> None:
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        report(metrics)


@asynccontextmanager
async def reporting(metrics: Metrics, interval: float) -> AsyncIterator[None]:
    """Отчитываться каждые ``interval``, пока выполняется блок.

    Остановка - событие, а не отмена, и по той же причине, что в ``mmorpg.health``:
    последнее окно пишет сам цикл, по дороге наружу, а не теряет вместе с задачей.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(_tick(metrics, interval, stop), name="metrics")
    logger.info("metrics_started", seconds=interval)
    try:
        yield
    finally:
        stop.set()
        with suppress(asyncio.CancelledError):
            await task
        report(metrics)
        logger.info("metrics_stopped")


class Stopwatch:
    """Замерить одно дело, не импортируя ``time`` в каждом месте вызова."""

    __slots__ = ("_started",)

    def __init__(self) -> None:
        self._started = time.perf_counter()

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self._started
