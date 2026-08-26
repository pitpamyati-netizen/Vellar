"""Короткое вступление: шесть дел, которые новый игрок делает один раз.

Не сценарий и не клетка. Задачи - это обычные действия игры: посмотреть свои
характеристики, положить умение в слот, взять задание, выиграть бой, зайти в
лавку, сдать задание, - и каждая отмечается сделанной в ту минуту, когда
случилась, искал её игрок или наткнулся на неё сам.

Домен знает задачи и одно число, которое их записывает; на каком экране задача
делается - вопрос к ``presentation``. Ход дела - битовая маска на персонаже,
поэтому задачу нельзя засчитать дважды, а порядок можно поменять позже, ничего
никому не переписывая.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from mmorpg.domain.entities.character import Character


class TutorialTask(StrEnum):
    """Одно дело. Порядок здесь - тот, в каком их предлагают игроку."""

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
    """Всё ли позади. Тогда экран перестают предлагать."""
    return done_count(character) == len(ORDER)


def next_task(character: Character) -> TutorialTask | None:
    """Первое ещё не сделанное дело или ``None``, когда вступление окончено."""
    for task in ORDER:
        if not is_done(character, task):
            return task
    return None


def complete(character: Character, task: TutorialTask) -> Character | None:
    """Отметить дело сделанным. ``None``, когда оно уже было, - чтобы ничего не сохранять дважды.

    По этому ``None`` вызывающие решают, есть ли что говорить: игрока, который
    неделю покупал вещи, не нужно поздравлять с посещением лавки.
    """
    if is_done(character, task):
        return None
    return replace(character, tutorial=character.tutorial | _bit(task))
