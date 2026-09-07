"""Правила доступности из docs/accessibility.md, закреплённые механически.

Падение здесь - ошибка уровня «блокер», а не придирка к стилю: за каждым из них
стоит способ, которым игра становится непроходимой на слух.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules.combat import hero_combatant
from mmorpg.presentation.telegram.keyboards.labels import (
    BACK,
    BUTTON_LIMIT,
    LOOK,
    MAIN_MENU,
    SERVICE_ROW,
    label,
)
from mmorpg.presentation.telegram.screens import combat as combat_screens
from mmorpg.presentation.telegram.screens import items as item_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
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


def test_every_thing_a_card_can_show_has_a_russian_name(content: GameContent) -> None:
    """Прибавка на вещи читается словом, а не английским ключом.

    Карточка вещи (``screens/items.modifier_line``) берёт название из
    ``MODIFIER_NAMES``; ключа, которого там нет, диктор прочтёт по буквам. Сюда
    попадает всё, что вещь и её род могут навесить: особые свойства легендарок,
    прибавки родов оружия и доспеха (ADR 0052).
    """
    shown: set[str] = {prop.key for prop in content.special_properties}
    for weapon in content.weapon_types:
        shown |= set(weapon.modifiers)
    for armor in content.armor_types:
        shown |= set(armor.modifiers)
    missing = sorted(key for key in shown if key not in item_screens.MODIFIER_NAMES)
    assert not missing, f"нет русского имени на карточке вещи: {missing}"


# --- правило 9: надпись укладывается в то, что Telegram отдаёт обратно ---


def test_no_screen_draws_a_button_longer_than_telegram_sends(all_screens: list[Screen]) -> None:
    """Кнопка длиннее предела приходит обрезанной, и экран её не узнаёт.

    Так пропадал целый экран умений: «Обманный финт — боевое, ранг 3 из 5: откат
    короче...» доезжал без последних знаков, маршрут по точному тексту не
    находил умения, и в ответ приходило «Нажмите умение из списка».
    """
    for screen in all_screens:
        for row in screen.button_texts(emoji=True):
            for text in row:
                assert len(text) <= BUTTON_LIMIT, f"{screen.id}: {len(text)} знаков, {text!r}"


def test_a_long_label_is_cut_at_a_word_and_stays_within_the_limit() -> None:
    long = label("Слово " * 40)
    assert len(long.text) <= BUTTON_LIMIT
    assert long.text.endswith("…")
    assert long.matches(long.text)


def test_every_skill_in_the_game_fits_its_button(content: GameContent) -> None:
    """Ни одно умение не собирает надписи, которую пришлось бы резать.

    Проверяется всё содержимое, а не тот десяток умений, что попал на экраны:
    ``skills.toml`` правится без кода, и длинное описание не должно превращать
    кнопку в нерабочую ни в списке умений, ни в боевой панели.
    """
    for skill in content.skills:
        owner = skill.owner.split(":", 1)
        class_id = owner[1] if owner[0] == "class" else "warrior"
        if not any(one.id == class_id for one in content.classes):
            class_id = "warrior"
        hero = Character(
            id=1, user_id=1, name="Тест", race_id="human", class_id=class_id, level=150
        )
        for rank in (1, content.rules.max_rank):
            known = replace(hero, loadout=hero.loadout.with_rank(skill.code, rank))
            for who in (hero, known):
                said = skill_screens.skill_entry_text(content, who, skill)
                assert len(said) <= BUTTON_LIMIT, f"{skill.code}: {len(said)} знаков, {said!r}"
            if not skill.is_active:
                continue
            panel = replace(known, loadout=known.loadout.with_active(0, skill.code))
            viewer = hero_combatant(content, panel, combatant_id=1, side=0, live=True)
            in_panel = combat_screens.skill_label(content, panel, viewer, 0).text
            assert len(in_panel) <= BUTTON_LIMIT, f"{skill.code}: {in_panel!r}"
