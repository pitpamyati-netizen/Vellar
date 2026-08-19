"""Contracts: what a city asks for, and how far a character has got with it.

A contract in Vellar is a paid job, not a calling: somebody names a price, the
player either takes it or does not (``Narrative.md``, section 4). The definition
comes from ``content/quests.toml``; the progress lives on the character, because
two characters of the same player keep their own ledgers.

Only the counter is stored. What a counter *means* - five beasts, three searched
caches - is content, and is looked up again every time it is shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class ObjectiveKind(StrEnum):
    """What the contract counts.

    ``KILL`` counts defeated opponents, optionally of one kind; ``ELITE`` counts
    the strong ones only; ``SEARCH`` counts nodes worked through without a fight -
    gathering, caches and shrines. ``CRAFT`` counts things made with your own
    hands, narrowed by ``target_kind`` to one item id: somebody in the city needs
    a thing, and a craft is how it gets made (Roadmap, "Риски").
    """

    KILL = "kill"
    ELITE = "elite"
    SEARCH = "search"
    CRAFT = "craft"


@dataclass(frozen=True, slots=True)
class Quest:
    """One contract, as written in content.

    ``follows`` chains the act together: a contract with a predecessor stays off
    the board until that one is paid out.
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
    #: строка на доске. Подряд с нанимателем виден и на доске, и у него самого.
    giver_id: str = ""
    #: Номер локации того же города, где подряд делают. Ноль - где угодно.
    #: Без него первый же подряд читался как «обойдите три места» без единого
    #: слова о том, куда идти, и игроки просто не понимали, что делать.
    location_slot: int = 0

    @property
    def summary(self) -> str:
        """One line for a list entry: what is counted and how much it pays."""
        return f"{self.name}, {self.target_count} по счёту, плата {self.reward_gold}"


@dataclass(frozen=True, slots=True)
class QuestLog:
    """Contracts a character took, and the ones they already closed.

    ``taken`` maps a contract id to its counter. A contract disappears from
    ``taken`` when it is paid out and its id moves to ``done`` - so a paid
    contract can never be handed in twice.
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
        """Move one counter. Unknown contracts are ignored, not created."""
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
        """Give a contract back. The counter is lost, the contract is not."""
        if quest_id not in self.taken:
            return self
        remaining = {key: value for key, value in self.taken.items() if key != quest_id}
        return replace(self, taken=MappingProxyType(remaining))
