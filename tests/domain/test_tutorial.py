"""Вступление: шесть дел, одна битовая маска, засчитать дважды нельзя, и за них платят."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import Equipment
from mmorpg.domain.rules import tutorial
from mmorpg.domain.rules.adventure import apply_tutorial_rewards
from mmorpg.domain.rules.tutorial import TutorialTask


@pytest.fixture
def newcomer() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


def _finish_all_but_one(character: Character) -> Character:
    """Персонаж, которому остался ровно последний шаг обучения."""
    for task in tutorial.ORDER[:-1]:
        marked = tutorial.complete(character, task)
        assert marked is not None
        character = marked
    return character


def test_a_new_character_starts_at_the_first_task(newcomer: Character) -> None:
    assert tutorial.next_task(newcomer) is TutorialTask.STATS
    assert tutorial.done_count(newcomer) == 0
    assert tutorial.finished(newcomer) is False


def test_completing_a_task_moves_to_the_next(newcomer: Character) -> None:
    marked = tutorial.complete(newcomer, TutorialTask.STATS)
    assert marked is not None
    assert tutorial.is_done(marked, TutorialTask.STATS)
    assert tutorial.next_task(marked) is TutorialTask.SKILL_SLOT


def test_the_same_task_is_never_counted_twice(newcomer: Character) -> None:
    """По этому None вызывающий решает, есть ли что говорить."""
    once = tutorial.complete(newcomer, TutorialTask.TRADE)
    assert once is not None
    assert tutorial.complete(once, TutorialTask.TRADE) is None
    assert tutorial.done_count(once) == 1


def test_tasks_can_be_done_out_of_order(newcomer: Character) -> None:
    """Игрока, который подрался раньше, чем прочитал свои характеристики, назад не отправляют."""
    fought = tutorial.complete(newcomer, TutorialTask.FIGHT)
    assert fought is not None
    assert tutorial.next_task(fought) is TutorialTask.STATS
    assert tutorial.is_done(fought, TutorialTask.FIGHT)


def test_all_six_finish_the_introduction(newcomer: Character) -> None:
    character = newcomer
    for task in tutorial.ORDER:
        marked = tutorial.complete(character, task)
        assert marked is not None
        character = marked
    assert tutorial.finished(character)
    assert tutorial.next_task(character) is None
    assert tutorial.done_count(character) == len(tutorial.ORDER)


def test_progress_is_one_number_that_survives_storage(newcomer: Character) -> None:
    """Маска - это то, что лежит в колонке, поэтому она обязана быть простой и небольшой."""
    character = newcomer
    for task in (TutorialTask.STATS, TutorialTask.QUEST):
        marked = tutorial.complete(character, task)
        assert marked is not None
        character = marked
    assert character.tutorial == 0b000101


# --- награда -------------------------------------------------------------


def test_newly_done_names_only_what_flipped() -> None:
    before = tutorial._bit(TutorialTask.STATS)
    after = before | tutorial._bit(TutorialTask.QUEST) | tutorial._bit(TutorialTask.FIGHT)
    assert tutorial.newly_done(before, after) == frozenset({TutorialTask.QUEST, TutorialTask.FIGHT})
    assert tutorial.newly_done(after, after) == frozenset()


def test_a_step_pays_experience_and_gold(newcomer: Character, content: GameContent) -> None:
    done = tutorial.complete(newcomer, TutorialTask.STATS)
    assert done is not None
    payout = apply_tutorial_rewards(content, done, frozenset({TutorialTask.STATS}))
    assert payout.gold == tutorial.STEP_REWARD.gold
    assert payout.character.gold == newcomer.gold + tutorial.STEP_REWARD.gold
    assert payout.character.experience > newcomer.experience
    assert payout.items == ()


def test_nothing_new_means_no_payout(newcomer: Character, content: GameContent) -> None:
    payout = apply_tutorial_rewards(content, newcomer, frozenset())
    assert payout.character is newcomer
    assert payout.gold == 0 and payout.experience == 0


def test_finishing_the_last_step_adds_the_completion_set(
    newcomer: Character, content: GameContent
) -> None:
    almost = _finish_all_but_one(newcomer)
    last = tutorial.ORDER[-1]
    finished = tutorial.complete(almost, last)
    assert finished is not None and tutorial.finished(finished)

    payout = apply_tutorial_rewards(content, finished, frozenset({last}))
    assert payout.gold == tutorial.STEP_REWARD.gold + tutorial.COMPLETION_REWARD.gold
    assert payout.items == tutorial.COMPLETION_REWARD.items
    # Пустые слоты доспеха заполнены комплектом класса.
    for slot in ("head", "hands", "feet"):
        assert payout.character.equipment.item_in(slot) is not None
    assert any("Обучение пройдено" in line for line in payout.lines)


def test_the_completion_set_does_not_overwrite_worn_gear(
    newcomer: Character, content: GameContent
) -> None:
    almost = _finish_all_but_one(newcomer)
    worn = replace(almost, equipment=Equipment().equip("head", "cloth_head@1#uncommon"))
    finished = tutorial.complete(worn, tutorial.ORDER[-1])
    assert finished is not None

    payout = apply_tutorial_rewards(content, finished, frozenset({tutorial.ORDER[-1]}))
    assert payout.character.equipment.item_in("head") == "cloth_head@1#uncommon"


def test_the_completion_set_is_paid_once(newcomer: Character, content: GameContent) -> None:
    finished = tutorial.complete(_finish_all_but_one(newcomer), tutorial.ORDER[-1])
    assert finished is not None
    first = apply_tutorial_rewards(content, finished, frozenset({tutorial.ORDER[-1]}))
    # Ничего нового не закрылось - второй раз completion-набор не идёт.
    again = apply_tutorial_rewards(content, first.character, frozenset())
    assert again.gold == 0
