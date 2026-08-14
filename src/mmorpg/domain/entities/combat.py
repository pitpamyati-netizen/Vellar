"""Combat state.

Combat is strictly turn-based and has **no real-time component**: the state simply
waits for the player's action for as long as it takes (accessibility rule 13).

Every state object is immutable; resolving a turn returns a new state plus the
events that happened. Events are structured, not prose - the presentation layer
turns them into Russian sentences, so the domain stays free of interface text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.location import Enemy


class CombatOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"
    DEFEAT = "defeat"
    FLED = "fled"
    AVOIDED = "avoided"

    @property
    def is_over(self) -> bool:
        return self is not CombatOutcome.ONGOING


class ActionKind(StrEnum):
    ATTACK = "attack"
    SKILL = "skill"
    RACIAL = "racial"
    ITEM = "item"
    FLEE = "flee"


class ActionTag(StrEnum):
    """The trace a move leaves behind: press, guard or precision.

    Three tags in a closed circle, so no move is safe and none is useless. The
    Russian words for them are written by the presentation layer.
    """

    PRESS = "press"
    GUARD = "guard"
    PRECISION = "precision"


_COUNTERS: dict[ActionTag, ActionTag] = {
    ActionTag.PRESS: ActionTag.GUARD,
    ActionTag.GUARD: ActionTag.PRECISION,
    ActionTag.PRECISION: ActionTag.PRESS,
}


def counter_to(tag: ActionTag) -> ActionTag:
    """The tag that opens a breach in ``tag``.

    A guard stops a press, precision finds the gap in a guard, and a press is
    inside the reach before precision picks its spot.
    """
    return _COUNTERS[tag]


TRACE_LENGTH = 3


@dataclass(frozen=True, slots=True)
class Trace:
    """The last few tags the player left, newest last.

    Only ``TRACE_LENGTH`` are kept, because that is all the rules ever ask about:
    a repeat builds momentum, three different tags in a row break the exchange.
    """

    tags: tuple[ActionTag, ...] = ()

    @property
    def last(self) -> ActionTag | None:
        return self.tags[-1] if self.tags else None

    @property
    def streak(self) -> int:
        """How many identical tags close the trace."""
        count = 0
        for tag in reversed(self.tags):
            if tag is not self.last:
                break
            count += 1
        return count

    def push(self, tag: ActionTag) -> Trace:
        return Trace(tags=(*self.tags, tag)[-TRACE_LENGTH:])

    def breaks_with(self, tag: ActionTag) -> bool:
        """Whether adding ``tag`` makes the last three tags all different."""
        recent = (*self.tags[-(TRACE_LENGTH - 1) :], tag)
        return len(recent) == TRACE_LENGTH and len(set(recent)) == TRACE_LENGTH


class EventKind(StrEnum):
    DAMAGE = "damage"
    MISS = "miss"
    DODGE = "dodge"
    CRIT = "crit"
    HEAL = "heal"
    SHIELD = "shield"
    EFFECT_APPLIED = "effect_applied"
    EFFECT_EXPIRED = "effect_expired"
    CLEANSED = "cleansed"
    STUNNED = "stunned"
    RESOURCE = "resource"
    ENEMY_DEFEATED = "enemy_defeated"
    PLAYER_DEFEATED = "player_defeated"
    FLED = "fled"
    FLEE_FAILED = "flee_failed"
    AVOIDED = "avoided"
    NOT_ENOUGH_RESOURCE = "not_enough_resource"
    ON_COOLDOWN = "on_cooldown"
    EMPTY_SLOT = "empty_slot"
    TURN_SKIPPED = "turn_skipped"
    MOMENTUM = "momentum"
    BREACH = "breach"
    BREAKTHROUGH = "breakthrough"


@dataclass(frozen=True, slots=True)
class CombatEvent:
    """One thing that happened, in machine-readable form."""

    kind: EventKind
    actor: str = ""
    target: str = ""
    amount: int = 0
    skill_name: str = ""
    effect_name: str = ""
    turns: int = 0


@dataclass(frozen=True, slots=True)
class CombatAction:
    """What the player chose to do this turn."""

    kind: ActionKind
    slot: int | None = None
    item_id: str | None = None
    target: int = 0


@dataclass(frozen=True, slots=True)
class PlayerState:
    name: str
    health: int
    max_health: int
    resource: int
    max_resource: int
    resource_name: str
    effects: EffectStack = field(default_factory=EffectStack)
    cooldowns: Mapping[str, int] = field(default_factory=dict)
    shield: int = 0
    stunned: int = 0
    free_cast: bool = False
    evade_charges: int = 0

    @property
    def alive(self) -> bool:
        return self.health > 0

    def cooldown_of(self, skill_code: str) -> int:
        return self.cooldowns.get(skill_code, 0)

    def with_cooldown(self, skill_code: str, turns: int) -> PlayerState:
        cooldowns = {key: value for key, value in self.cooldowns.items() if value > 0}
        if turns > 0:
            cooldowns[skill_code] = turns
        return replace(self, cooldowns=MappingProxyType(cooldowns))

    def tick_cooldowns(self) -> PlayerState:
        cooldowns = {key: value - 1 for key, value in self.cooldowns.items() if value - 1 > 0}
        return replace(self, cooldowns=MappingProxyType(cooldowns))

    def damaged(self, amount: int) -> tuple[PlayerState, int]:
        """Apply damage through the shield first. Returns the state and the health lost."""
        absorbed = min(self.shield, amount)
        to_health = amount - absorbed
        return (
            replace(
                self,
                shield=self.shield - absorbed,
                health=max(0, self.health - to_health),
            ),
            to_health,
        )

    def healed(self, amount: int) -> tuple[PlayerState, int]:
        restored = min(amount, self.max_health - self.health)
        return replace(self, health=self.health + restored), restored


@dataclass(frozen=True, slots=True)
class EnemyState:
    enemy: Enemy
    health: int
    effects: EffectStack = field(default_factory=EffectStack)
    stunned: int = 0
    index: int = 0

    @property
    def alive(self) -> bool:
        return self.health > 0

    @property
    def name(self) -> str:
        return self.enemy.name

    def damaged(self, amount: int) -> EnemyState:
        return replace(self, health=max(0, self.health - amount))

    @classmethod
    def spawn(cls, enemy: Enemy, index: int) -> EnemyState:
        return cls(enemy=enemy, health=enemy.max_health, index=index)


@dataclass(frozen=True, slots=True)
class CombatState:
    """A whole fight. Immutable; each turn produces a new one."""

    player: PlayerState
    enemies: tuple[EnemyState, ...]
    turn: int = 1
    trace: Trace = field(default_factory=Trace)
    outcome: CombatOutcome = CombatOutcome.ONGOING
    events: tuple[CombatEvent, ...] = ()
    experience: int = 0
    gold: int = 0
    loot: tuple[str, ...] = ()

    @property
    def living_enemies(self) -> tuple[EnemyState, ...]:
        return tuple(enemy for enemy in self.enemies if enemy.alive)

    @property
    def is_over(self) -> bool:
        return self.outcome.is_over

    def enemy_at(self, index: int) -> EnemyState | None:
        for enemy in self.enemies:
            if enemy.index == index and enemy.alive:
                return enemy
        return None

    def first_living(self) -> EnemyState | None:
        living = self.living_enemies
        return living[0] if living else None

    def with_events(self, *events: CombatEvent) -> CombatState:
        return replace(self, events=(*self.events, *events))

    def replace_enemy(self, updated: EnemyState) -> CombatState:
        enemies = tuple(
            updated if enemy.index == updated.index else enemy for enemy in self.enemies
        )
        return replace(self, enemies=enemies)
