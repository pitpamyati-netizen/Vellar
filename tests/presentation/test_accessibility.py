"""Правила доступности из docs/accessibility.md, закреплённые механически.

Падение здесь - ошибка уровня «блокер», а не придирка к стилю: за каждым из них
стоит способ, которым игра становится непроходимой на слух.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.presentation.telegram.keyboards.labels import BACK, LOOK, MAIN_MENU, SERVICE_ROW
from mmorpg.presentation.telegram.screens import items as item_screens
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


# --- правила 1 и 2: никаких inline-клавиатур, никаких правок сообщений ---


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_inline_keyboards_or_message_edits(path: Path) -> None:
    """Inline-кнопки и правки сообщений экранному диктору не видны. Запрещены оба."""
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


# --- правило 8: служебный ряд есть на каждом экране -------------------


def test_service_row_is_stable() -> None:
    assert SERVICE_ROW == (BACK, MAIN_MENU)
    assert [item.text for item in SERVICE_ROW] == ["Назад", "Главное меню"]
    # «Осмотреться» ушло с клавиатуры, но не из игры: команда работает, как работает и
    # кнопка, нажатая на старой клавиатуре.
    assert LOOK not in SERVICE_ROW


#: Единственный экран без служебного ряда - корень. «Назад» из главного меню
#: вело в главное меню, «Главное меню» - туда же: две кнопки, не делающие ничего.
ROOT_SCREEN = ScreenId.MAIN_MENU


def test_every_screen_ends_with_the_service_row(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        if screen.id is ROOT_SCREEN:
            assert SERVICE_ROW not in screen.all_rows(), screen.id
            continue
        assert screen.all_rows()[-1] == SERVICE_ROW, screen.id


# --- правило 9: надписи внутри экрана не повторяются -------------------


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


# --- правило 6: значок никогда не несёт смысл в одиночку --------------


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


# --- правило 5: никакой псевдографики ---------------------------------


def test_no_pseudo_graphics_in_screen_text(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        text = screen.text()
        for symbol in PSEUDO_GRAPHICS:
            assert symbol not in text, f"{screen.id} draws with {symbol!r}"


# --- правило 11: длина сообщения --------------------------------------


def test_screens_fit_the_message_limit(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        assert screen.fits_message_limit(), (
            f"{screen.id} is {len(screen.text())} characters, limit is {MESSAGE_LIMIT}"
        )


# --- правило 4: главное идёт первым -----------------------------------


def test_screens_open_with_a_non_empty_line(all_screens: list[Screen]) -> None:
    for screen in all_screens:
        assert screen.lines, screen.id
        assert screen.lines[0].strip(), screen.id


# --- правило 14: никакой разметки -------------------------------------


def test_no_markdown_parse_mode_is_configured() -> None:
    """Звёздочки и подчёркивания читаются вслух, поэтому бот шлёт чистый текст."""
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        assert "ParseMode.MARKDOWN" not in text, path.name
        assert 'parse_mode="Markdown"' not in text, path.name


def test_the_slot_list_matches_the_content_it_names(content: GameContent) -> None:
    """Слоты названы в двух местах, и разойтись им нельзя.

    Экран персонажа перебирает ``SLOT_NAMES``, а броню и допуски считает
    содержимое: слот, выпавший из одного списка, стал бы местом, куда нельзя ни
    надеть, ни снять.
    """
    assert [slot.id for slot in content.slots] == list(item_screens.SLOT_NAMES)
    for slot in content.slots:
        assert item_screens.SLOT_NAMES[slot.id] == slot.name
