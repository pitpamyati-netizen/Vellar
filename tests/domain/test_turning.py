"""Перерождение: чем платят за Печать, что она открывает и как считают голоса."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, Equipment, GameContent, SkillLoadout
from mmorpg.domain.entities.content import Turning, TurningOption
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import turning


@pytest.fixture
def elder() -> Character:
    """Тот, кто дошёл до конца дороги: триста уровней и есть что заложить."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=turning.MIN_LEVEL,
        equipment=Equipment(MappingProxyType({"trinket": "ring@26#legendary"})),
        loadout=SkillLoadout(
            actives=("warrior_cleave", None, None, None, None, None),
            ranks=MappingProxyType({"warrior_cleave": 5}),
            edges=MappingProxyType({"warrior_cleave": "warrior_cleave_a"}),
        ),
    )


@pytest.fixture
def question() -> Turning:
    return Turning(
        id="toll",
        name="Пошлина",
        question="Сколько берёт Палата?",
        options=(TurningOption(id="less", name="Меньше"), TurningOption(id="more", name="Больше")),
    )


def test_the_turning_is_the_last_level_and_not_before(elder: Character) -> None:
    young = replace(elder, level=turning.MIN_LEVEL - 1)
    assert f"с {turning.MIN_LEVEL} уровня" in turning.refusal(young)
    assert turning.refusal(elder) == ""


def test_a_seal_costs_something_worn(content: GameContent, elder: Character) -> None:
    """Заклад — это вещь с плеча: она уходит из слота и не падает в сумку."""
    offered = turning.pledgeable_items(content, elder)
    assert [item.id for item in offered] == ["ring@26#legendary"]

    sealed = turning.pledge_item(content, elder, "ring@26#legendary")
    assert sealed is not None
    assert sealed.item_id == "ring@26#legendary"
    assert sealed.character.equipment.item_in("trinket") is None
    assert sealed.character.seals == 1
    # Уровень и опыт перерождение не трогает - в этом и весь смысл.
    assert sealed.character.level == elder.level
    assert sealed.character.experience == elder.experience


def test_the_chamber_asks_more_after_every_turning(content: GameContent, elder: Character) -> None:
    assert turning.asking(0) < turning.asking(1) < turning.asking(2)
    # Той же вещью второй раз не откупишься, и не только потому, что её нет.
    once = turning.pledge_item(content, elder, "ring@26#legendary")
    assert once is not None
    again = replace(once.character, equipment=elder.equipment)
    assert turning.pledgeable_items(content, again) == ()
    assert turning.pledge_item(content, again, "ring@26#legendary") is None


def test_a_pledged_edge_is_gone_and_stays_gone(content: GameContent, elder: Character) -> None:
    """Грань выбирают бесплатно, поэтому заложенную нельзя выбрать заново.

    Иначе перерождение стало бы кнопкой «дай Печать»: заложил грань, выбрал её снова,
    заложил опять.
    """
    offered = turning.pledgeable_edges(content, elder)
    assert [skill.code for skill in offered] == ["warrior_cleave"]

    sealed = turning.pledge_edge(content, elder, "warrior_cleave")
    assert sealed is not None
    assert sealed.character.seals == 1
    assert sealed.character.loadout.edge_of("warrior_cleave") is None
    assert sealed.character.loadout.rank_of("warrior_cleave") == 5
    # Ранг остался, грань ушла, и второй раз её Палата не возьмёт.
    assert turning.pledgeable_edges(content, sealed.character) == ()
    assert turning.pledge_edge(content, sealed.character, "warrior_cleave") is None


def test_a_half_learned_edge_is_not_a_pledge(content: GameContent, elder: Character) -> None:
    """Грань брошенного умения отдать не жалко, поэтому её и не берут."""
    shallow = replace(
        elder,
        loadout=replace(elder.loadout, ranks=MappingProxyType({"warrior_cleave": 3})),
    )
    assert turning.pledgeable_edges(content, shallow) == ()


def test_a_seal_opens_a_layer_and_not_a_number(content: GameContent, elder: Character) -> None:
    """Печать открывает доступы: спуск глубже и грань раньше. Силы она не даёт."""
    assert turning.descent_depth(elder) == turning.BASE_DESCENT_DEPTH
    sealed = replace(elder, seals=2)
    assert turning.descent_depth(sealed) == turning.BASE_DESCENT_DEPTH + 2
    assert skill_rules.edge_rank_for(content, sealed) < skill_rules.edge_rank_for(content, elder)
    assert skill_rules.edge_rank_for(content, replace(elder, seals=99)) == 1


def test_a_voice_is_paid_for_with_a_turning(elder: Character, question: Turning) -> None:
    assert not turning.may_answer(elder)
    assert turning.answer(elder, question, "less") is None

    sealed = replace(elder, seals=2)
    voted = turning.answer(sealed, question, "less")
    assert voted is not None
    assert turning.voice(voted) == 2
    assert turning.answered(voted, question) == "less"
    # Тот же ответ второй раз ничего не меняет, другой - меняет.
    assert turning.answer(voted, question, "less") is None
    changed = turning.answer(voted, question, "more")
    assert changed is not None
    assert turning.answered(changed, question) == "more"


def test_an_answer_to_another_question_is_not_counted(elder: Character, question: Turning) -> None:
    """Голос за прошлый цикл в этом не считается, и ответ, которого нет, тоже."""
    stale = replace(elder, seals=1, turning_cycle="gates", turning_answer="less")
    assert turning.answered(stale, question) == ""
    gone = replace(elder, seals=1, turning_cycle="toll", turning_answer="нет такого")
    assert turning.answered(gone, question) == ""


def test_the_lead_needs_a_lead() -> None:
    assert turning.leading({}) == ""
    assert turning.leading({"less": 2, "more": 2}) == ""
    assert turning.leading({"less": 3, "more": 2}) == "less"


def test_nothing_is_pledged_below_the_last_level(content: GameContent, elder: Character) -> None:
    young = replace(elder, level=10)
    assert turning.pledge_item(content, young, "ring@26#legendary") is None
    assert turning.pledge_edge(content, young, "warrior_cleave") is None
