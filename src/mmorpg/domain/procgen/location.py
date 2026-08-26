"""Сборка локации.

Локация - это от 8 до 14 узлов, сшитых в связный граф, где вход стоит под
номером 0, а выход - последним. Связность и достижимость выхода обеспечены самой
постройкой: каждый узел привязывается к более раннему до того, как добавляются
короткие тропы, поэтому граф - это остовное дерево плюс рёбра.

Сборщик не знает ни о времени, ни о хранении: это чистая функция от места, а
результат выбрасывается сразу после отрисовки. Карта не переворачивается никогда
- локация это локация, а не бросок костей, который держится, пока кто-нибудь его
не пройдёт. Наполняется же содержимое её узлов (``domain/rules/nodes.py``).
"""

from __future__ import annotations

import random

from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import location_seed, node_seed, rng

MIN_NODES = 8
MAX_NODES = 14
EXTRA_LINK_RATIO = 0.35

# Виды узлов, которыми может быть средний узел, с их весами. Вход и выход закреплены,
# поэтому это касается только того, что между ними.
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
    """Собрать локацию, стоящую в одном месте города. Всегда одну и ту же."""
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
    """Вход первым, выход последним, между ними - взвешенные виды узлов.

    Самый глубокий внутренний узел - тот, что стоит на верху полосы уровней, - всегда
    держит босса, поэтому у каждой локации босс ровно один и всегда на одном и том же
    удалении. По дороге к выходу он не стоит: в графе есть короткие тропы, поэтому
    драться с ним - решение, а не пошлина.
    """
    population = [kind for kind, _ in INTERIOR_KINDS]
    weights = [weight for _, weight in INTERIOR_KINDS]
    interior = source.choices(population, weights=weights, k=count - 2)
    interior[-1] = NodeKind.BOSS_BATTLE
    return (NodeKind.ENTRANCE, *interior, NodeKind.EXIT)


def _build_links(source: random.Random, count: int) -> list[set[int]]:
    """Остовное дерево, растущее от входа, плюс несколько коротких троп.

    Раз узел ``i`` всегда цепляется к какому-то узлу ``j < i``, каждый узел
    достижим от нулевого - выход в том числе. Ненаправленные тропы, добавленные
    сверху, сломать это не могут.
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
    """Чем глубже узел, тем он тяжелее; выход стоит на верху полосы."""
    if count <= 1 or level_max <= level_min:
        return level_min
    step = (level_max - level_min) * index / (count - 1)
    return level_min + int(step)


def node_level_span(location: GeneratedLocation) -> tuple[int, int]:
    levels = [node.level for node in location.nodes]
    return min(levels), max(levels)


def combat_nodes(location: GeneratedLocation) -> tuple[LocationNode, ...]:
    return tuple(node for node in location.nodes if node.kind.is_combat)
