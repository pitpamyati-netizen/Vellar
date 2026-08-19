"""Shared test fixtures and helpers."""

from __future__ import annotations

import logging
import logging.handlers
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content import load_content

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mmorpg"
CONTENT_ROOT = PROJECT_ROOT / "content"


@pytest.fixture(scope="session")
def content() -> GameContent:
    """Содержимое игры, прочитанное один раз на весь прогон.

    Оно неизменяемо, поэтому делить его между тестами безопасно; пять копий этой
    фикстуры по пакетам разбирали одни и те же девять файлов заново.
    """
    return load_content(CONTENT_ROOT)


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def source_root() -> Path:
    return SOURCE_ROOT


@pytest.fixture(scope="session")
def content_root() -> Path:
    return CONTENT_ROOT


def iter_source_files(root: Path = SOURCE_ROOT) -> list[Path]:
    """Every Python module shipped in the package."""
    return sorted(root.rglob("*.py"))


@pytest.fixture(autouse=True)
def _logging_stays_where_it_was() -> Iterator[None]:
    """Вернуть журнал в то состояние, в котором тест его застал.

    ``configure_logging`` — настройка процесса целиком: она перебирает обработчики
    корневого логгера и открывает файлы. Тест, который её вызвал, иначе решал бы
    за всех, кто идёт после него, а вдобавок держал бы файл открытым — в Windows
    такой файл не удалить и не повернуть.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        for handler in root.handlers:
            if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
        structlog.reset_defaults()
