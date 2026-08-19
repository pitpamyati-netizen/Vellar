"""Что игра записывает в файлы и что переживает автоочистку."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from mmorpg.config import Settings
from mmorpg.logging import (
    ACTIVITY_FILE,
    IMPORTANT_FILE,
    SECONDS_IN_DAY,
    configure_logging,
    get_logger,
    is_important,
    sweep,
)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"_env_file": None}
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


def _written(directory: Path, name: str) -> str:
    path = directory / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- две половины журнала ---------------------------------------------


def test_a_line_lands_in_the_everyday_file(tmp_path: Path) -> None:
    configure_logging(_settings(log_dir=str(tmp_path)))
    get_logger("test").info("action", who=42, did="Атака", result="ok", ms=7)

    written = _written(tmp_path, ACTIVITY_FILE)
    assert "action" in written
    assert "who=42" in written
    assert "Атака" in written


def test_everyday_chatter_stays_out_of_the_important_file(tmp_path: Path) -> None:
    """Иначе вечный файл — это тот же журнал, только его никогда не чистят."""
    configure_logging(_settings(log_dir=str(tmp_path)))
    get_logger("test").info("action", who=42, did="Главное меню", result="ok", ms=3)

    assert "action" in _written(tmp_path, ACTIVITY_FILE)
    assert _written(tmp_path, IMPORTANT_FILE) == ""


def test_a_failure_is_written_down_twice(tmp_path: Path) -> None:
    """В обычный файл — как всё, и в важный — как то, что чистка не тронет."""
    configure_logging(_settings(log_dir=str(tmp_path)))
    logger = get_logger("test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("handler_failed", event_type="Update")

    for name in (ACTIVITY_FILE, IMPORTANT_FILE):
        written = _written(tmp_path, name)
        assert "handler_failed" in written
        assert "RuntimeError: boom" in written


def test_an_action_that_failed_is_kept(tmp_path: Path) -> None:
    configure_logging(_settings(log_dir=str(tmp_path)))
    get_logger("test").info("action", who=42, did="Купить", result="failed", ms=9)

    assert "did=Купить" in _written(tmp_path, IMPORTANT_FILE)


def test_the_gold_ledger_is_kept(tmp_path: Path) -> None:
    """По ней правят экономику, и вопрос о пропавшем золоте приходит не назавтра."""
    configure_logging(_settings(log_dir=str(tmp_path)))
    get_logger("test").info("gold_flow", flow="fight", amount=12, character_id=1)

    assert "gold_flow" in _written(tmp_path, IMPORTANT_FILE)


def test_the_ledger_survives_a_quiet_log_level(tmp_path: Path) -> None:
    """Игра, которой велели молчать, всё равно считает золото."""
    configure_logging(_settings(log_dir=str(tmp_path), log_level="WARNING"))
    get_logger("test").info("gold_flow", flow="shop", amount=-5, character_id=1)

    assert "gold_flow" in _written(tmp_path, IMPORTANT_FILE)
    assert _written(tmp_path, ACTIVITY_FILE) == ""


def test_an_empty_directory_means_stdout_only(tmp_path: Path) -> None:
    """Контейнеру писать некуда, и это не повод не обслуживать игроков."""
    configure_logging(_settings(log_dir=""))
    get_logger("test").info("action", who=42, did="Атака", result="ok", ms=7)

    assert list(tmp_path.iterdir()) == []


def test_importance_is_decided_by_level_for_a_line_from_a_library() -> None:
    assert is_important("connection lost", logging.WARNING) is True
    assert is_important("some chatter", logging.INFO) is False


# --- автоочистка ------------------------------------------------------


def _aged(path: Path, *, days: float) -> Path:
    path.write_text("старая строка", encoding="utf-8")
    old = time.time() - days * SECONDS_IN_DAY
    os.utime(path, (old, old))
    return path


def test_the_sweep_removes_only_what_is_past_its_term(tmp_path: Path) -> None:
    stale = _aged(tmp_path / f"{ACTIVITY_FILE}.2026-08-01", days=10)
    fresh = _aged(tmp_path / f"{ACTIVITY_FILE}.2026-08-18", days=1)

    assert sweep(tmp_path, days=7, stem=ACTIVITY_FILE) == 1
    assert not stale.exists()
    assert fresh.exists()


def test_the_sweep_never_touches_the_important_file(tmp_path: Path) -> None:
    """Ровно то, ради чего журнал вообще разделён надвое."""
    kept = _aged(tmp_path / f"{IMPORTANT_FILE}.2020-01-01", days=2000)

    assert sweep(tmp_path, days=7, stem=ACTIVITY_FILE) == 0
    assert kept.exists()


def test_zero_days_deletes_nothing(tmp_path: Path) -> None:
    kept = _aged(tmp_path / f"{IMPORTANT_FILE}.2020-01-01", days=2000)

    assert sweep(tmp_path, days=0, stem=IMPORTANT_FILE) == 0
    assert kept.exists()


def test_the_file_being_written_to_is_not_a_rollover(tmp_path: Path) -> None:
    current = _aged(tmp_path / ACTIVITY_FILE, days=99)

    assert sweep(tmp_path, days=7, stem=ACTIVITY_FILE) == 0
    assert current.exists()


def test_a_run_after_a_week_away_sweeps_before_it_writes(tmp_path: Path) -> None:
    stale = _aged(tmp_path / f"{ACTIVITY_FILE}.2026-08-01", days=30)

    configure_logging(_settings(log_dir=str(tmp_path)))

    assert not stale.exists()
    assert "log_files" in _written(tmp_path, ACTIVITY_FILE)


def test_the_library_line_that_repeats_our_own_is_quieted(tmp_path: Path) -> None:
    """Одно нажатие - одна строка.

    ``aiogram.event`` пишет «Update id=... is handled» рядом с нашей же строкой
    ``action``, где сказано то же самое и вдобавок кто и что нажал.
    """
    configure_logging(_settings(log_dir=str(tmp_path)))

    assert logging.getLogger("aiogram.event").level == logging.WARNING
