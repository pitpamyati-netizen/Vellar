"""Короткое вступление: шесть дел, которые новый игрок делает один раз.

Не сценарий и не клетка. Задачи - это обычные действия игры: посмотреть свои
характеристики, положить умение в слот, взять задание, выиграть бой, зайти в
лавку, сдать задание, - и каждая отмечается сделанной в ту минуту, когда
случилась, искал её игрок или наткнулся на неё сам.

Домен знает задачи и одно число, которое их записывает; на каком экране задача
делается - вопрос к ``presentation``. Ход дела - битовая маска на персонаже,
поэтому задачу нельзя засчитать дважды, а порядок можно поменять позже, ничего
никому не переписывая.

За шаг платят - немного опыта и золота (:data:`STEP_REWARD`), - а за пройденное
целиком дают набор: доспех первой ступени в пустые слоты, зелья, опыт и золото
(:data:`COMPLETION_REWARD`). Что именно начислить, решает
``domain/rules/adventure.apply_tutorial_rewards``; здесь только числа.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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


def newly_done(before_mask: int, after_mask: int) -> frozenset[TutorialTask]:
    """Какие шаги закрылись между двумя масками. По ним начисляют награду."""
    opened = after_mask & ~before_mask
    return frozenset(task for task in ORDER if opened & _bit(task))


def complete(character: Character, task: TutorialTask) -> Character | None:
    """Отметить дело сделанным. ``None``, когда оно уже было, - чтобы ничего не сохранять дважды.

    По этому ``None`` вызывающие решают, есть ли что говорить: игрока, который
    неделю покупал вещи, не нужно поздравлять с посещением лавки.
    """
    if is_done(character, task):
        return None
    return replace(character, tutorial=character.tutorial | _bit(task))


# --- награда --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TutorialReward:
    """Что дают за шаг обучения или за всё сразу. Только числа и ключи."""

    experience: int = 0
    gold: int = 0
    #: Расходники: ключ вещи и сколько. Кладёт в сумку хендлер.
    items: tuple[tuple[str, int], ...] = ()
    #: Дозаполнить пустые слоты снаряжения комплектом класса (только на завершении).
    fill_gear: bool = False


#: Одинаково за каждый из шести шагов - чтобы порядок можно было менять, не трогая
#: баланс. Немного: обучение подталкивает, а не заменяет собой первые уровни.
STEP_REWARD = TutorialReward(experience=25, gold=8)

#: Сверх шести шагов - за то, что обучение пройдено целиком.
COMPLETION_REWARD = TutorialReward(
    experience=100,
    gold=40,
    items=(("small_healing_potion", 3),),
    fill_gear=True,
)

#: Слоты, которые completion-набор дозаполняет, если они пусты. Оружие и нагрудник
#: игрок обычно получает при создании; здесь - шлем, перчатки, сапоги и то из
#: первых двух, чего вдруг нет.
GEAR_SLOTS: tuple[str, ...] = ("weapon", "head", "body", "hands", "feet")
