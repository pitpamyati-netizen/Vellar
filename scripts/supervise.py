"""Внешний процесс завершает зависшего бота и запускает его снова."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from mmorpg.config import load_settings
from mmorpg.health import is_alive


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if stream.read(1) == b"":
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def stop_child(child: subprocess.Popen[bytes], *, timeout: float = 20) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def supervise(
    command: Sequence[str] | None = None,
    *,
    startup: float = 180,
    stop_requested: Callable[[], bool] | None = None,
) -> int:
    settings = load_settings()
    stopped = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with exclusive(settings.heartbeat_path.with_suffix(".supervisor.lock")):
        if is_alive(settings):
            raise RuntimeError("Другая копия бота уже работает")
        delay = 1.0
        while not stopped and not (stop_requested and stop_requested()):
            started = time.monotonic()
            child = subprocess.Popen(command or [sys.executable, "-m", "mmorpg.main"])
            ready = False
            try:
                while not stopped and child.poll() is None:
                    if stop_requested and stop_requested():
                        stopped = True
                        break
                    alive = is_alive(settings)
                    ready = ready or alive
                    if not alive and (ready or time.monotonic() - started > startup):
                        print(
                            "Vellar: обработка или доставка остановилась; перезапуск.", flush=True
                        )
                        break
                    time.sleep(min(1.0, settings.heartbeat_seconds))
            finally:
                stop_child(child)
                settings.heartbeat_path.unlink(missing_ok=True)
            if stopped:
                break
            if ready and time.monotonic() - started > startup:
                delay = 1.0
            deadline = time.monotonic() + delay
            while not stopped and time.monotonic() < deadline:
                time.sleep(0.2)
            delay = min(60.0, delay * 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(supervise())
