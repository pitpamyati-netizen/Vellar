"""Сборка локации: вечный скелет и сменное поколение округи.

Локация - это от 12 до 24 узлов, сшитых в связный граф, где вход стоит под
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

**К хозину логова ведёт цепочка эпических боёв.** Несколько самых глубоких
внутренних узлов перед боссом - это подступ: закреплённая линейная вереница
эпических противников, и короткие тропы её не обходят (ADR 0033). Драться с самим
боссом по-прежнему необязательно: выход привязан в обход подступа, и мимо логова
есть дорога.

Сборщик не знает ни о времени, ни о хранении: это чистая функция от места и
номера поколения, а результат выбрасывается сразу после отрисовки. Наполнение же
узлов приходит волнами (``domain/rules/nodes.py``).
"""

from __future__ import annotations

import random
from enum import StrEnum

from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import epoch_seed, location_seed, node_seed, rng

MIN_NODES = 16
MAX_NODES = 28
EXTRA_LINK_RATIO = 0.5


def approach_length(count: int) -> int:
    """Сколько эпических узлов-стражей стоит на подступе к боссу.

    Растёт с размером локации, но полого: у большинства локаций один страж, у
    самых больших - два (ADR 0034). Считается от числа узлов, а число узлов -
    часть скелета, поэтому длина подступа тоже постоянна и переживает смену
    поколения.
    """
    return 1 + (count - MIN_NODES) // 8


class _Category(StrEnum):
    """Постоянная роль внутреннего узла. Конкретный вид внутри неё решает поколение."""

    COMBAT = "combat"
    FINDING = "finding"
    SHRINE = "shrine"
    APPROACH = "approach"
    BOSS = "boss"


# Веса постоянных категорий среднего узла. Вход, выход, подступ и босс закреплены
# отдельно, поэтому это касается только того, что между входом и подступом. Находок
# чуть больше боёв: на большой локации череда одинаковых стычек звучит однообразно,
# а разнобой дел - это и есть то разнообразие, ради которого локацию растили.
_INTERIOR_CATEGORIES: tuple[tuple[_Category, int], ...] = (
    (_Category.COMBAT, 45),
    (_Category.FINDING, 46),
    (_Category.SHRINE, 9),
)

# Какие конкретные виды и с каким весом принимает узел категории. Состав находок
# (сколько сбора, сколько тайников) решается на скелете и постоянен - на него
# завязаны задания (ADR 0032). Боевой состав (сколько стай, сколько сильных
# одиночек) перекладывает поколение (``_relay_combat``, ADR 0034). Сильный
# одиночка на случайном боевом узле теперь редок: подступа к боссу с его
# вереницей стражей на смену ритма хватает, а стена эпиков утомляла.
_CATEGORY_KINDS: dict[_Category, tuple[tuple[NodeKind, int], ...]] = {
    _Category.COMBAT: ((NodeKind.BATTLE, 37), (NodeKind.ELITE_BATTLE, 6)),
    _Category.FINDING: ((NodeKind.GATHER, 15), (NodeKind.EVENT, 15), (NodeKind.CACHE, 14)),
    _Category.SHRINE: ((NodeKind.SHRINE, 1),),
    _Category.APPROACH: ((NodeKind.ELITE_BATTLE, 1),),
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
        "Звериная тропа",
        "Нора",
        "Гнездовье",
        "Разорённый пост",
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
        "Осыпь",
        "Промоина",
        "Бурелом",
        "Заброшенный шурф",
        "Каменная гряда",
    ),
    _Category.SHRINE: (
        "Святилище",
        "Замшелый камень",
        "Источник",
        "Ключ из-под камня",
        "Обетный столб",
    ),
    _Category.APPROACH: (
        "Дозорный завал",
        "Тесный проход",
        "Сторожевая нора",
        "Последняя развилка",
        "Осыпь перед логовом",
        "Загороженный лаз",
    ),
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

    ``epoch`` меняет виды узлов (в пределах их категорий), боевой состав локации
    (``_relay_combat``) и короткие тропы. При ``epoch == 0`` и без выработки
    округа стоит в исходном виде.
    """
    seed = location_seed(world_seed, city_id, slot)
    skeleton = rng(seed)

    count = skeleton.randint(MIN_NODES, MAX_NODES)
    approach = approach_length(count)
    approach_start = count - 2 - approach

    categories = _interior_categories(skeleton, count, approach)
    composition = _kind_composition(skeleton, categories)
    tree = _spanning_tree(skeleton, count, approach_start)

    era = rng(epoch_seed(seed, epoch))
    composition = _relay_combat(era, categories, composition)
    kinds = _lay_kinds(era, categories, composition)
    links = _lay_paths(era, count, tree, approach_start)

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


def _interior_categories(source: random.Random, count: int, approach: int) -> tuple[_Category, ...]:
    """Постоянная категория каждого среднего узла.

    Хвост закреплён: несколько узлов подступа, а за ними - босс. Босс на самом
    глубоком внутреннем узле - тот же инвариант, что и раньше: у каждой локации
    ровно один босс и всегда на одном удалении. По дороге к выходу он не стоит -
    выход привязан в обход подступа, поэтому драться с боссом решение, а не
    пошлина. Раз этот хвост закреплён, боевой узел в локации есть всегда.
    """
    population = [category for category, _ in _INTERIOR_CATEGORIES]
    weights = [weight for _, weight in _INTERIOR_CATEGORIES]
    # count - 2 внутренних узлов всего, минус подступ, минус один под босса.
    interior = source.choices(population, weights=weights, k=count - 3 - approach)
    interior.extend(_Category.APPROACH for _ in range(approach))
    interior.append(_Category.BOSS)
    return tuple(interior)


def _kind_composition(
    source: random.Random, categories: tuple[_Category, ...]
) -> dict[_Category, list[NodeKind]]:
    """Состав видов внутри каждой категории на скелете.

    Локация с тремя узлами-находками держит, скажем, «сбор, сбор, тайник» во всех
    своих поколениях - меняется только то, какой из трёх узлов каким стал. Это и
    держит задание «отработайте четыре схрона» выполнимым при любой перекладке
    (ADR 0032). Боевой состав здесь только предварительный: поколение перекладывает
    его заново (``_relay_combat``, ADR 0034).
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


