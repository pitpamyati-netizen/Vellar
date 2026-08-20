"""Палата: заклад, Печать и голос — нажатиями, как их нажимает игрок."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, Equipment, GameContent, SkillLoadout
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)


@pytest.fixture
def elder(content: GameContent) -> Character:
    """Триста уровней, надетая вещь выше запроса и умение с гранью на полном ранге."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=turning_rules.MIN_LEVEL,
        equipment=Equipment(MappingProxyType({"trinket": "ashen_signet"})),
        loadout=SkillLoadout(
            actives=("warrior_cleave", None, None, None, None, None),
            ranks=MappingProxyType({"warrior_cleave": 5}),
            edges=MappingProxyType({"warrior_cleave": "warrior_cleave_a"}),
        ),
    )


def step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, hero, current, message, clock=CLOCK, world_seed=WORLD_SEED)
    return current


@pytest.fixture
def in_chamber(content: GameContent, elder: Character) -> PlayState:
    return step(content, elder, begin(elder), "Мир", "Дубно", "Палата")


def test_the_chamber_stands_in_every_city(content: GameContent, elder: Character) -> None:
    for city in content.cities:
        assert "chamber" in city.services, city.id


def test_a_turning_takes_the_thing_and_gives_a_seal(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    assert in_chamber.screen is ScreenId.CHAMBER

    listed = step(content, elder, in_chamber, "Совершить Оборот")
    assert listed.screen is ScreenId.CHAMBER_PLEDGE
    shown = render(content, elder, listed, world_seed=WORLD_SEED)
    pressed = next(
        item.text for row in shown.rows for item in row if item.text.startswith("Вещь: ")
    )

    sealed = step(content, elder, listed, pressed)
    assert sealed.screen is ScreenId.CHAMBER
    stored = sealed.pending.character
    assert stored is not None
    assert stored.seals == 1
    assert stored.equipment.item_in("trinket") is None
    assert stored.level == elder.level
    assert "Оборот совершён" in sealed.notice


def test_nobody_short_of_the_last_level_is_let_in(content: GameContent, elder: Character) -> None:
    young = replace(elder, level=100)
    in_chamber = step(content, young, begin(young), "Мир", "Дубно", "Палата")
    shown = render(content, young, in_chamber, world_seed=WORLD_SEED)
    assert f"с {turning_rules.MIN_LEVEL} уровня" in shown.text()
    assert all("Оборот" not in item.text for row in shown.rows for item in row)

    # Кнопки заклада на экране нет вовсе, и нажатая мимо неё уводит не дальше
    # самой Палаты (доступность, правило 12).
    refused = step(content, young, in_chamber, "Совершить Оборот")
    assert refused.screen is ScreenId.CHAMBER


def test_the_question_is_answered_by_those_who_paid_for_a_voice(
    content: GameContent, elder: Character, in_chamber: PlayState
) -> None:
    turning = content.open_turning()
    assert turning is not None
    option = turning.options[0]

    asked = step(content, elder, in_chamber, "Счётный вопрос")
    assert asked.screen is ScreenId.TURNING
    # Без Печати кнопок с ответами на экране нет, а нажатая мимо клавиатуры
    # объясняет, чего не хватает.
    silent = render(content, elder, asked, world_seed=WORLD_SEED)
    assert all("Ответить" not in item.text for row in silent.rows for item in row)
    assert "Голос дают за Оборот" in silent.text()

    sealed = replace(elder, seals=2)
    voting = step(content, sealed, begin(sealed), "Мир", "Дубно", "Палата", "Счётный вопрос")
    voted = step(content, sealed, voting, f"Ответить: {option.name}")
    stored = voted.pending.character
    assert stored is not None
    assert (stored.turning_cycle, stored.turning_answer) == (turning.id, option.id)
    assert "весит 2" in voted.notice


def test_the_tally_is_shown_where_the_vote_is_cast(content: GameContent, elder: Character) -> None:
    turning = content.open_turning()
    assert turning is not None
    sealed = replace(elder, seals=1, turning_cycle=turning.id, turning_answer=turning.options[0].id)
    asked = step(content, sealed, begin(sealed), "Мир", "Дубно", "Палата", "Счётный вопрос")

    shown = render(
        content,
        sealed,
        asked,
        world_seed=WORLD_SEED,
        tally={turning.options[0].id: 4, turning.options[1].id: 1},
    )
    assert "Подано голосов: 5." in shown.text()
    assert f"Впереди: {turning.options[0].name}." in shown.text()
    assert f"Ваш голос отдан за: {turning.options[0].name}." in shown.text()


def test_the_descent_screen_counts_the_layers_a_seal_opened(
    content: GameContent, elder: Character
) -> None:
    sealed = replace(elder, seals=2)
    below = step(content, sealed, begin(sealed), "Мир", "Дубно", "Подземелья")
    shown = render(content, sealed, below, world_seed=WORLD_SEED)
    assert f"{turning_rules.descent_depth(sealed)} схваток подряд" in shown.text()
