"""Задания: чего просит город и как далеко зашёл персонаж.

Задание в Vellar - оплаченная работа, а не призвание: кто-то называет цену, а
игрок берётся или нет (``Narrative.md``, раздел 4). Описание приходит из
``content/quests.toml``; ход дела лежит на персонаже, потому что два персонажа
одного игрока ведут свои счета порознь.

Хранится только счётчик. Что счётчик *значит* - пять зверей, три обысканных
тайника - это содержимое, и его смотрят заново каждый раз, когда показывают.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class ObjectiveKind(StrEnum):
    """Что считает задание.

    ``KILL`` считает побеждённых противников, при желании одной породы; ``ELITE``
    считает только сильных; ``SEARCH`` считает узлы, отработанные без боя, — сбор,
    тайники и святилища. ``CRAFT`` считает сделанное своими руками, суженное через
    ``target_kind`` до одной вещи: кому-то в городе нужна вещь, а ремесло — это то,
    как её делают.
    """

    KILL = "kill"
    ELITE = "elite"
    SEARCH = "search"
    CRAFT = "craft"


@dataclass(frozen=True, slots=True)
class Quest:
    """Одно задание в том виде, в каком оно записано в содержимом.

    ``follows`` сшивает акт: задание с предшественником не появляется на доске,
    пока тот не оплачен.
    """

    id: str
    city_id: str
    level: int
    name: str
    giver: str
    intro: str
    terms: str
    objective: ObjectiveKind
    target_count: int
    target_kind: str = ""
    reward_gold: int = 0
    reward_experience: int = 0
    reward_item: str = ""
    follows: str = ""
    #: Житель, который его раздаёт (``content.Npc``), если это не безымянная
    #: строка на доске. Задание с нанимателем видно и на доске, и у него самого.
    giver_id: str = ""
    #: Номер локации того же города, где задание делают. Ноль - где угодно.
    #: Без него первое же задание читалось как «обойдите три места» без единого
    #: слова о том, куда идти, и игроки просто не понимали, что делать.
    location_slot: int = 0

    @property
    def summary(self) -> str:
        """Одна строка списка: что считается и сколько за это платят."""
        return f"{self.name}, {self.target_count} по счёту, плата {self.reward_gold}"


@dataclass(frozen=True, slots=True)
class QuestLog:
    """Задания, которые персонаж взял, и те, что он уже закрыл.

    ``taken`` сопоставляет заданию его счётчик. Из ``taken`` задание исчезает,
    когда за него заплатили, а идентификатор переезжает в ``done``, - и оплаченное
    задание нельзя сдать дважды.
    """

    taken: Mapping[str, int] = field(default_factory=dict)
    done: tuple[str, ...] = ()

    def progress(self, quest_id: str) -> int:
        return self.taken.get(quest_id, 0)

    def is_taken(self, quest_id: str) -> bool:
        return quest_id in self.taken

    def is_done(self, quest_id: str) -> bool:
        return quest_id in self.done

    def take(self, quest_id: str) -> QuestLog:
        if self.is_taken(quest_id) or self.is_done(quest_id):
            return self
        return replace(self, taken=MappingProxyType({**self.taken, quest_id: 0}))

    def advanced(self, quest_id: str, amount: int = 1) -> QuestLog:
        """Сдвинуть один счётчик. Неизвестные задания пропускаются, а не заводятся."""
        if quest_id not in self.taken:
            return self
        counted = {**self.taken, quest_id: self.taken[quest_id] + amount}
        return replace(self, taken=MappingProxyType(counted))

    def complete(self, quest_id: str) -> QuestLog:
        if quest_id not in self.taken:
            return self
        remaining = {key: value for key, value in self.taken.items() if key != quest_id}
        return QuestLog(taken=MappingProxyType(remaining), done=(*self.done, quest_id))

    def abandon(self, quest_id: str) -> QuestLog:
        """Вернуть задание. Счётчик теряется, задание - нет."""
        if quest_id not in self.taken:
            return self
        remaining = {key: value for key, value in self.taken.items() if key != quest_id}
        return replace(self, taken=MappingProxyType(remaining))
