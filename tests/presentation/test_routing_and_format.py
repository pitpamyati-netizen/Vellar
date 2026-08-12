"""Routing, text commands, navigation stack and speakable formatting."""

from __future__ import annotations

import pytest

from mmorpg.presentation.telegram.keyboards.labels import label
from mmorpg.presentation.telegram.routing import (
    Intent,
    parse_command,
    resolve,
    stale_button_answer,
)
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import (
    MESSAGE_LIMIT,
    amount,
    gold,
    paginate_text,
    percent,
    turns,
)
from mmorpg.presentation.telegram.states.screens import (
    BACK_TARGET,
    CREATION_ORDER,
    STATE_FOR_SCREEN,
    NavigationStack,
    back_target,
)


@pytest.fixture
def screen() -> Screen:
    return Screen(
        id=ScreenId.CITY,
        lines=("Город Дальний Оплот.",),
        rows=((label("Лавка"), label("Локации")),),
    )


# --- text commands (rule 10) -----------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("/назад", Intent.BACK),
        ("/осмотреться", Intent.LOOK),
        ("/меню", Intent.MAIN_MENU),
        ("/back", Intent.BACK),
        ("/бой атака", Intent.ATTACK),
        ("/бой бежать", Intent.FLEE),
        ("/combat attack", Intent.ATTACK),
    ],
)
def test_every_action_has_a_typed_duplicate(text: str, intent: Intent) -> None:
    parsed = parse_command(text)
    assert parsed is not None
    assert parsed.intent is intent


def test_skill_and_page_commands_carry_numbers() -> None:
    skill = parse_command("/умение 3")
    assert skill is not None
    assert (skill.intent, skill.number) == (Intent.SKILL, 3)

    page = parse_command("/страница 12")
    assert page is not None
    assert (page.intent, page.number) == (Intent.PAGE, 12)


def test_commands_are_case_insensitive() -> None:
    parsed = parse_command("/НАЗАД")
    assert parsed is not None
    assert parsed.intent is Intent.BACK


def test_plain_text_is_not_a_command() -> None:
    assert parse_command("Лавка") is None


def test_unknown_command_is_answered_not_ignored() -> None:
    parsed = parse_command("/чтототакое")
    assert parsed is not None
    assert parsed.intent is Intent.UNKNOWN


# --- button routing ---------------------------------------------------


def test_service_buttons_route_from_any_screen(screen: Screen) -> None:
    assert resolve("Назад", screen).intent is Intent.BACK
    assert resolve("Осмотреться", screen).intent is Intent.LOOK
    assert resolve("Главное меню", screen).intent is Intent.MAIN_MENU


def test_service_buttons_route_with_emoji_too(screen: Screen) -> None:
    """An old keyboard may have been rendered with the other emoji setting."""
    assert resolve("◀️ Назад", screen).intent is Intent.BACK
    assert resolve("🔁 Осмотреться", screen).intent is Intent.LOOK


def test_screen_buttons_resolve_to_a_selection(screen: Screen) -> None:
    command = resolve("Лавка", screen)
    assert command.intent is Intent.SELECT
    assert command.argument == "Лавка"


def test_a_stale_button_is_reported_not_swallowed(screen: Screen) -> None:
    """Rule 12: never stay silent, never raise."""
    command = resolve("Вихрь клинков", screen)
    assert command.intent is Intent.UNKNOWN
    answer = stale_button_answer("Город Дальний Оплот")
    assert answer.startswith("Это действие сейчас недоступно")
    assert "Город Дальний Оплот" in answer


# --- navigation stack (spec section 12) ------------------------------


def test_back_walks_one_step_at_a_time() -> None:
    stack = NavigationStack()
    for screen_id in CREATION_ORDER:
        stack = stack.push(screen_id)
    assert stack.current is ScreenId.CREATE_CONFIRM

    stack, previous = stack.pop()
    assert previous is ScreenId.CREATE_POINTS
    stack, previous = stack.pop()
    assert previous is ScreenId.CREATE_TRAITS


def test_back_from_the_first_step_leaves_the_flow() -> None:
    stack = NavigationStack().push(ScreenId.CREATE_NAME)
    _, previous = stack.pop()
    assert previous is None
    assert back_target(ScreenId.CREATE_NAME) is None


def test_pushing_the_same_screen_twice_does_not_grow_the_stack() -> None:
    stack = NavigationStack().push(ScreenId.CITY).push(ScreenId.CITY)
    assert len(stack.screens) == 1


def test_stack_survives_serialisation() -> None:
    stack = NavigationStack().push(ScreenId.MAIN_MENU).push(ScreenId.WORLD).push(ScreenId.CITY)
    restored = NavigationStack.deserialise(stack.serialise())
    assert restored == stack
    assert NavigationStack.deserialise("") == NavigationStack(())


def test_every_screen_has_a_state_and_a_back_target() -> None:
    """No dead ends: every screen knows where it is and where back leads."""
    for screen_id in ScreenId:
        if screen_id is ScreenId.START:
            continue
        assert screen_id in STATE_FOR_SCREEN, screen_id
        assert screen_id in BACK_TARGET, screen_id


def test_back_targets_never_dangle_and_always_terminate() -> None:
    for screen_id in BACK_TARGET:
        seen: list[ScreenId] = []
        current: ScreenId | None = screen_id
        while current is not None:
            assert current not in seen, f"back loop starting at {screen_id}"
            seen.append(current)
            assert current in BACK_TARGET, f"{current} has no back target"
            current = BACK_TARGET[current]


# --- formatting (rules 4 and 5) --------------------------------------


def test_amount_is_spoken_not_drawn() -> None:
    assert amount(42, 120) == "42 из 120, это 35 процентов"
    assert amount(42, 120, with_percent=False) == "42 из 120"
    assert "[" not in amount(42, 120)


def test_amount_survives_a_zero_maximum() -> None:
    assert amount(0, 0) == "0"


def test_percent_states_penalties_in_words() -> None:
    assert percent(15) == "15 процентов"
    assert percent(-10) == "минус 10 процентов"


def test_russian_plurals() -> None:
    assert turns(1) == "1 ход"
    assert turns(2) == "2 хода"
    assert turns(5) == "5 ходов"
    assert turns(11) == "11 ходов"
    assert turns(21) == "21 ход"
    assert gold(2) == "2 золотых"


def test_long_text_is_paginated_on_line_boundaries() -> None:
    text = "\n".join(f"Строка номер {index}" for index in range(200))
    pages = paginate_text(text)
    assert len(pages) > 1
    assert all(len(page) <= MESSAGE_LIMIT for page in pages)
    assert "\n".join(pages) == text


def test_short_text_stays_one_message() -> None:
    assert paginate_text("Одна строка") == ("Одна строка",)
