"""Character creation: every step, and every way back out of it.

The flow is a pure function, so these tests walk the whole thing the way a player
would - by sending button texts.
"""

from __future__ import annotations

import pytest

from mmorpg.application.dto.creation import CharacterDraft, validate_name
from mmorpg.domain.entities import GameContent, StatBlock, StatCode
from mmorpg.presentation.telegram.flows.creation import (
    CreationState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import CREATION_ORDER


def walk(content: GameContent, *messages: str, state: CreationState | None = None) -> CreationState:
    current = state or begin()
    for message in messages:
        current = advance(content, current, message)
    return current


@pytest.fixture
def named(content: GameContent) -> CreationState:
    return walk(content, "Аргус")


@pytest.fixture
def raced(content: GameContent, named: CreationState) -> CreationState:
    return walk(content, "Кадур", "Продолжить", state=named)


@pytest.fixture
def classed(content: GameContent, raced: CreationState) -> CreationState:
    return walk(content, "Ратник — стойкий боец ближнего боя", "Продолжить", state=raced)


@pytest.fixture
def traited(content: GameContent, classed: CreationState) -> CreationState:
    return walk(content, "Берсерк", "Дуэлянт", "Продолжить", state=classed)


@pytest.fixture
def pointed(content: GameContent, traited: CreationState) -> CreationState:
    return walk(
        content,
        "Сила плюс один",
        "Сила плюс один",
        "Выносливость плюс один",
        "Удача плюс один",
        "Ловкость плюс один",
        "Продолжить",
        state=traited,
    )


# --- names ------------------------------------------------------------


@pytest.mark.parametrize("name", ["Аргус", "Мал", "Ли-Ан", "Дед Мороз", "O'Brien", "Игрок 2"])
def test_valid_names(name: str) -> None:
    assert validate_name(name).ok


@pytest.mark.parametrize("name", ["", "А", "   ", "1Игрок", "*звёздочка*", "и" * 21])
def test_invalid_names_are_explained(name: str) -> None:
    check = validate_name(name)
    assert check.ok is False
    assert check.problem


def test_name_step_accepts_a_typed_name(content: GameContent, named: CreationState) -> None:
    assert named.draft.name == "Аргус"
    assert named.screen is ScreenId.CREATE_RACE


def test_a_bad_name_keeps_the_step_and_explains(content: GameContent) -> None:
    state = walk(content, "*")
    assert state.screen is ScreenId.CREATE_NAME
    assert state.notice
    assert state.draft.name == ""


def test_a_taken_name_is_refused_politely(content: GameContent) -> None:
    state = advance(content, begin(), "Аргус", name_taken=True)
    assert state.screen is ScreenId.CREATE_NAME
    assert "занято" in state.notice


# --- the happy path ---------------------------------------------------


def test_full_walk_reaches_confirmation(content: GameContent, pointed: CreationState) -> None:
    assert pointed.screen is ScreenId.CREATE_CONFIRM
    assert pointed.draft.is_complete(content)


def test_confirmation_finishes_the_flow(content: GameContent, pointed: CreationState) -> None:
    done = advance(content, pointed, "Подтвердить")
    assert done.finished is True


def test_the_draft_becomes_a_playable_character(
    content: GameContent, pointed: CreationState
) -> None:
    character = pointed.draft.to_character(content, user_id=42)
    assert character.name == "Аргус"
    assert character.race_id == "dwarf"
    assert character.class_id == "warrior"
    assert character.level == 1
    assert character.city_id == "farhold"
    # The first class active and the racial active are slotted; nothing else is.
    assert character.loadout.actives[0] == "warrior_cleave"
    assert character.loadout.actives[1:] == (None,) * 5
    assert character.loadout.racial == "race_dwarf_ancestral_stance"


# --- back navigation (spec section 12) --------------------------------


def test_back_returns_exactly_one_step(content: GameContent, traited: CreationState) -> None:
    assert traited.screen is ScreenId.CREATE_POINTS
    assert advance(content, traited, "Назад").screen is ScreenId.CREATE_TRAITS


def test_back_keeps_every_choice_already_made(content: GameContent, traited: CreationState) -> None:
    back = advance(content, traited, "Назад")
    assert back.draft == traited.draft
    assert back.draft.trait_ids == traited.draft.trait_ids


def _walk_back_to(content: GameContent, state: CreationState, target: ScreenId) -> CreationState:
    current = state
    for _ in range(10):
        if current.screen is target:
            return current
        current = advance(content, current, "Назад")
    raise AssertionError(f"never reached {target}")


def test_going_back_to_race_shows_the_current_race(
    content: GameContent, classed: CreationState
) -> None:
    back = _walk_back_to(content, classed, ScreenId.CREATE_RACE)
    assert "Кадур" in render(content, back).text()


def test_changing_the_race_restates_its_bonuses(
    content: GameContent, classed: CreationState
) -> None:
    """Switching race after going back must not silently change the numbers."""
    at_race = _walk_back_to(content, classed, ScreenId.CREATE_RACE)
    switched = advance(content, at_race, "Аурен")
    assert switched.draft.race_id == "high_elf"
    assert "плюс 2 к интеллекту" in switched.notice
    assert "минус 1 к выносливости" in switched.notice


def test_races_beyond_the_first_page_are_reachable(
    content: GameContent, named: CreationState
) -> None:
    """16 races over 8-entry pages: the later ones need the navigation row."""
    assert advance(content, named, "Ургаш").draft.race_id == "", "Ургаш is not on page 1"
    second_page = advance(content, named, "Следующая страница")
    assert second_page.race_page.page == 2
    chosen = advance(content, second_page, "Ургаш")
    assert chosen.draft.race_id == "orc"
    assert "плюс 3 к силе" in chosen.notice


def test_back_from_the_first_step_leaves_creation(content: GameContent) -> None:
    state = advance(content, begin(), "Назад")
    assert state.exited is True


def test_every_creation_screen_is_reachable_and_has_a_way_back(
    content: GameContent, pointed: CreationState
) -> None:
    """Walk the whole flow backwards: no dead ends, no unreachable steps."""
    visited = [pointed.screen]
    state = pointed
    while not state.exited:
        state = advance(content, state, "Назад")
        if state.exited:
            break
        visited.append(state.screen)
    assert set(CREATION_ORDER) <= set(visited)


def test_details_screens_return_to_their_list(content: GameContent, named: CreationState) -> None:
    at_race = walk(content, "Кадур", "Подробно о народе", state=named)
    assert at_race.screen is ScreenId.CREATE_RACE_DETAILS
    assert "Дублёная кожа" in render(content, at_race).text()
    assert advance(content, at_race, "Назад").screen is ScreenId.CREATE_RACE


# --- guards -----------------------------------------------------------


def test_continue_without_a_choice_is_refused(content: GameContent, named: CreationState) -> None:
    blocked = advance(content, named, "Продолжить")
    assert blocked.screen is ScreenId.CREATE_RACE
    assert blocked.notice


def test_exactly_two_traits_are_required(content: GameContent, classed: CreationState) -> None:
    one = walk(content, "Берсерк", "Продолжить", state=classed)
    assert one.screen is ScreenId.CREATE_TRAITS
    assert "ровно 2" in one.notice


def test_a_third_trait_is_refused_and_explained(
    content: GameContent, traited: CreationState
) -> None:
    at_traits = advance(content, traited, "Назад")
    assert at_traits.screen is ScreenId.CREATE_TRAITS
    third = advance(content, at_traits, "Палач")
    assert len(third.draft.trait_ids) == 2
    assert "Снимите одну" in third.notice


def test_a_chosen_trait_can_be_unpicked(content: GameContent, classed: CreationState) -> None:
    picked = walk(content, "Берсерк", state=classed)
    assert picked.draft.trait_ids == ("berserker",)
    unpicked = advance(content, picked, "Выбрано: Берсерк")
    assert unpicked.draft.trait_ids == ()


def test_points_cannot_exceed_the_budget(content: GameContent, traited: CreationState) -> None:
    budget = content.rules.free_points_at_creation
    state = traited
    for _ in range(budget + 3):
        state = advance(content, state, "Сила плюс один")
    assert state.draft.spent_points == budget
    assert "не осталось" in state.notice


def test_points_can_be_reset(content: GameContent, traited: CreationState) -> None:
    spent = advance(content, traited, "Сила плюс один")
    reset = advance(content, spent, "Сбросить очки")
    assert reset.draft.allocated == StatBlock()


def test_confirmation_is_blocked_until_points_are_spent(
    content: GameContent, traited: CreationState
) -> None:
    """The Continue button stays on screen; the refusal is spoken (rule 7)."""
    assert traited.screen is ScreenId.CREATE_POINTS
    blocked = advance(content, traited, "Продолжить")
    assert blocked.screen is ScreenId.CREATE_POINTS
    assert "распределите" in blocked.notice


# --- accessibility behaviour ------------------------------------------


def test_look_repeats_the_screen_without_changing_state(
    content: GameContent, traited: CreationState
) -> None:
    """ "Осмотреться" is the "say that again" button - it must change nothing."""
    looked = advance(content, traited, "Осмотреться")
    assert looked.screen is traited.screen
    assert looked.draft == traited.draft
    assert render(content, looked).text() == render(content, traited).text()


def test_an_unknown_message_is_answered_not_ignored(
    content: GameContent, traited: CreationState
) -> None:
    answered = advance(content, traited, "Вихрь клинков")
    assert answered.screen is traited.screen
    assert answered.notice


def test_state_survives_a_round_trip_through_fsm_data(
    content: GameContent, pointed: CreationState
) -> None:
    restored = CreationState.deserialise(pointed.serialise())
    assert restored.screen is pointed.screen
    assert restored.draft == pointed.draft
    assert restored.stack == pointed.stack


def test_draft_records_allocation_per_stat() -> None:
    draft = CharacterDraft().spend_point(StatCode.STR, budget=2).spend_point(StatCode.LCK, budget=2)
    assert draft.allocated[StatCode.STR] == 1
    assert draft.allocated[StatCode.LCK] == 1
    assert draft.spend_point(StatCode.AGI, budget=2) == draft
