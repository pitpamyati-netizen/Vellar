"""Перила задержки.

У бота бюджет p95 - 100 мс на обновление. Всё, что блокирует цикл событий:
синхронный HTTP-вызов, файловый ввод-вывод на ходу, тяжёлый цикл, - всплывает
здесь предупреждением о медленном колбэке, а не игроком, ждущим в тишине.
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
    """Попросить asyncio жаловаться на колбэки, занимающие цикл.

    ``slow_callback_duration`` работает только в режиме отладки, поэтому ставятся
    оба сразу. Режим отладки к тому же проставляет время каждому колбэку и держит
    живыми истоки корутин, а это уместная цена, пока играет один разработчик, и
    неуместная - при сотне подключённых игроков. Отсюда и переключатель, и его
    значение по умолчанию: включено там, где игру пишут, выключено там, где в неё
    играют (``Settings.watching_slow_callbacks``).
    """
    if not settings.watching_slow_callbacks:
        logger.info("slow_callback_detector_disabled", env=settings.app_env.value)
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
    """Записать в журнал любое дело, вышедшее за свой бюджет."""
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
    """Тот же :func:`measure`, но декоратором и для синхронной работы."""

    def decorate(function: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            with measure(operation, budget_seconds):
                return function(*args, **kwargs)

        wrapper.__name__ = function.__name__
        wrapper.__doc__ = function.__doc__
        return wrapper

    return decorate
