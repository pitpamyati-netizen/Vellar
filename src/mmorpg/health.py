"""Сердцебиение: жив ли цикл событий.

С процессом, который умер, разберётся политика перезапуска. Процесс, у которого
встал цикл событий, она не видит - блокирующий вызов, взаимная блокировка,
сокет без срока, - и такой контейнер считается «поднятым», пока каждый игрок
ждёт в тишине. Ради этого отказа модуль и написан.

Поэтому цикл доказывает, что он жив, делая то, что может сделать только живой
цикл: фоновая задача трогает файл каждые ``heartbeat_seconds``.
``scripts/healthcheck.py`` читает возраст файла, контейнер объявляется
нездоровым, как только удары прекратились, а дальше дело политики перезапуска.

Файл пишется из рабочего потока. В нём 30 байт, и удары редки, но бюджет p95 в
100 мс на обновление (``docs/architecture.md``) не оставляет места
неожиданному файловому вводу-выводу в цикле: том контейнера умеет вставать
намного дольше, чем стоит сама запись.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from mmorpg.config import Settings
from mmorpg.logging import get_logger

logger = get_logger(__name__)


def touch(path: Path) -> None:
    """Записать «цикл был жив только что» временем изменения файла.

    Содержимое - для человека, который читает файл через ``docker exec``; проверка
    смотрит только на отметку времени, поэтому недописанный файл её не обманет.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def age_seconds(path: Path, *, now: float | None = None) -> float | None:
    """Сколько секунд прошло с последнего удара, или ``None``, если ударов не было."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - modified)


def is_alive(settings: Settings, *, now: float | None = None) -> bool:
    """Достаточно ли свежо сердцебиение, чтобы считать бота здоровым."""
    age = age_seconds(settings.heartbeat_path, now=now)
    return age is not None and age <= settings.heartbeat_stale_after


async def _beat(path: Path, interval: float, stop: asyncio.Event) -> None:
    """Трогать файл каждые ``interval``, пока не попросят остановиться.

    Остановка - это событие, а не отмена задачи, и нарочно: отмена не дотягивается
    до рабочего потока, поэтому отменённый удар успевает положить свою запись
    *после* того, как остановка удалила файл, - и оставить свежее сердцебиение
    процессу, которого больше нет.
    """
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        await asyncio.to_thread(touch, path)


@asynccontextmanager
async def heartbeat(settings: Settings) -> AsyncIterator[None]:
    """Биться, пока выполняется блок.

    Первый удар ложится до входа в блок, чтобы проверке было что читать с той
    минуты, как бот начал обслуживать игроков.
    """
    path = settings.heartbeat_path
    await asyncio.to_thread(touch, path)
    stop = asyncio.Event()
    task = asyncio.create_task(_beat(path, settings.heartbeat_seconds, stop), name="heartbeat")
    logger.info("heartbeat_started", path=str(path), seconds=settings.heartbeat_seconds)
    try:
        yield
    finally:
        # Сначала попросить, потом дождаться: удар возвращается сразу, и запись, уже
        # ушедшая в полёт, успевает лечь до того, как файл ниже будет удалён.
        stop.set()
        with suppress(asyncio.CancelledError):
            await task
        # Файл, оставшийся после чистой остановки, заставил бы следующий старт три удара
        # подряд выглядеть вставшим.
        with suppress(OSError):
            path.unlink()
        logger.info("heartbeat_stopped")
