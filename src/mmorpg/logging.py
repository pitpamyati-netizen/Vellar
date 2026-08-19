"""structlog configuration, and where the lines end up.

Console renderer in development, JSON in production. Configured once at startup.

Everything is still written to stdout, because that is what the log driver of a
container collects and what a solo run shows in its window. Beside it, when
``LOG_DIR`` is set - and it is by default - the same lines go to two files,
because a window that scrolls away is not a record of anything:

- ``vellar.log``    - all of it, rolled over at midnight and swept after
  ``LOG_RETENTION_DAYS`` days. This is the busy half: every button pressed, every
  metrics line, every screen served.
- ``important.log`` - the half that must survive that sweep: warnings, errors and
  tracebacks, every movement of gold, every account turned away, and every start
  and stop of the game. Kept for ``LOG_IMPORTANT_RETENTION_DAYS`` days, and ``0``
  - the default - means it is never deleted at all.

That split is the whole point of the automatic cleanup. Old chatter is worth
deleting; a failure is worth reading a year later, and a cleanup that cannot tell
them apart is one that eventually erases the evidence. So importance is decided
once, here, and the sweep only ever touches the file it is allowed to.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import structlog

from mmorpg.config import AppEnv, Settings

#: Everything the game said, and the part of it worth keeping.
ACTIVITY_FILE = "vellar.log"
IMPORTANT_FILE = "important.log"

#: Events kept regardless of their level. All of them answer a question asked
#: long after the fact: where the gold went (``gold_flow``, the ledger
#: ``scripts/economy.py`` reads), who was turned away and when, and when the game
#: was actually up - the first thing anyone checks against "it was broken all
#: evening".
KEPT_EVENTS = frozenset(
    {
        "gold_flow",
        "build",
        "connected",
        "shutdown",
        "shutdown_requested",
        "postgres_recovered",
        "telegram_recovered",
    }
)

#: Outcomes of a player action (``middlewares.audit``) that are kept as well. A
#: duplicate update and an unanswered button are noise; a crash and a locked door
#: are not.
KEPT_RESULTS = frozenset({"failed", "banned"})

SECONDS_IN_DAY = 86_400

#: Библиотечные журналы, которые говорят то же самое, что уже сказано своими
#: словами. ``aiogram.event`` пишет строку на каждое обновление ("Update id=...
#: is handled. Duration 163 ms"), а рядом с ней стоит наша же строка ``action``
#: с тем же временем, но ещё и с тем, кто и что нажал (``middlewares.audit``).
#: Две строки на нажатие - это журнал, в котором не видно игры.
QUIET_LOGGERS: Mapping[str, int] = MappingProxyType({"aiogram.event": logging.WARNING})


def configure_logging(settings: Settings) -> None:
    """Configure structlog, the stdlib bridge, and the log files."""
    level = getattr(logging, settings.log_level)
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    use_json = settings.log_json or settings.app_env is AppEnv.PROD
    root = logging.getLogger()
    _drop_our_handlers(root)
    # Never above INFO, whatever the level asked for: the ledger and the failed
    # actions are INFO lines, and a root that dropped them would leave the
    # important file empty on exactly the run somebody turned the noise down.
    # What the level does is decide what the console and the everyday file show.
    root.setLevel(min(level, logging.INFO))

    for name, quiet_at in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_at)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_formatter(shared, json=use_json, colors=not use_json))
    root.addHandler(console)

    directory = _prepare(settings.log_path)
    if directory is None:
        return

    # A run that starts after a week away sweeps before it writes: the handlers
    # below only delete on their own rollover, which is a midnight that may be a
    # day off.
    swept = sweep(directory, days=settings.log_retention_days, stem=ACTIVITY_FILE)
    swept += sweep(directory, days=settings.log_important_retention_days, stem=IMPORTANT_FILE)

    activity = _rotating(directory / ACTIVITY_FILE, keep=settings.log_retention_days)
    activity.setLevel(level)
    activity.setFormatter(_formatter(shared, json=use_json, colors=False))
    root.addHandler(activity)

    important = _rotating(directory / IMPORTANT_FILE, keep=settings.log_important_retention_days)
    # DEBUG, not ``level``: what is kept is decided by the filter below, and a
    # game running at WARNING must still keep its gold ledger.
    important.setLevel(logging.DEBUG)
    important.addFilter(_ImportantOnly())
    important.setFormatter(_formatter(shared, json=use_json, colors=False))
    root.addHandler(important)

    get_logger(__name__).info(
        "log_files",
        directory=str(directory),
        keep_days=settings.log_retention_days,
        keep_important_days=settings.log_important_retention_days or "forever",
        swept=swept,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for the given module name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def is_important(event: object, level: int) -> bool:
    """Whether this line outlives the cleanup.

    Takes the event dictionary a structlog line carries, or anything else for a
    line that reached the bridge from a library - where only the level is known,
    which is exactly the case the level rule is for.
    """
    if level >= logging.WARNING:
        return True
    if not isinstance(event, dict):
        return False
    return event.get("event") in KEPT_EVENTS or event.get("result") in KEPT_RESULTS


def sweep(directory: Path, *, days: int, stem: str, now: float | None = None) -> int:
    """Delete rolled-over copies of ``stem`` older than ``days``; ``0`` deletes none.

    Only the rollovers (``vellar.log.2026-08-12``) are considered: the file being
    written to has no age worth reading, and a stem the caller did not name is
    never touched - that is what keeps a sweep of the everyday log away from the
    important one.
    """
    if days <= 0 or not directory.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - days * SECONDS_IN_DAY
    removed = 0
    for path in sorted(directory.glob(f"{stem}.*")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # pragma: no cover - a file somebody else holds open
            continue
    return removed


class _ImportantOnly(logging.Filter):
    """Passes only what the cleanup is not allowed to lose."""

    def filter(self, record: logging.LogRecord) -> bool:
        return is_important(record.msg, record.levelno)


def _formatter(
    shared: list[structlog.typing.Processor], *, json: bool, colors: bool
) -> logging.Formatter:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=colors)
    )
    return structlog.stdlib.ProcessorFormatter(processor=renderer, foreign_pre_chain=shared)


def _rotating(path: Path, *, keep: int) -> logging.handlers.TimedRotatingFileHandler:
    """One file a day. ``keep=0`` rolls over and deletes nothing, ever."""
    return logging.handlers.TimedRotatingFileHandler(
        path,
        when="midnight",
        utc=True,
        backupCount=keep,
        encoding="utf-8",
        delay=True,
    )


def _prepare(directory: Path | None) -> Path | None:
    """Make the log directory, or answer ``None`` and keep going on stdout.

    A tree that cannot be written to is not a reason to refuse to serve players:
    the container runs as a user that owns nothing under ``/app``, and there the
    log belongs to the daemon collecting stdout anyway.
    """
    if directory is None:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        get_logger(__name__).warning("log_files_unavailable", path=str(directory), error=str(error))
        return None
    return directory


def _drop_our_handlers(root: logging.Logger) -> None:
    """Undo a previous configuration, closing the files it had open.

    Configuring twice happens in tests and in a reload; on Windows a file left
    open cannot be rotated or deleted by the run that comes after.
    """
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.close()
