"""Crafts: gathering raw stuff on the road and making things out of it.

A craft is work, not identity: an adventurer picks a class once and can learn
every craft afterwards (``Narrative.md``, section 2). The definitions come from
``content/crafts.toml``; only the work already done lives on the character,
because a rank is earned, never granted.

Nothing derived is stored. A rank is recomputed from experience every time it is
shown, exactly like a character level.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.stats import StatCode


class CraftKind(StrEnum):
    """Gathering brings materials in; making turns them into items."""

    GATHERING = "gathering"
    MAKING = "making"


@dataclass(frozen=True, slots=True)
class CraftYield:
    """One material a gathering craft can bring back, from ``level`` up."""

    item_id: str
    level: int


@dataclass(frozen=True, slots=True)
class Craft:
    id: str
    name: str
    kind: CraftKind
    stat: StatCode
    description: str
    yields: tuple[CraftYield, ...] = ()

    @property
    def gathers(self) -> bool:
        return self.kind is CraftKind.GATHERING


@dataclass(frozen=True, slots=True)
class RecipeInput:
    item_id: str
    count: int


@dataclass(frozen=True, slots=True)
class Recipe:
    """One thing a making craft knows how to produce."""

    id: str
    craft_id: str
    rank: int
    inputs: tuple[RecipeInput, ...]
    output_id: str
    output_count: int
    experience: int


@dataclass(frozen=True, slots=True)
class QualityTier:
    """How well a batch came out.

    A bag holds item ids and counts, never instances, so quality pays in what
    comes out of the work: ``extra`` more items, and ``refund_percent`` of the
    materials left unspent.
    """

    id: str
    name: str
    extra: int
    refund_percent: int


@dataclass(frozen=True, slots=True)
class CraftRules:
    """The structural numbers of the craft system, shared by content and rules."""

    max_rank: int
    experience_per_rank: int
    rank_names: tuple[str, ...]
    gather_base: int
    gather_per_rank: int
    gather_experience: int
    qualities: tuple[QualityTier, ...]
    good_chance_base: float
    good_chance_per_rank: float
    fine_chance_base: float
    fine_chance_per_rank: float

    def quality(self, quality_id: str) -> QualityTier:
        for tier in self.qualities:
            if tier.id == quality_id:
                return tier
        msg = f"unknown quality {quality_id}"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class CraftProgress:
    """What one character has done in one craft.

    ``gathered_at`` is the unix time of the last gathering. The cooldown is
    personal and short: the road refills for this character on their own clock,
    not on a shared watch everybody had to wait out together.
    """

    experience: int = 0
    gathered_at: int = 0


@dataclass(frozen=True, slots=True)
class CraftLog:
    """Every craft a character has touched. Untouched crafts are simply absent."""

    entries: Mapping[str, CraftProgress] = field(default_factory=dict)

    def progress(self, craft_id: str) -> CraftProgress:
        return self.entries.get(craft_id, CraftProgress())

    def with_experience(self, craft_id: str, gained: int) -> CraftLog:
        current = self.progress(craft_id)
        updated = replace(current, experience=current.experience + max(0, gained))
        return CraftLog(MappingProxyType({**self.entries, craft_id: updated}))

    def with_gathered_at(self, craft_id: str, moment: int) -> CraftLog:
        current = self.progress(craft_id)
        updated = replace(current, gathered_at=moment)
        return CraftLog(MappingProxyType({**self.entries, craft_id: updated}))