def _relay_combat(
    era: random.Random,
    categories: tuple[_Category, ...],
    composition: dict[_Category, list[NodeKind]],
) -> dict[_Category, list[NodeKind]]:
    """Переложить боевой состав локации под нынешнее поколение округи (ADR 0034).

    Сколько боевых узлов встретит стаю, а сколько - сильного одиночку, решает
    поколение, а не скелет: это и есть смена ритма, которой не хватало, когда
    расстановка боёв была заморожена навсегда. Находок и святилищ это не касается -
    на них завязаны задания (ADR 0032), - а одна стая в локации есть всегда, иначе
    в ней бывали бы только эпические бои.
    """
    slots = sum(1 for item in categories if item is _Category.COMBAT)
    if not slots:
        return composition
    kinds = [kind for kind, _ in _CATEGORY_KINDS[_Category.COMBAT]]
    weights = [weight for _, weight in _CATEGORY_KINDS[_Category.COMBAT]]
    picked = era.choices(kinds, weights=weights, k=slots)
    if NodeKind.BATTLE not in picked:
        picked[era.randrange(slots)] = NodeKind.BATTLE
    composition[_Category.COMBAT] = picked
    return composition


def _lay_kinds(
    era: random.Random,
    categories: tuple[_Category, ...],
    composition: dict[_Category, list[NodeKind]],
) -> tuple[NodeKind, ...]:
    """Раздать состав видов по узлам категории, перемешав места по поколению."""
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


def _spanning_tree(source: random.Random, count: int, approach_start: int) -> list[set[int]]:
    """Остовное дерево, растущее от входа. Постоянное: это и есть выученная дорога.

    Обычные узлы (``1 .. approach_start - 1``) цепляются к любому более раннему,
    поэтому каждый из них достижим от входа. Хвост уложен нарочно:

    * подступ - линейная вереница: первый его узел привязан к какому-то обычному
      узлу, каждый следующий - к предыдущему;
    * босс висит только на последнем узле подступа;
    * выход привязан к обычному узлу, а не к подступу или боссу, - значит, к
      выходу можно пройти, не трогая ни стражей, ни хозина логова (ADR 0033).
    """
    links: list[set[int]] = [set() for _ in range(count)]

    def join(left: int, right: int) -> None:
        links[left].add(right)
        links[right].add(left)

    for index in range(1, approach_start):
        join(index, source.randrange(index))

    join(approach_start, source.randrange(approach_start))
    for index in range(approach_start + 1, count - 2):
        join(index, index - 1)

    join(count - 2, count - 3)  # босс - только за последним стражем
    join(count - 1, source.randrange(approach_start))  # выход - в обход подступа
    return links


def _lay_paths(
    era: random.Random, count: int, tree: list[set[int]], approach_start: int
) -> list[set[int]]:
    """Дерево плюс несколько коротких троп этого поколения.

    Ненаправленные тропы, добавленные поверх остовного дерева, связность сломать
    не могут: выход достижим от входа по самому дереву. Подступа и босса они не
    касаются - иначе срезка провела бы мимо стражей, и подступ перестал бы быть
    подступом (ADR 0033).
    """
    protected = set(range(approach_start, count - 1))  # стражи и босс; выход не в счёт
    links: list[set[int]] = [set(node) for node in tree]
    for _ in range(int(count * EXTRA_LINK_RATIO)):
        left = era.randrange(count)
        right = era.randrange(count)
        if left == right or left in protected or right in protected:
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
