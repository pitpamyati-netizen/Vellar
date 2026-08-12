"""Primary character statistics.

Seven primary stats feed every derived value. This module is pure data: it knows
how stats add up, not where they come from.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum


class StatCode(StrEnum):
    """The seven primary statistics."""

    STR = "STR"  # strength: physical damage, carry weight
    AGI = "AGI"  # agility: accuracy, dodge, initiative
    END = "END"  # endurance: health, armor, resistance
    INT = "INT"  # intellect: magic damage, mana
    WIS = "WIS"  # wisdom: healing, magic resistance, resource regeneration
    CHA = "CHA"  # charisma: prices, quest rewards, influence
    LCK = "LCK"  # luck: crit chance, loot rarity, random effects


@dataclass(frozen=True, slots=True)
class StatBlock:
    """An immutable set of stat values. Addition is component-wise."""

    STR: int = 0
    AGI: int = 0
    END: int = 0
    INT: int = 0
    WIS: int = 0
    CHA: int = 0
    LCK: int = 0

    def __add__(self, other: StatBlock) -> StatBlock:
        return StatBlock(
            STR=self.STR + other.STR,
            AGI=self.AGI + other.AGI,
            END=self.END + other.END,
            INT=self.INT + other.INT,
            WIS=self.WIS + other.WIS,
            CHA=self.CHA + other.CHA,
            LCK=self.LCK + other.LCK,
        )

    def __getitem__(self, code: StatCode) -> int:
        value: int = getattr(self, code.value)
        return value

    def __iter__(self) -> Iterator[tuple[StatCode, int]]:
        for field in fields(self):
            yield StatCode(field.name), getattr(self, field.name)

    @property
    def total(self) -> int:
        """Net sum of all stat values, penalties included."""
        return sum(value for _, value in self)

    @property
    def positive_total(self) -> int:
        return sum(value for _, value in self if value > 0)

    @property
    def penalty_total(self) -> int:
        """Absolute sum of the negative values."""
        return -sum(value for _, value in self if value < 0)

    def with_change(self, code: StatCode, delta: int) -> StatBlock:
        """Return a copy with one stat shifted by ``delta``."""
        values = {name.value: value for name, value in self}
        values[code.value] += delta
        return StatBlock(**values)

    def as_mapping(self) -> dict[StatCode, int]:
        return dict(self)

    @classmethod
    def uniform(cls, value: int) -> StatBlock:
        """A block with the same value in every stat."""
        return cls(STR=value, AGI=value, END=value, INT=value, WIS=value, CHA=value, LCK=value)

    @classmethod
    def from_mapping(cls, values: Mapping[str, int]) -> StatBlock:
        """Build from a ``{"STR": 2, ...}`` mapping. Unknown keys raise KeyError."""
        unknown = set(values) - {code.value for code in StatCode}
        if unknown:
            msg = f"unknown stat codes: {sorted(unknown)}"
            raise KeyError(msg)
        return cls(**{key: int(value) for key, value in values.items()})
