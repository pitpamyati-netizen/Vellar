"""What the running game is doing, in numbers an operator can read.

The heartbeat (``mmorpg.health``) answers "is it alive". This answers the next
question: "and is it well". One line every minute, in the same log everything
else is written to, carrying the four numbers that say whether players are being
served or being kept waiting:

    metrics updates=412 failures=0 p50=0.014 p95=0.061 slowest=0.22

Latency is kept in fixed buckets rather than as a list of samples: a hundred
players pressing buttons for a day is millions of numbers, and the answer wanted
from them is always "which bucket does the 95th fall into". So the cost is a
handful of integers, whatever the traffic, and the percentiles are honest about
being bucket edges rather than exact.

The window resets after every report on purpose. A running average over a day
hides the ten minutes the database was struggling; a minute of numbers does not.

Nothing here decides anything: no rule reads it, no screen shows it. It is what
the alert on a quiet game looks at, and what the load test prints
(``scripts/loadtest.py``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from mmorpg.logging import get_logger

logger = get_logger(__name__)

#: Bucket ceilings in seconds. The p95 budget of one update is 100 ms
#: (``docs/architecture.md``), so the buckets are dense around it and coarse
#: above: past a second the exact number no longer changes what is done about it.
BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass(slots=True)
class Metrics:
    """One window of counters. Cheap to update, cheap to keep."""

    updates: int = 0
    failures: int = 0
    slowest: float = 0.0
    #: One counter per bucket, plus a last one for everything above the ceiling.
    counts: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS) + 1))

    def observe(self, seconds: float, *, failed: bool = False) -> None:
        """Record one served update."""
        self.updates += 1
        if failed:
            self.failures += 1
        self.slowest = max(self.slowest, seconds)
        self.counts[_bucket_of(seconds)] += 1

    def failed(self) -> None:
        """Record a failure counted where the exception was caught, not timed."""
        self.failures += 1

    def quantile(self, share: float) -> float:
        """The bucket ceiling the given share of updates fits under.

        Zero when nothing has been observed yet, and the ceiling of the last
        bucket for anything slower than every bucket - said plainly rather than
        as an invented number.
        """
        if not self.updates:
            return 0.0
        wanted = share * self.updates
        seen = 0
        for index, count in enumerate(self.counts):
            seen += count
            if seen >= wanted:
                return BUCKETS[index] if index < len(BUCKETS) else BUCKETS[-1]
        return BUCKETS[-1]  # pragma: no cover - the loop always reaches the share

    def snapshot(self) -> Mapping[str, float | int]:
        """The window as it would be logged."""
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
    """Write one window down and start the next.

    An empty window is written too: "nobody pressed anything for a minute" is
    news about a live game, and a line that stops appearing is how the watchdog
    tells a wedged loop from a quiet one.
    """
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
    """Report every ``interval`` for as long as the block runs.

    Stopping is an event and not a cancellation, for the same reason as in
    ``mmorpg.health``: the last window is written by the loop itself, on the way
    out, instead of being lost with the task.
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
    """Time one thing without importing ``time`` at every call site."""

    __slots__ = ("_started",)

    def __init__(self) -> None:
        self._started = time.perf_counter()

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self._started
