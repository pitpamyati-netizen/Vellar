"""Сборка локации: вечный скелет и сменное поколение округи.

Локация - это от 8 до 14 узлов, сшитых в связный граф, где вход стоит под
номером 0, а выход - последним. Связность и достижимость выхода обеспечены самой
постройкой: каждый узел привязывается к более раннему до того, как добавляются
короткие тропы, поэтому граф - это остовное дерево плюс рёбра.

**Скелет постоянен.** Число узлов, их места, их имена, их уровни и главные тропы
между ними - чистая функция от ``location_seed`` и не меняются никогда. Игрок,
выучивший дорогу на слух - «вход, узел 3 налево, за ним развилка», - продолжает
её знать (ADR 0013).

**Поколение округи сменное.** Поверх скелета лежит ``epoch``: он переставляет
конкретный вид каждого узла внутри его постоянной категории (стычка встаёт там,
где стояла засада; тайник - там, где были следы) и заново стелет короткие тропы.
Номер поколения считает выработка, а не время
(``domain/rules/nodes.location_epoch``, ADR 0032). Категория узла, его имя и
уровень при смене поколения не трогаются.

Сборщик не знает ни о времени, ни о хранении: это чистая функция от места и
номера поколения, а результат выбрасывается сразу после отрисовки. Наполнение же
узлов приходит волнами (``domain/rules/nodes.py``).
"""

from __future__ import annotations

import random
from enum import StrEnum

from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import epoch_seed, location_seed, node_seed, rng

MIN_NODES = 8
MAX_NODES = 14
EXTRA_LINK_RATIO = 0.35


class _Category(StrEnum):
    """Постоянная роль внутреннего узла. Конкретный вид внутри неё решает поколение."""

    COMBAT = "combat"
    FINDING = "finding"
    SHRINE = "shrine"
    BOSS = "boss"


# Веса постоянных категорий среднего узла. Вход, выход и босс закреплены отдельно,
# поэтому это касается только того, что между ними и до босса. Сумма весов
# повторяет прежнее деление видов: бой 42 + 9, находка 16 + 14 + 12, святилище 7.
_INTERIOR_CATEGORIES: tuple[tuple[_Category, int], ...] = (
    (_Category.COMBAT, 51),
    (_Category.FINDING, 42),
    (_Category.SHRINE, 7),
)

# Какие конкретные виды и с каким весом принимает узел категории. Состав узлов
# категории (сколько стычек, сколько сильных боёв) решается на скелете и
# постоянен; поколение только переставляет, какой узел каким из них стал.
_CATEGORY_KINDS: dict[_Category, tuple[tuple[NodeKind, int], ...]] = {
    _Category.COMBAT: ((NodeKind.BATTLE, 42), (NodeKind.ELITE_BATTLE, 9)),
    _Category.FINDING: ((NodeKind.GATHER, 16), (NodeKind.EVENT, 14), (NodeKind.CACHE, 12)),
    _Category.SHRINE: ((NodeKind.SHRINE, 1),),
    _Category.BOSS: ((NodeKind.BOSS_BATTLE, 1),),
}

# Имя узла - от его постоянной категории, поэтому оно честно при любом виде,
# который узел примет в этом поколении: «развилка» одинаково подходит и сбору, и
# тайнику, и событию. Без «древнего»: чёрный список Narrative.md, раздел 2.
_CATEGORY_NAMES: dict[_Category, tuple[str, ...]] = {
    _Category.COMBAT: (
        "Стычка",
        "Засада",
        "Патруль",
        "Логово",
        "Развилка с врагом",
        "Страж прохода",
    ),
    _Category.FINDING: (
        "Развилка",
        "Следы",
        "Заросли",
        "Останки",
        "Чужой лагерь",
        "Прогалина",
        "Старая делянка",
        "Приметное место",
    ),
    _Category.SHRINE: ("Святилище", "Замшелый камень", "Источник"),
    _Category.BOSS: ("Хозяин этих мест", "Тронный камень", "Сердце логова"),
}

_DOOR_NAMES: dict[NodeKind, str] = {NodeKind.ENTRANCE: "Вход", NodeKind.EXIT: "Выход"}


