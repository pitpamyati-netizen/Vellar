"""The short introduction: six things a new player does once.

Not a script and not a cage. The tasks are the ordinary actions of the game -
look at your stats, put a skill in a slot, take a contract, win a fight, use the
shop, hand the contract in - and each is marked done the moment it happens,
whether the player went looking for it or stumbled into it.

The domain knows the tasks and the one number that records them; which screen a
task is done on is a question for ``presentation``. Progress is a bitmask on the
character, so a task cannot be counted twice and the order can change later
without rewriting anybody's save.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from mmorpg.domain.entities.character import Character


class TutorialTask(StrEnum):
    """One thing to do. The order here is the order the player is offered them."""

    STATS = "stats"
    SKILL_SLOT = "skill_slot"
    QUEST = "quest"
    FIGHT = "fight"
    TRADE = "trade"
    HAND_IN = "hand_in"


ORDER: tuple[TutorialTask, ...] = (
    TutorialTask.STATS,
    TutorialTask.SKILL_SLOT,
    TutorialTask.QUEST,
    TutorialTask.FIGHT,
    TutorialTask.TRADE,
    TutorialTask.HAND_IN,
)


def _bit(task: TutorialTask) -> int:
    return 1 << ORDER.index(task)


def is_done(character: Character, task: TutorialTask) -> bool:
    return bool(character.tutorial & _bit(task))


def done_count(character: Character) -> int:
    return sum(1 for task in ORDER if is_done(character, task))


def finished(character: Character) -> bool:
    """Whether every task is behind them. Then the screen stops being offered."""
    return done_count(character) == len(ORDER)


def next_task(character: Character) -> TutorialTask | None:
    """The first task still open, or ``None`` when the introduction is over."""
    for task in ORDER:
        if not is_done(character, task):
            return task
    return None


def complete(character: Character, task: TutorialTask) -> Character | None:
    """Mark a task done. ``None`` when it already was, so nothing is stored twice.

    Callers use the ``None`` to decide whether there is anything worth saying:
    a player who has bought things for a week does not need to be congratulated
    on visiting a shop.
    """
    if is_done(character, task):
        return None
    return replace(character, tutorial=character.tutorial | _bit(task))
