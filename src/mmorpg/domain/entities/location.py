"""Generated location structures.

Nothing in this module is stored. A location is a pure function of its seed, so
the same seed always rebuilds the same graph, the same nodes and the same enemies.
See ``docs/procgen.md``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.damage import DamageType


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


#: Чем бьёт порода, когда о роде урона у неё не сказано ничего. Зверь рвёт,
#: гуманоид рубит, нежить бьёт скверной, стихия - чарами, тварь - разумом. Ровно
#: то же делалось раньше делением на «железо и чары», только теперь у каждой
#: породы свой род урона, а не один магический на всех.
DEFAULT_DAMAGE_TYPES: dict[EnemyKind, DamageType] = {
    EnemyKind.BEAST: DamageType.RENDING,
    EnemyKind.HUMANOID: DamageType.SLASHING,
    EnemyKind.UNDEAD: DamageType.NEGATIVE,
    EnemyKind.ELEMENTAL: DamageType.ARCANE,
    EnemyKind.ABERRATION: DamageType.MENTAL,
}


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
    #: Чем бьёт этот архетип. ``None`` - не объявлено, и решает порода
    #: (``DEFAULT_ELEMENTS``): «железом» и «не объявлено» - разные вещи, иначе
    #: каменный истукан не смог бы бить камнем.
    element: DamageType | None = None

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
    element: DamageType = DamageType.SLASHING

    @property
    def is_elite(self) -> bool:
        """Kept as a word because content and traits speak of elites, not ranks."""
        return self.rank is not EnemyRank.NORMAL


@dataclass(frozen=True, slots=True)
class NodeState:
    """What everybody standing at one node shares: the wave and what is left of it.

    A node is not a switch that flips to "пройден" for ever. It holds a wave of
    things - a few packs of opponents, a few handfuls of ore - and ``taken`` says
    how many of them are already gone. When the last one goes, ``emptied_at``
    stamps the moment, and a few minutes later the node fills up with the next
    wave (``domain/rules/nodes.py``).
    """

    wave: int = 0
    taken: int = 0
    emptied_at: int = 0

    @property
    def empty(self) -> bool:
        return bool(self.emptied_at)


@dataclass(frozen=True, slots=True)
class LocationState:
    """What everybody standing in one location shares.

    The map itself is permanent and is not here: it is a pure function of the
    place. What is shared is the state of every node - which wave stands there and
    how much of it is left. A pack another player killed is gone for you too, and
    what neither of you touched is still waiting.
    """

    nodes: Mapping[int, NodeState] = field(default_factory=dict)

    def node(self, index: int) -> NodeState:
        return self.nodes.get(index, NodeState())

    def with_node(self, index: int, node: NodeState) -> LocationState:
        return LocationState(nodes=MappingProxyType({**self.nodes, index: node}))


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
    """A location as it always is.

    The map has no generation and never rolls over: the Meadows keep their nodes,
    their names and their paths for good, and a player who learned the way there
    by ear keeps knowing it. What changes is what stands in the nodes
    (``domain/rules/nodes.py``).
    """

    city_id: str
    slot: int
    name: str
    biome: str
    level_min: int
    level_max: int
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
