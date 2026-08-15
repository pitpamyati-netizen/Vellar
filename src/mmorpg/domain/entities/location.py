"""Generated location structures.

Nothing in this module is stored. A location is a pure function of its seed, so
the same seed always rebuilds the same graph, the same nodes and the same enemies.
See ``docs/procgen.md``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class EnemyRank(StrEnum):
    """How long an opponent is meant to hold out.

    The whole point of the tier is fight length: an ordinary opponent falls in
    about three turns, an epic one takes twice that, a boss twice again. Nothing
    else about the tiers differs - same tags, same intents, same panel.
    """

    NORMAL = "normal"
    ELITE = "elite"
    BOSS = "boss"

    @property
    def is_long_fight(self) -> bool:
        return self is not EnemyRank.NORMAL


class NodeKind(StrEnum):
    """What a player finds at a node."""

    ENTRANCE = "entrance"
    BATTLE = "battle"
    ELITE_BATTLE = "elite_battle"
    BOSS_BATTLE = "boss_battle"
    GATHER = "gather"
    EVENT = "event"
    CACHE = "cache"
    SHRINE = "shrine"
    EXIT = "exit"

    @property
    def is_combat(self) -> bool:
        return self in {NodeKind.BATTLE, NodeKind.ELITE_BATTLE, NodeKind.BOSS_BATTLE}

    @property
    def rank(self) -> EnemyRank:
        """Which tier of opponent waits here. Only meaningful for combat nodes."""
        match self:
            case NodeKind.ELITE_BATTLE:
                return EnemyRank.ELITE
            case NodeKind.BOSS_BATTLE:
                return EnemyRank.BOSS
            case _:
                return EnemyRank.NORMAL


class EnemyKind(StrEnum):
    BEAST = "beast"
    HUMANOID = "humanoid"
    UNDEAD = "undead"
    ELEMENTAL = "elemental"
    ABERRATION = "aberration"


@dataclass(frozen=True, slots=True)
class EnemyArchetype:
    """Content-defined template an enemy is generated from."""

    id: str
    name: str
    kind: EnemyKind
    biomes: tuple[str, ...]
    health: float
    damage: float
    armor: float
    initiative: float
    loot: tuple[str, ...]

    def fits(self, biome: str) -> bool:
        return "*" in self.biomes or biome in self.biomes


@dataclass(frozen=True, slots=True)
class Enemy:
    """A concrete opponent, generated for one encounter."""

    archetype_id: str
    name: str
    kind: EnemyKind
    level: int
    max_health: int
    damage: int
    armor: int
    initiative: float
    loot: tuple[str, ...]
    gold: int
    rank: EnemyRank = EnemyRank.NORMAL

    @property
    def is_elite(self) -> bool:
        """Kept as a word because content and traits speak of elites, not ranks."""
        return self.rank is not EnemyRank.NORMAL


@dataclass(frozen=True, slots=True)
class LocationState:
    """What everybody standing in one location shares.

    ``generation`` says which map is up; ``cleared`` is the bitmask of nodes
    already worked through, by anyone. The place is common ground: a node another
    player emptied is empty for you too, and when the last one falls the location
    rolls over into its next generation.
    """

    generation: int = 0
    cleared: int = 0


@dataclass(frozen=True, slots=True)
class Presence:
    """One player seen standing in a location, as others see them."""

    character_id: int
    name: str
    level: int
    node: int


@dataclass(frozen=True, slots=True)
class LocationNode:
    """One point of interest inside a location."""

    index: int
    kind: NodeKind
    name: str
    level: int
    links: tuple[int, ...]

    @property
    def is_exit(self) -> bool:
        return self.kind is NodeKind.EXIT


@dataclass(frozen=True, slots=True)
class GeneratedLocation:
    """A location as it stands in one generation of itself.

    The generation changes only when the place is cleared out; until then every
    player who walks in sees the same map, the same nodes and the same enemies.
    """

    city_id: str
    slot: int
    name: str
    biome: str
    level_min: int
    level_max: int
    generation: int
    nodes: tuple[LocationNode, ...]

    @property
    def entrance(self) -> LocationNode:
        return self.nodes[0]

    @property
    def exit_node(self) -> LocationNode:
        for node in self.nodes:
            if node.is_exit:
                return node
        msg = "a generated location always has an exit"  # pragma: no cover - invariant
        raise LookupError(msg)  # pragma: no cover

    def node(self, index: int) -> LocationNode:
        return self.nodes[index]

    def neighbours(self, index: int) -> tuple[LocationNode, ...]:
        return tuple(self.nodes[link] for link in self.nodes[index].links)

    def adjacency(self) -> Mapping[int, tuple[int, ...]]:
        return {node.index: node.links for node in self.nodes}

    def reachable_from(self, start: int = 0) -> frozenset[int]:
        """Breadth-first walk over the undirected graph."""
        seen = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for link in self.nodes[current].links:
                if link not in seen:
                    seen.add(link)
                    queue.append(link)
        return frozenset(seen)

    @property
    def is_connected(self) -> bool:
        return len(self.reachable_from()) == len(self.nodes)
