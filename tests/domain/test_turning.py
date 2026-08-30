"""Новое имя: что даёт уход, какой титул и как совет считает голоса."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character
from mmorpg.domain.entities.content import Turning, TurningOption
from mmorpg.domain.rules import turning


@pytest.fixture
def elder() -> Character:
    """Тот, кто дошёл до конца дороги: триста уровней, нового имени ещё не просил."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=turning.MIN_LEVEL,
        gold=500,
        experience=1_000,
        unspent_stat_points=3,
    )


@pytest.fixture
def question() -> Turning:
    return Turning(
        id="toll",
        name="Доля в казну",
        question="Сколько берёт Престол?",
        options=(TurningOption(id="less", name="Меньше"), TurningOption(id="more", name="Больше")),
    )


def test_a_new_name_is_the_last_level_and_not_before(elder: Character) -> None:
    young = replace(elder, level=turning.MIN_LEVEL - 1)
    assert f"с {turning.MIN_LEVEL} уровня" in turning.refusal(young)
    assert turning.refusal(elder) == ""
    assert turning.become(young) is None


def test_a_new_name_resets_the_level_and_keeps_the_haul(elder: Character) -> None:
    reborn = turning.become(elder)
    assert reborn is not None
    hero = reborn.character
    assert hero.level == 1
    assert hero.experience == 0
    assert hero.gold == 500
    assert hero.remorts == 1
    # Прибавка идёт в тот же нераспределённый пул.
    assert hero.unspent_stat_points == 3 + turning.STAT_GIFT_PER_REMORT
    assert reborn.stat_points == turning.STAT_GIFT_PER_REMORT
    assert reborn.title == "Вписанный"


def test_the_stat_gift_has_a_ceiling(elder: Character) -> None:
    assert turning.stat_gift(0) == turning.STAT_GIFT_PER_REMORT
    assert turning.stat_gift(turning.STAT_GIFT_CAP - 1) == turning.STAT_GIFT_PER_REMORT
    assert turning.stat_gift(turning.STAT_GIFT_CAP) == 0

    capped = turning.become(replace(elder, remorts=turning.STAT_GIFT_CAP))
    assert capped is not None
    assert capped.stat_points == 0
    assert capped.character.unspent_stat_points == 3


def test_the_title_ladder_runs_out_and_holds(elder: Character) -> None:
    assert turning.title(0) == ""
    assert turning.title(1) == turning.TITLES[0]
    assert turning.title(len(turning.TITLES)) == turning.TITLES[-1]
    assert turning.title(len(turning.TITLES) + 5) == turning.TITLES[-1]


def test_a_voice_is_paid_for_with_a_new_name(elder: Character, question: Turning) -> None:
    assert not turning.may_answer(elder)
    assert turning.answer(elder, question, "less") is None

    reborn = replace(elder, remorts=2)
    voted = turning.answer(reborn, question, "less")
    assert voted is not None
    assert turning.voice(voted) == 2
    assert turning.answered(voted, question) == "less"
    # Тот же ответ второй раз ничего не меняет, другой - меняет.
    assert turning.answer(voted, question, "less") is None
    changed = turning.answer(voted, question, "more")
    assert changed is not None
    assert turning.answered(changed, question) == "more"


def test_the_voice_is_capped(elder: Character) -> None:
    assert turning.voice(replace(elder, remorts=turning.COUNCIL_VOTE_CAP + 3)) == (
        turning.COUNCIL_VOTE_CAP
    )


def test_an_answer_to_another_question_is_not_counted(elder: Character, question: Turning) -> None:
    """Голос за прошлый цикл в этом не считается, и ответ, которого нет, тоже."""
    stale = replace(elder, remorts=1, turning_cycle="gates", turning_answer="less")
    assert turning.answered(stale, question) == ""
    gone = replace(elder, remorts=1, turning_cycle="toll", turning_answer="нет такого")
    assert turning.answered(gone, question) == ""


def test_the_lead_needs_a_lead() -> None:
    assert turning.leading({}) == ""
    assert turning.leading({"less": 2, "more": 2}) == ""
    assert turning.leading({"less": 3, "more": 2}) == "less"
