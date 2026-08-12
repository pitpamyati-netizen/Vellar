"""Latency guard rails.

The bot has a p95 budget of 100 ms per update. Anything that blocks the event
loop - a synchronous HTTP call, file I/O at runtime, a heavy loop - shows up here
as a slow callback warning rather than as a player waiting in silence.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from mmorpg.config import AppEnv, Settings
from mmorpg.logging import get_logger

logger = get_logger(__name__)


def install_slow_callback_detector(loop: asyncio.AbstractEventLoop, settings: Settings) -> None:
    """Ask asyncio to complain about callbacks that hog the loop.

    ``slow_callback_duration`` only takes effect in debug mode, so both are set
    together. Debug mode also timestamps every callback and keeps coroutine
    origins alive, which is a fine price while one developer is playing and the
    wrong price with a hundred players connected - hence the switch.
    """
    if not settings.slow_callback_detector:
        logger.info("slow_callback_detector_disabled")
        return

    if settings.app_env is not AppEnv.LOCAL:
        logger.warning(
            "slow_callback_detector_enabled_outside_local",
            env=settings.app_env.value,
            detail="asyncio debug mode costs throughput; set SLOW_CALLBACK_DETECTOR=false",
        )

    loop.set_debug(True)
    loop.slow_callback_duration = settings.slow_callback_seconds
    logger.info(
        "slow_callback_detector_installed",
        threshold_seconds=settings.slow_callback_seconds,
    )


@contextmanager
def measure(operation: str, budget_seconds: float = 0.1) -> Iterator[None]:
    """Log any operation that overruns its budget."""
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        if elapsed > budget_seconds:
            logger.warning(
                "slow_operation",
                operation=operation,
                seconds=round(elapsed, 4),
                budget=budget_seconds,
            )


def timed(
    operation: str, budget_seconds: float = 0.1
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Decorator form of :func:`measure` for synchronous work."""

    def decorate(function: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            with measure(operation, budget_seconds):
                return function(*args, **kwargs)

        wrapper.__name__ = function.__name__
        wrapper.__doc__ = function.__doc__
        return wrapper

    return decorate
