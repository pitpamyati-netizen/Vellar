"""Собранные строения локации.

Ничто в этом модуле не хранится. Локация - чистая функция от своего сида,
поэтому один и тот же сид всегда собирает тот же граф, те же узлы и тех же
противников. См. ``docs/procgen.md``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.damage import DamageType


class EnemyRank(StrEnum):
    """Сколько противник должен продержаться.

    Весь смысл ступени - длина боя: обычный противник падает ходов за три, эпический
    держится вдвое дольше, босс - вчетверо. Больше ступени не отличаются ничем: те
    же теги, те же намерения, та же панель.
    """

    NORMAL = "normal"
    ELITE = "elite"
    BOSS = "boss"

    @property
    def is_long_fight(self) -> bool:
        return self is not EnemyRank.NORMAL


class NodeKind(StrEnum):
    """Что игрок находит в узле."""

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
        """Противник какой ступени ждёт здесь. Имеет смысл только для боевых узлов."""
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
    """Порода, объявленная в содержимом, из которой собирается противник."""

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
    """Конкретный противник, собранный для одной встречи."""

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
        """Держится словом, потому что содержимое и черты говорят об эпических, а не о ступенях."""
        return self.rank is not EnemyRank.NORMAL


@dataclass(frozen=True, slots=True)
class NodeState:
    """Что делят все, кто стоит на одном узле: волна и то, что от неё осталось.

    Узел — не рубильник, переключающийся в «пройден» навсегда. В нём стоит волна из
    нескольких вещей — несколько стай противников, несколько горстей руды, — а
    ``taken`` говорит, сколько из них уже ушло. Когда уходит последняя,
    ``emptied_at`` отмечает минуту, и несколькими минутами позже узел наполняется
    следующей волной (``domain/rules/nodes.py``).
    """

    wave: int = 0
    taken: int = 0
    emptied_at: int = 0

    @property
    def empty(self) -> bool:
        return bool(self.emptied_at)


@dataclass(frozen=True, slots=True)
class LocationState:
    """Что делят все, кто стоит в одной локации.

    Сама карта постоянна и лежит не здесь: она чистая функция от места. Общим
    остаётся состояние каждого узла - какая волна там стоит и сколько от неё
    осталось. Стая, которую убил другой игрок, для тебя тоже мертва, а то, чего не
    тронул никто, всё ещё ждёт.
    """

    nodes: Mapping[int, NodeState] = field(default_factory=dict)

    def node(self, index: int) -> NodeState:
        return self.nodes.get(index, NodeState())

    def with_node(self, index: int, node: NodeState) -> LocationState:
        return LocationState(nodes=MappingProxyType({**self.nodes, index: node}))


@dataclass(frozen=True, slots=True)
class Presence:
    """Один игрок, стоящий в локации, - таким его видят остальные."""

    character_id: int
    name: str
    level: int
    node: int


@dataclass(frozen=True, slots=True)
class LocationNode:
    """Одно приметное место внутри локации."""

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
    """Локация в том виде, в каком она есть всегда.

    У карты нет поколений и она не переворачивается: Луга держат свои узлы, свои
    имена и свои тропы навсегда, и игрок, выучивший дорогу на слух, продолжает её
    знать. Меняется то, что стоит в узлах (``domain/rules/nodes.py``).
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
        msg = "a generated location always has an exit"  # pragma: no cover - инвариант
        raise LookupError(msg)  # pragma: no cover

    def node(self, index: int) -> LocationNode:
        return self.nodes[index]

    def neighbours(self, index: int) -> tuple[LocationNode, ...]:
        return tuple(self.nodes[link] for link in self.nodes[index].links)

    def adjacency(self) -> Mapping[int, tuple[int, ...]]:
        return {node.index: node.links for node in self.nodes}

    def reachable_from(self, start: int = 0) -> frozenset[int]:
        """Обход неориентированного графа в ширину."""
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
