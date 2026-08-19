"""Журнал действий: кто что нажал и чем это кончилось."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Chat, Message, TelegramObject, Update, User

from mmorpg.config import Settings
from mmorpg.logging import ACTIVITY_FILE, IMPORTANT_FILE, configure_logging
from mmorpg.presentation.telegram.middlewares.audit import (
    FAILED,
    KEY,
    MAX_TEXT,
    AuditMiddleware,
    Note,
    note_of,
)

PRIVATE = 42
GROUP = -100_500


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"_env_file": None}
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    configure_logging(_settings(log_dir=str(tmp_path)))
    return tmp_path


def _update(text: str, *, chat_type: str = "private") -> Update:
    chat_id = PRIVATE if chat_type == "private" else GROUP
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=PRIVATE, is_bot=False, first_name="Игрок"),
        text=text,
    )
    return Update(update_id=1, message=message)


async def _run(update: Update, answer: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}

    async def handler(event: Any, payload: dict[str, Any]) -> Any:
        if isinstance(answer, Exception):
            raise answer
        return answer

    await AuditMiddleware()(handler, update, data)
    return data


def _lines(directory: Path, name: str) -> list[str]:
    path = directory / name
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if "action" in line]


# --- одно нажатие — одна строка ---------------------------------------


async def test_a_pressed_button_is_written_down(journal: Path) -> None:
    await _run(_update("Атака"), None)

    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "who=42" in line
    assert "did=Атака" in line
    assert "result=ok" in line
    assert "ms=" in line


async def test_a_long_message_is_cut_short(journal: Path) -> None:
    """Журнал — не переписка: в него попадает столько, чтобы понять действие."""
    await _run(_update("я" * 200), None)

    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "я" * MAX_TEXT in line
    assert "я" * (MAX_TEXT + 1) not in line


async def test_a_crash_is_recorded_and_still_raised(journal: Path) -> None:
    with pytest.raises(RuntimeError):
        await _run(_update("Купить"), RuntimeError("boom"))

    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "result=failed" in line
    # И в вечном файле: именно этой строки хватятся, когда придёт вопрос.
    assert "result=failed" in _lines(journal, IMPORTANT_FILE)[0]


async def test_a_button_nobody_answered_is_a_finding(journal: Path) -> None:
    """Молчание бота слепой игрок отличить от паузы не может."""
    await _run(_update("Сдаться"), UNHANDLED)

    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "result=ignored" in line


async def test_the_group_is_written_down_only_when_the_bot_answered(journal: Path) -> None:
    """Разговор игроков между собой игру не касается и в журнал не попадает."""
    await _run(_update("да ну тебя", chat_type="group"), UNHANDLED)
    assert _lines(journal, ACTIVITY_FILE) == []

    await _run(_update("профиль", chat_type="group"), None)
    assert _lines(journal, ACTIVITY_FILE) == []

    with pytest.raises(RuntimeError):
        await _run(_update("профиль", chat_type="group"), RuntimeError("boom"))
    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "chat=group" in line
    assert "result=failed" in line


# --- блокнот, который заполняют по дороге -----------------------------


async def test_the_note_is_left_for_the_rest_of_the_way(journal: Path) -> None:
    data = await _run(_update("Атака"), None)

    assert isinstance(data[KEY], Note)
    assert note_of(data) is data[KEY]


def test_the_first_outcome_wins() -> None:
    """Отметка ближе к причине: заблокированный не становится «дубликатом»."""
    note = Note()
    note.done("banned")
    note.done(FAILED)

    assert note.result == "banned"


def test_a_note_is_only_a_note_when_there_is_one() -> None:
    assert note_of({}) is None
    assert note_of({KEY: "не блокнот"}) is None


# --- вся цепочка целиком ----------------------------------------------


async def test_the_outcome_travels_through_the_real_dispatcher(journal: Path) -> None:
    """Блокнот должен пережить дорогу до хендлера.

    aiogram передаёт данные обновления распаковкой, и по дороге словарь
    пересобирается. Держится журнал тем, что отметка ставится в объекте, а не в
    словаре, — а это допущение проверяется только сборкой целиком.
    """
    from aiogram import Bot, Dispatcher, Router
    from aiogram.fsm.storage.memory import MemoryStorage

    class Doorman(BaseMiddleware):
        """Тот, кто заворачивает игрока и отмечается в блокноте, как ban-гейт."""

        async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
        ) -> Any:
            note = note_of(data)
            assert note is not None, "блокнот не доехал до прослойки сообщения"
            note.done("banned")
            return None

    router = Router()

    @router.message()
    async def _never(message: Message) -> None:  # pragma: no cover - дверь закрыта
        raise AssertionError("до хендлера доходить было не должно")

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(AuditMiddleware())
    dispatcher.message.outer_middleware(Doorman())
    dispatcher.include_router(router)

    bot = Bot(token="42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN")
    try:
        await dispatcher.feed_update(bot, _update("Атака"))
    finally:
        await bot.session.close()

    (line,) = _lines(journal, ACTIVITY_FILE)
    assert "result=banned" in line
    # И в вечном файле: закрытая дверь — то, о чём спросят позже.
    assert "result=banned" in _lines(journal, IMPORTANT_FILE)[0]