def generate_location(
    *,
    world_seed: str,
    city_id: str,
    slot: int,
    name: str,
    biome: str,
    level_min: int,
    level_max: int,
    epoch: int = 0,
) -> GeneratedLocation:
    """Собрать локацию в одном месте города, в её нынешнем поколении округи.

    ``epoch`` меняет только виды узлов (в пределах их категорий) и короткие
    тропы. При ``epoch == 0`` и без выработки округа стоит в исходном виде.
    """
    seed = location_seed(world_seed, city_id, slot)
    skeleton = rng(seed)

    count = skeleton.randint(MIN_NODES, MAX_NODES)
    categories = _interior_categories(skeleton, count)
    composition = _kind_composition(skeleton, categories)
    tree = _spanning_tree(skeleton, count)

    era = rng(epoch_seed(seed, epoch))
    kinds = _lay_kinds(era, categories, composition)
    links = _lay_paths(era, count, tree)

    ordered_kinds = (NodeKind.ENTRANCE, *kinds, NodeKind.EXIT)
    ordered_categories: tuple[_Category | None, ...] = (None, *categories, None)

    nodes = tuple(
        LocationNode(
            index=index,
            kind=ordered_kinds[index],
            name=_name_for(node_seed(seed, index), ordered_kinds[index], ordered_categories[index]),
            level=_level_for(index, count, level_min, level_max),
            links=tuple(sorted(links[index])),
        )
        for index in range(count)
    )

    return GeneratedLocation(
        city_id=city_id,
        slot=slot,
        name=name,
        biome=biome,
        level_min=level_min,
        level_max=level_max,
        epoch=epoch,
        nodes=nodes,
    )


def _interior_categories(source: random.Random, count: int) -> tuple[_Category, ...]:
    """Постоянная категория каждого среднего узла. Самый глубокий - всегда босс.

    Босс на самом глубоком внутреннем узле - тот же инвариант, что и раньше:
    у каждой локации ровно один босс и всегда на одном удалении, а по дороге к
    выходу он не стоит - в графе есть короткие тропы, поэтому драться с ним
    решение, а не пошлина. Раз этот узел закреплён, боевой узел в локации есть
    всегда.
    """
    population = [category for category, _ in _INTERIOR_CATEGORIES]
    weights = [weight for _, weight in _INTERIOR_CATEGORIES]
    interior = source.choices(population, weights=weights, k=count - 2)
    interior[-1] = _Category.BOSS
    return tuple(interior)


def _kind_composition(
    source: random.Random, categories: tuple[_Category, ...]
) -> dict[_Category, list[NodeKind]]:
    """Постоянный состав видов внутри каждой категории.

    Локация с тремя узлами-находками держит, скажем, «сбор, сбор, тайник» во всех
    своих поколениях - меняется только то, какой из трёх узлов каким стал. Это и
    держит задание «отработайте четыре схрона» выполнимым при любой перекладке.
    """
    composition: dict[_Category, list[NodeKind]] = {}
    for category in _Category:
        slots = sum(1 for item in categories if item is category)
        if not slots:
            continue
        kinds = [kind for kind, _ in _CATEGORY_KINDS[category]]
        weights = [weight for _, weight in _CATEGORY_KINDS[category]]
        composition[category] = source.choices(kinds, weights=weights, k=slots)
    return composition


def _lay_kinds(
    era: random.Random,
    categories: tuple[_Category, ...],
    composition: dict[_Category, list[NodeKind]],
) -> tuple[NodeKind, ...]:
    """Раздать постоянный состав видов по узлам категории, перемешав по поколению."""
    pools: dict[_Category, list[NodeKind]] = {}
    for category, kinds in composition.items():
        shuffled = list(kinds)
        era.shuffle(shuffled)
        pools[category] = shuffled

    cursor = dict.fromkeys(pools, 0)
    result: list[NodeKind] = []
    for category in categories:
        index = cursor[category]
        cursor[category] = index + 1
        result.append(pools[category][index])
    return tuple(result)


def _spanning_tree(source: random.Random, count: int) -> list[set[int]]:
    """Остовное дерево, растущее от входа. Постоянное: это и есть выученная дорога.

    Раз узел ``i`` всегда цепляется к какому-то узлу ``j < i``, каждый узел
    достижим от нулевого - выход в том числе.
    """
    links: list[set[int]] = [set() for _ in range(count)]
    for index in range(1, count):
        parent = source.randrange(index)
        links[index].add(parent)
        links[parent].add(index)
    return links


def _lay_paths(era: random.Random, count: int, tree: list[set[int]]) -> list[set[int]]:
    """Дерево плюс несколько коротких троп этого поколения.

    Ненаправленные тропы, добавленные поверх остовного дерева, связность сломать
    не могут: выход достижим от входа по самому дереву.
    """
    links: list[set[int]] = [set(node) for node in tree]
    for _ in range(int(count * EXTRA_LINK_RATIO)):
        left = era.randrange(count)
        right = era.randrange(count)
        if left == right:
            continue
        links[left].add(right)
        links[right].add(left)
    return links


def _name_for(seed: bytes, kind: NodeKind, category: _Category | None) -> str:
    if category is None:
        return _DOOR_NAMES[kind]
    options = _CATEGORY_NAMES[category]
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
