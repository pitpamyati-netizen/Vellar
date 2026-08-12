"""Liveness heartbeat.

A restart policy handles a process that dies. It cannot see a process whose event
loop is wedged - a blocking call, a deadlock, a socket that never times out - and
such a container stays "up" while every player waits in silence. That is the
failure mode this module exists for.

So the loop proves it is alive by doing something only a running loop can do: a
background task touches a file every ``heartbeat_seconds``. ``scripts/healthcheck.py``
reads the file's age, the container is declared unhealthy once the beat stops,
and the restart policy takes it from there.

The file is written from a worker thread. It is 30 bytes and the beat is slow, but
the p95 budget of 100 ms per update (``docs/architecture.md``) leaves no room for
surprise file I/O on the loop - a container volume can stall for far longer than
the write itself costs.
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
    """Record "the loop was alive just now" as the file's modification time.

    The contents are for a human reading the file over ``docker exec``; the probe
    only looks at the timestamp, so a partially written file cannot mislead it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def age_seconds(path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the last beat, or ``None`` when there has never been one."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - modified)


def is_alive(settings: Settings, *, now: float | None = None) -> bool:
    """Whether the heartbeat is recent enough to call the bot healthy."""
    age = age_seconds(settings.heartbeat_path, now=now)
    return age is not None and age <= settings.heartbeat_stale_after


async def _beat(path: Path, interval: float) -> None:
    """Touch the file forever, until cancelled."""
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(touch, path)


@asynccontextmanager
async def heartbeat(settings: Settings) -> AsyncIterator[None]:
    """Beat for as long as the block is running.

    The first beat lands before the block is entered, so the probe has something
    to read from the moment the bot starts serving.
    """
    path = settings.heartbeat_path
    await asyncio.to_thread(touch, path)
    task = asyncio.create_task(_beat(path, settings.heartbeat_seconds), name="heartbeat")
    logger.info("heartbeat_started", path=str(path), seconds=settings.heartbeat_seconds)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # A file left behind by a clean shutdown would make the next start look
        # wedged for its first three beats.
        with suppress(OSError):
            path.unlink()
        logger.info("heartbeat_stopped")
