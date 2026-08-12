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


class NodeKind(StrEnum):
    """What a player finds at a node."""

    ENTRANCE = "entrance"
    BATTLE = "battle"
    ELITE_BATTLE = "elite_battle"
    GATHER = "gather"
    EVENT = "event"
    CACHE = "cache"
    SHRINE = "shrine"
    EXIT = "exit"

    @property
    def is_combat(self) -> bool:
        return self in {NodeKind.BATTLE, NodeKind.ELITE_BATTLE}


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
    is_elite: bool
    loot: tuple[str, ...]
    gold: int


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
    """A location as it exists during one world cycle."""

    city_id: str
    slot: int
    name: str
    biome: str
    level_min: int
    level_max: int
    cycle_index: int
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
