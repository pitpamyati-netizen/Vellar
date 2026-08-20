"""Location generation.

A location is 8 to 14 nodes joined into a connected graph with the entrance at
index 0 and the exit at the last index. Connectivity and exit reachability are
guaranteed by construction: every node is linked to an earlier node before any
extra shortcuts are added, so the graph is a spanning tree plus edges.

The generator knows nothing about time or storage: it is a pure function of the
place, and the result is thrown away after rendering. The map never rolls over -
a location is a location, not a roll of the dice that lasts until somebody
finishes it. What refills is the contents of its nodes (``domain/rules/nodes.py``).
"""

from __future__ import annotations

import random

from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import location_seed, node_seed, rng

MIN_NODES = 8
MAX_NODES = 14
EXTRA_LINK_RATIO = 0.35

# Node kinds a middle node can take, with their relative weights. The entrance and
# the exit are fixed, so these only apply between them.
INTERIOR_KINDS: tuple[tuple[NodeKind, int], ...] = (
    (NodeKind.BATTLE, 42),
    (NodeKind.GATHER, 16),
    (NodeKind.EVENT, 14),
    (NodeKind.CACHE, 12),
    (NodeKind.ELITE_BATTLE, 9),
    (NodeKind.SHRINE, 7),
)

NODE_NAMES: dict[NodeKind, tuple[str, ...]] = {
    NodeKind.ENTRANCE: ("Вход",),
    NodeKind.BATTLE: ("Стычка", "Засада", "Патруль", "Логово", "Развилка с врагом"),
    NodeKind.ELITE_BATTLE: ("Сильный противник", "Вожак стаи", "Страж прохода"),
    NodeKind.BOSS_BATTLE: ("Хозяин этих мест", "Тронный камень", "Сердце логова"),
    NodeKind.GATHER: ("Заросли", "Жила руды", "Останки", "Полезные травы"),
    NodeKind.EVENT: ("Странная находка", "Развилка", "Следы", "Чужой лагерь"),
    NodeKind.CACHE: ("Тайник", "Сундук", "Схрон", "Забытый мешок"),
    # Без «древнего»: чёрный список Narrative.md, раздел 2. Имена узлов
    # приходят из кода, и тест содержимого их не видит, - тем внимательнее.
    NodeKind.SHRINE: ("Святилище", "Замшелый камень", "Источник"),
    NodeKind.EXIT: ("Выход",),
}


def generate_location(
    *,
    world_seed: str,
    city_id: str,
    slot: int,
    name: str,
    biome: str,
    level_min: int,
    level_max: int,
) -> GeneratedLocation:
    """Build the location standing in one city slot. Always the same one."""
    seed = location_seed(world_seed, city_id, slot)
    source = rng(seed)

    count = source.randint(MIN_NODES, MAX_NODES)
    kinds = _pick_kinds(source, count)
    links = _build_links(source, count)

    nodes = tuple(
        LocationNode(
            index=index,
            kind=kind,
            name=_name_for(node_seed(seed, index), kind),
            level=_level_for(index, count, level_min, level_max),
            links=tuple(sorted(links[index])),
        )
        for index, kind in enumerate(kinds)
    )

    return GeneratedLocation(
        city_id=city_id,
        slot=slot,
        name=name,
        biome=biome,
        level_min=level_min,
        level_max=level_max,
        nodes=nodes,
    )


def _pick_kinds(source: random.Random, count: int) -> tuple[NodeKind, ...]:
    """Entrance first, exit last, weighted kinds in between.

    The deepest interior node - the one at the top of the level band - always holds
    the boss, so every location has exactly one and it is always the same distance
    in. It is not on the way out: the graph has shortcuts, so fighting it is a
    decision, not a toll.
    """
    population = [kind for kind, _ in INTERIOR_KINDS]
    weights = [weight for _, weight in INTERIOR_KINDS]
    interior = source.choices(population, weights=weights, k=count - 2)
    interior[-1] = NodeKind.BOSS_BATTLE
    return (NodeKind.ENTRANCE, *interior, NodeKind.EXIT)


def _build_links(source: random.Random, count: int) -> list[set[int]]:
    """A spanning tree rooted at the entrance, plus a few shortcuts.

    Because node ``i`` always attaches to some node ``j < i``, every node is
    reachable from index 0 - including the exit. Adding undirected shortcuts on top
    cannot break that.
    """
    links: list[set[int]] = [set() for _ in range(count)]
    for index in range(1, count):
        parent = source.randrange(index)
        links[index].add(parent)
        links[parent].add(index)

    for _ in range(int(count * EXTRA_LINK_RATIO)):
        left = source.randrange(count)
        right = source.randrange(count)
        if left == right:
            continue
        links[left].add(right)
        links[right].add(left)
    return links


def _name_for(seed: bytes, kind: NodeKind) -> str:
    options = NODE_NAMES[kind]
    return options[rng(seed).randrange(len(options))]


def _level_for(index: int, count: int, level_min: int, level_max: int) -> int:
    """Nodes get harder the deeper they sit; the exit sits at the top of the band."""
    if count <= 1 or level_max <= level_min:
        return level_min
    step = (level_max - level_min) * index / (count - 1)
    return level_min + int(step)


def node_level_span(location: GeneratedLocation) -> tuple[int, int]:
    levels = [node.level for node in location.nodes]
    return min(levels), max(levels)


def combat_nodes(location: GeneratedLocation) -> tuple[LocationNode, ...]:
    return tuple(node for node in location.nodes if node.kind.is_combat)
