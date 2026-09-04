"""Управа: новое имя и голос — нажатиями, как их нажимает игрок."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)


@pytest.fixture
def elder(content: GameContent) -> Character:
    """Триста уровней, нового имени ещё не просил."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=turning_rules.MIN_LEVEL,
        gold=400,
        unspent_stat_points=2,
    )


def step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, hero, current, message, clock=CLOCK, world_seed=WORLD_SEED)
    return current


@pytest.fixture
def in_chamber(content: GameContent, elder: Character) -> PlayState:
    return step(content, elder, begin(elder), "Мир", "Управа")


def test_the_chamber_stands_in_every_city(content: GameContent, elder: Character) -> None:
    for city in content.cities:
        assert "chamber" in city.services, city.id


def test_a_new_name_resets_the_level_and_keeps_the_haul(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    assert in_chamber.screen is ScreenId.CHAMBER

    confirm = step(content, elder, in_chamber, "Просить новое имя")
    assert confirm.screen is ScreenId.CHAMBER_REMORT

    done = step(content, elder, confirm, "Подтвердить")
    assert done.screen is ScreenId.CHAMBER
    stored = done.pending.character
    assert stored is not None
    assert stored.level == 1
    assert stored.gold == 400
    assert stored.remorts == 1
    assert stored.unspent_stat_points == 2 + turning_rules.STAT_GIFT_PER_REMORT
    assert "Вписанный" in done.notice


def test_nobody_short_of_the_last_level_is_let_in(content: GameContent, elder: Character) -> None:
    young = replace(elder, level=100)
    in_chamber = step(content, young, begin(young), "Мир", "Управа")
    shown = render(content, young, in_chamber, world_seed=WORLD_SEED)
    assert f"с {turning_rules.MIN_LEVEL} уровня" in shown.text()
    assert all("новое имя" not in item.text.lower() for row in shown.rows for item in row)

    # Кнопки нового имени на экране нет вовсе, а нажатая мимо неё уводит не дальше
    # самой управы (доступность, правило 12).
    refused = step(content, young, in_chamber, "Просить новое имя")
    assert refused.screen is ScreenId.CHAMBER


def test_the_question_is_answered_by_those_who_paid_for_a_voice(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    turning = content.open_turning()
    assert turning is not None
    option = turning.options[0]

    asked = step(content, elder, in_chamber, "Голосование")
    assert asked.screen is ScreenId.TURNING
    silent = render(content, elder, asked, world_seed=WORLD_SEED)
    assert all("Ответить" not in item.text for row in silent.rows for item in row)
    assert "Голос дают за уход" in silent.text()

    reborn = replace(elder, remorts=2)
    voting = step(content, reborn, begin(reborn), "Мир", "Управа", "Голосование")
    voted = step(content, reborn, voting, f"Ответить: {option.name}")
    stored = voted.pending.character
    assert stored is not None
    assert (stored.turning_cycle, stored.turning_answer) == (turning.id, option.id)
    assert "весит 2" in voted.notice


def test_the_tally_is_shown_where_the_vote_is_cast(content: GameContent, elder: Character) -> None:
    turning = content.open_turning()
    assert turning is not None
    reborn = replace(
        elder, remorts=1, turning_cycle=turning.id, turning_answer=turning.options[0].id
    )
    asked = step(content, reborn, begin(reborn), "Мир", "Управа", "Голосование")

    shown = render(
        content,
        reborn,
        asked,
        world_seed=WORLD_SEED,
        tally={turning.options[0].id: 4, turning.options[1].id: 1},
    )
    assert "Подано голосов: 5." in shown.text()
    assert f"Впереди: {turning.options[0].name}." in shown.text()
    assert f"Ваш голос отдан за: {turning.options[0].name}." in shown.text()
