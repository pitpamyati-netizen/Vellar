"""The accessibility rules from docs/accessibility.md, enforced mechanically.

A failure here is a blocker-priority bug, not a style nit: each of these
corresponds to a way the game becomes unplayable by ear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mmorpg.presentation.telegram.keyboards.labels import BACK, LOOK, MAIN_MENU, SERVICE_ROW
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT
from tests.conftest import SOURCE_ROOT, iter_source_files

FORBIDDEN_SUBSTRINGS = (
    "InlineKeyboardMarkup",
    "InlineKeyboardButton",
    "callback_query",
    "CallbackQuery",
    "edit_message_text",
    "edit_message_reply_markup",
    "edit_text",
)

PSEUDO_GRAPHICS = ("■", "□", "▓", "░", "█", "▒", "─", "│", "┌", "└", "├")


def _source_files() -> list[Path]:
    return iter_source_files()


# --- rule 1 and 2: no inline keyboards, no message editing ------------


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_inline_keyboards_or_message_edits(path: Path) -> None:
    """Inline buttons and edits are invisible to a screen reader. Both are banned."""
    text = path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in text and "FORBIDDEN_SUBSTRINGS" not in text:
            pytest.fail(f"{path.relative_to(SOURCE_ROOT)} mentions {forbidden}")


def test_only_reply_keyboards_are_imported() -> None:
    keyboards = SOURCE_ROOT / "presentation" / "telegram" / "keyboards"
    imported: set[str] = set()
    for path in keyboards.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aiogram"):
                imported.update(alias.name for alias in node.names)
    assert "InlineKeyboardMarkup" not in imported
    assert "ReplyKeyboardMarkup" in imported


# --- rule 8: the service row is on every screen ----------------------


def test_service_row_is_stable() -> None:
    assert SERVICE_ROW == (BACK, MAIN_MENU)
    assert [item.text for item in SERVICE_ROW] == ["Назад", "Главное меню"]
    # "Осмотреться" left the keyboard but not the game: the command still works,
    # and so does a button pressed from an older keyboard.
    assert LOOK not in SERVICE_ROW


def test_every_screen_ends_with_the_service_row(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        assert screen.all_rows()[-1] == SERVICE_ROW, screen.id


# --- rule 9: unique labels within a screen ---------------------------


def test_labels_are_unique_within_every_screen(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        rendered = [text for row in screen.button_texts() for text in row]
        assert len(rendered) == len(set(rendered)), screen.id


def test_duplicate_labels_are_rejected_at_construction() -> None:
    from mmorpg.presentation.telegram.keyboards.labels import label

    with pytest.raises(ValueError, match="duplicate button label"):
        Screen(
            id=ScreenId.TAVERN,
            lines=("Тест.",),
            rows=((label("Повтор"), label("Повтор")),),
        )


# --- rule 6: emoji never carry meaning alone -------------------------


def test_labels_are_unambiguous_without_emoji(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        plain = [text for row in screen.button_texts(emoji=False) for text in row]
        assert all(text.strip() for text in plain), screen.id
        assert len(plain) == len(set(plain)), screen.id


def test_labels_stay_unique_with_emoji(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        fancy = [text for row in screen.button_texts(emoji=True) for text in row]
        assert len(fancy) == len(set(fancy)), screen.id


def test_emoji_are_off_by_default() -> None:
    from mmorpg.domain.ports import AccessibilitySettings

    assert AccessibilitySettings().emoji is False


# --- rule 5: no pseudo-graphics --------------------------------------


def test_no_pseudo_graphics_in_screen_text(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        text = screen.text()
        for symbol in PSEUDO_GRAPHICS:
            assert symbol not in text, f"{screen.id} draws with {symbol!r}"


# --- rule 11: message length -----------------------------------------


def test_screens_fit_the_message_limit(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        assert screen.fits_message_limit(), (
            f"{screen.id} is {len(screen.text())} characters, limit is {MESSAGE_LIMIT}"
        )


# --- rule 4: the key fact comes first --------------------------------


def test_screens_open_with_a_non_empty_line(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        assert screen.lines, screen.id
        assert screen.lines[0].strip(), screen.id


# --- rule 14: no Markdown --------------------------------------------


def test_no_markdown_parse_mode_is_configured() -> None:
    """Asterisks and underscores are read aloud, so the bot sends plain text."""
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        assert "ParseMode.MARKDOWN" not in text, path.name
        assert 'parse_mode="Markdown"' not in text, path.name
