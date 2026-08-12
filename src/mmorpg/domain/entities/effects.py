"""Temporary effects and the stack that holds them.

Effects are keyed by id. Applying the same effect again **refreshes** it - it never
stacks a second copy, so a player spamming a buff cannot double its bonus. That
rule is asserted by ``tests/domain/test_effects.py``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ActiveEffect:
    """A modifier bundle with a lifetime measured in turns."""

    id: str
    name: str
    modifiers: Mapping[str, float]
    turns_left: int
    source: str = ""
    # Whether the effect helps its holder. Set explicitly by whoever creates the
    # effect: the sign of a modifier is not enough to tell (a positive
    # damage_taken_percent is a penalty).
    beneficial: bool = True

    def ticked(self) -> ActiveEffect:
        return replace(self, turns_left=self.turns_left - 1)

    @property
    def expired(self) -> bool:
        return self.turns_left <= 0


@dataclass(frozen=True, slots=True)
class EffectStack:
    """Immutable collection of active effects, keyed by effect id."""

    effects: Mapping[str, ActiveEffect] = field(default_factory=dict)

    def __iter__(self) -> Iterator[ActiveEffect]:
        return iter(self.effects.values())

    def __len__(self) -> int:
        return len(self.effects)

    def __contains__(self, effect_id: str) -> bool:
        return effect_id in self.effects

    def apply(self, effect: ActiveEffect) -> EffectStack:
        """Add or refresh an effect.

        Re-applying keeps the longer of the two remaining durations and does not
        add the modifiers twice.
        """
        current = self.effects.get(effect.id)
        merged = (
            effect
            if current is None
            else replace(effect, turns_left=max(effect.turns_left, current.turns_left))
        )
        return EffectStack(MappingProxyType({**self.effects, effect.id: merged}))

    def remove(self, effect_id: str) -> EffectStack:
        remaining = {key: value for key, value in self.effects.items() if key != effect_id}
        return EffectStack(MappingProxyType(remaining))

    def cleanse(self, count: int, *, beneficial: bool = False) -> EffectStack:
        """Remove up to ``count`` effects, oldest first.

        By default it strips penalties, which is what every cleansing skill does.
        """
        remaining = dict(self.effects)
        for effect_id, effect in self.effects.items():
            if count <= 0:
                break
            if effect.beneficial is beneficial:
                del remaining[effect_id]
                count -= 1
        return EffectStack(MappingProxyType(remaining))

    def tick(self) -> EffectStack:
        """Advance one turn and drop whatever expired."""
        advanced = {
            effect_id: ticked
            for effect_id, effect in self.effects.items()
            if not (ticked := effect.ticked()).expired
        }
        return EffectStack(MappingProxyType(advanced))

    def modifiers(self) -> dict[str, float]:
        """Sum of every active effect's modifiers."""
        total: dict[str, float] = {}
        for effect in self.effects.values():
            for key, value in effect.modifiers.items():
                total[key] = total.get(key, 0.0) + value
        return total

    def penalties(self) -> tuple[ActiveEffect, ...]:
        return tuple(effect for effect in self.effects.values() if not effect.beneficial)
