"""Настройка structlog и то, куда ложатся строки.

В разработке - консольный вывод, в бою - JSON. Настраивается один раз на
старте.

Всё пишется в stdout - это собирает драйвер журнала контейнера и это же
показывает solo-запуск в своём окне. Рядом, когда задан ``LOG_DIR`` (а он задан
по умолчанию), те же строки ложатся в два файла:

- ``vellar.log`` - всё подряд, с переворотом в полночь и уборкой через
  ``LOG_RETENTION_DAYS`` дней. Шумная половина: каждая нажатая кнопка, каждая
  строка метрик, каждый показанный экран.
- ``important.log`` - половина, которая обязана эту уборку пережить:
  предупреждения, отказы и трассировки, каждое движение золота, каждый закрытый
  аккаунт, каждый старт и каждая остановка. Держится
  ``LOG_IMPORTANT_RETENTION_DAYS`` дней, а ``0`` (значение по умолчанию) -
  вовсе не удаляется.

В этом делении весь смысл автоочистки: важность решается один раз, здесь, а
уборка трогает только тот файл, который ей разрешено трогать.
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

#: Всё, что игра сказала, и та часть сказанного, которую стоит сохранить.
ACTIVITY_FILE = "vellar.log"
IMPORTANT_FILE = "important.log"

#: События, которые держатся независимо от уровня. Все они отвечают на вопрос,
#: заданный много позже: куда ушло золото (``gold_flow``, книга для
#: ``scripts/economy.py``), кого и когда закрыли и когда игра работала.
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

#: Исходы действия игрока (``middlewares.audit``), которые держатся тоже. Повторное
#: обновление и неотвеченная кнопка - шум, а падение и закрытая дверь - нет.
KEPT_RESULTS = frozenset({"failed", "banned"})

SECONDS_IN_DAY = 86_400

#: Библиотечные журналы, которые говорят то же, что уже сказано своими словами.
#: ``aiogram.event`` пишет строку на каждое обновление, а рядом стоит наша
#: ``action`` с тем же временем и с тем, кто и что нажал
#: (``middlewares.audit``). Две строки на нажатие - журнал, в котором не видно
#: игры.
QUIET_LOGGERS: Mapping[str, int] = MappingProxyType({"aiogram.event": logging.WARNING})


def configure_logging(settings: Settings) -> None:
    """Настроить structlog, мост к стандартному журналу и файлы."""
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
    # Никогда не выше INFO, какой бы уровень ни просили: и книга золота, и
    # несостоявшиеся действия - это строки INFO, и корень, который их отбросил,
    # оставил бы важный файл пустым. Уровень решает другое - что покажут консоль и
    # повседневный файл.
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

    # Запуск после недели простоя убирает до того, как начнёт писать: обработчики ниже
    # удаляют только на собственном перевороте, а это полночь, до которой может быть
    # сутки.
    swept = sweep(directory, days=settings.log_retention_days, stem=ACTIVITY_FILE)
    swept += sweep(directory, days=settings.log_important_retention_days, stem=IMPORTANT_FILE)

    activity = _rotating(directory / ACTIVITY_FILE, keep=settings.log_retention_days)
    activity.setLevel(level)
    activity.setFormatter(_formatter(shared, json=use_json, colors=False))
    root.addHandler(activity)

    important = _rotating(directory / IMPORTANT_FILE, keep=settings.log_important_retention_days)
    # DEBUG, а не ``level``: что сохранить, решает фильтр ниже, и игра, работающая на
    # WARNING, обязана сохранить свою книгу золота.
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
    """Вернуть журнал, привязанный к имени модуля."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def is_important(event: object, level: int) -> bool:
    """Переживёт ли эта строка автоочистку.

    Принимает словарь события, который несёт строка structlog, или что угодно
    другое для строки, дошедшей до моста из библиотеки, - там известен только
    уровень, и правило по уровню написано ровно для этого случая.
    """
    if level >= logging.WARNING:
        return True
    if not isinstance(event, dict):
        return False
    return event.get("event") in KEPT_EVENTS or event.get("result") in KEPT_RESULTS


def sweep(directory: Path, *, days: int, stem: str, now: float | None = None) -> int:
    """Удалить перевёрнутые копии ``stem`` старше ``days``; ``0`` не удаляет ничего.

    Считаются только перевороты (``vellar.log.2026-08-12``): у файла, в который
    пишут прямо сейчас, нет возраста, а до основы, которую не назвал вызывающий,
    дело не доходит - именно это держит уборку повседневного журнала подальше от
    важного.
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
        except OSError:  # pragma: no cover - файл, который кто-то держит открытым
            continue
    return removed


class _ImportantOnly(logging.Filter):
    """Пропускает только то, что автоочистке терять нельзя."""

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
    """Один файл в день. ``keep=0`` переворачивает и не удаляет ничего, никогда."""
    return logging.handlers.TimedRotatingFileHandler(
        path,
        when="midnight",
        utc=True,
        backupCount=keep,
        encoding="utf-8",
        delay=True,
    )


def _prepare(directory: Path | None) -> Path | None:
    """Создать каталог журнала или ответить ``None`` и продолжить в stdout.

    Дерево, в которое нельзя писать, - не повод отказаться обслуживать игроков: в
    контейнере журнал и так принадлежит демону, собирающему stdout.
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
    """Отменить прежнюю настройку, закрыв открытые ею файлы.

    Настроить дважды случается в тестах и при перезагрузке; на Windows оставленный
    открытым файл нельзя ни перевернуть, ни удалить тому запуску, который придёт
    следом.
    """
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.close()
