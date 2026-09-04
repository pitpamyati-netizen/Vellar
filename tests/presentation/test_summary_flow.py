"""Экран «Сводка» в городе: направленные дела и переходы к ним (ADR 0053, 0054)."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=40,
        city_id="dusk_harbor",
    )


def _step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    for message in messages:
        state = advance(content, hero, state, message, clock=CLOCK, world_seed=WORLD_SEED)
    return state


def test_the_city_offers_the_summary(content: GameContent, hero: Character) -> None:
    state = _step(content, hero, begin(hero), "Мир", "Сводка")
    assert state.screen is ScreenId.SUMMARY


def test_the_summary_lists_the_deeds(content: GameContent, hero: Character) -> None:
    state = _step(content, hero, begin(hero), "Мир", "Сводка")
    text = render(content, hero, state, world_seed=WORLD_SEED, clock=CLOCK).text()
    assert text.count("Надбавка:") in (4, 5)
    assert "Выбить стаю" in text
    assert "Надбавку за этот переворот ещё не брали." in text


@pytest.mark.parametrize(
    ("button", "target"),
    [
        ("Локации", ScreenId.LOCATION_LIST),
        ("Дорога", ScreenId.WORLD),
        ("Подземелья", ScreenId.DUNGEON),
    ],
)
def test_summary_buttons_lead_to_the_deed(
    content: GameContent, hero: Character, button: str, target: ScreenId
) -> None:
    state = _step(content, hero, begin(hero), "Мир", "Сводка", button)
    assert state.screen is target
