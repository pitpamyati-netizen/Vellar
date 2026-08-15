"""The introduction: six tasks, one bitmask, no way to count one twice."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character
from mmorpg.domain.rules import tutorial
from mmorpg.domain.rules.tutorial import TutorialTask


@pytest.fixture
def newcomer() -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior")


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
    """The caller reads the None to decide whether there is anything to say."""
    once = tutorial.complete(newcomer, TutorialTask.TRADE)
    assert once is not None
    assert tutorial.complete(once, TutorialTask.TRADE) is None
    assert tutorial.done_count(once) == 1


def test_tasks_can_be_done_out_of_order(newcomer: Character) -> None:
    """A player who fights before reading their stats is not sent back."""
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
    """The mask is what the column holds, so it must be plain and small."""
    character = newcomer
    for task in (TutorialTask.STATS, TutorialTask.QUEST):
        marked = tutorial.complete(character, task)
        assert marked is not None
        character = marked
    assert character.tutorial == 0b000101
