"""Сборка локации: перекладывается целиком, босс держит конец.

Локация - это от 16 до 28 узлов, сшитых в связный граф, где вход стоит под
номером 0, а выход - последним. Связность и достижимость выхода обеспечены самой
постройкой: каждый узел привязывается к более раннему до того, как добавляются
короткие тропы, поэтому граф - это остовное дерево плюс рёбра.

**Расположение узлов перекладывается поколениями.** Округа стоит, пока в ней
есть что брать, и заселяется заново, когда её выработали: другое остовное дерево,
места встают в другом порядке, короткие тропы ложатся иначе, у мест другие
имена. Номер поколения считает выработка, а не время
(``domain/rules/nodes.location_epoch``, ADR 0035).

**Постоянного в локации - только то, чего игрок не слышит как карту.** От
``location_seed`` зависят число узлов, набор категорий среди них (сколько боёв,
сколько находок, сколько святилищ) и кривая уровней по глубине. Набор видов
находок («сбор, сбор, тайник») тоже постоянен - на нём держатся ``search``-задания
(ADR 0035). Всё это - функция места, не поколения.

**Босс держит конец локации.** Хозяин логова всегда стоит на самом глубоком
внутреннем узле (``count - 2``), за один шаг до выхода, и у каждой локации он
ровно один. По дороге к выходу он не стоит: выход привязан к обычному узлу в
обход логова, поэтому драться с боссом - решение, а не пошлина.

Сборщик не знает ни о времени, ни о хранении: это чистая функция от места и
номера поколения, а результат выбрасывается сразу после отрисовки. Наполнение же
узлов приходит волнами (``domain/rules/nodes.py``).
"""

from __future__ import annotations

import random
from enum import StrEnum

from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import epoch_seed, location_seed, rng

MIN_NODES = 16
MAX_NODES = 28
EXTRA_LINK_RATIO = 0.5


class _Category(StrEnum):
    """Роль внутреннего узла. Конкретный вид внутри неё решает поколение."""

    COMBAT = "combat"
    FINDING = "finding"
    SHRINE = "shrine"
    BOSS = "boss"


# Веса категорий среднего узла. Вход, выход и босс закреплены отдельно, поэтому
# это касается только того, что между входом и боссом. Находок чуть больше боёв:
# на большой локации череда одинаковых стычек звучит однообразно, а разнобой дел -
# это и есть то разнообразие, ради которого локацию растили.
_INTERIOR_CATEGORIES: tuple[tuple[_Category, int], ...] = (
    (_Category.COMBAT, 45),
    (_Category.FINDING, 46),
    (_Category.SHRINE, 9),
)

# Какие конкретные виды и с каким весом принимает узел категории. Состав находок
# (сколько сбора, сколько тайников) постоянен - на него завязаны задания
# (ADR 0035). Боевой состав (сколько стай, сколько сильных одиночек) перекладывает
# поколение (``_relay_combat``). Сильный одиночка на случайном боевом узле редок:
# смену ритма несёт босс в глубине, а стена эпиков утомляла.
_CATEGORY_KINDS: dict[_Category, tuple[tuple[NodeKind, int], ...]] = {
    _Category.COMBAT: ((NodeKind.BATTLE, 37), (NodeKind.ELITE_BATTLE, 6)),
    _Category.FINDING: ((NodeKind.GATHER, 15), (NodeKind.EVENT, 15), (NodeKind.CACHE, 14)),
    _Category.SHRINE: ((NodeKind.SHRINE, 1),),
    _Category.BOSS: ((NodeKind.BOSS_BATTLE, 1),),
}

# Имя узла - из пула его категории, поэтому оно честно при любом виде, который
# узел примет в этом поколении: «развилка» одинаково подходит и сбору, и тайнику,
# и событию. Без «древнего»: чёрный список Narrative.md, раздел 2.
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
    """Собрать локацию в одном месте города, в её нынешнем поколении.

    ``epoch`` решает всю раскладку: остовное дерево, где какая категория и вид
    узла стоит, боевой состав, короткие тропы и имена узлов. От места (не от
    поколения) зависят только число узлов, набор категорий и видов находок среди
    них и кривая уровней. При ``epoch == 0`` и без выработки округа стоит в
    исходном виде.
    """
    seed = location_seed(world_seed, city_id, slot)
    base = rng(seed)
    era = rng(epoch_seed(seed, epoch))

    count = base.randint(MIN_NODES, MAX_NODES)
    categories = _interior_categories(base, era, count)
    composition = _kind_composition(base, categories)

    composition = _relay_combat(era, categories, composition)
    kinds = _lay_kinds(era, categories, composition)
    names = tuple(_pick_name(era, category) for category in categories)
    links = _lay_paths(era, count, _spanning_tree(era, count))

    ordered_kinds = (NodeKind.ENTRANCE, *kinds, NodeKind.EXIT)
    ordered_names = (_DOOR_NAMES[NodeKind.ENTRANCE], *names, _DOOR_NAMES[NodeKind.EXIT])

    nodes = tuple(
        LocationNode(
            index=index,
            kind=ordered_kinds[index],
            name=ordered_names[index],
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


def _interior_categories(
    base: random.Random, era: random.Random, count: int
) -> tuple[_Category, ...]:
    """Категория каждого среднего узла, боссом в конце.

    Сколько среди внутренних узлов боёв, находок и святилищ - функция места
    (``base``): набор не должен вырождаться от поколения к поколению, иначе
    ``search``-задание на вид узла встало бы намертво. А вот в каком узле что
    стоит - решает поколение (``era`` тасует). Босс дописан последним и в тасовку
    не идёт: он всегда на самом глубоком внутреннем узле (``count - 2``). Раз этот
    хвост закреплён, боевой узел в локации есть всегда.
    """
    population = [category for category, _ in _INTERIOR_CATEGORIES]
    weights = [weight for _, weight in _INTERIOR_CATEGORIES]
    # count - 2 внутренних узлов всего, минус один под босса.
    interior = base.choices(population, weights=weights, k=count - 3)
    if _Category.COMBAT not in interior:
        interior[base.randrange(len(interior))] = _Category.COMBAT
    era.shuffle(interior)
    return (*interior, _Category.BOSS)


def _kind_composition(
    base: random.Random, categories: tuple[_Category, ...]
) -> dict[_Category, list[NodeKind]]:
    """Состав видов внутри каждой категории - функция места.

    Локация с тремя узлами-находками держит, скажем, «сбор, сбор, тайник» во всех
    своих поколениях - меняется только то, какой из трёх узлов каким стал. Это и
    держит задание «отработайте четыре схрона» выполнимым при любой перекладке
    (ADR 0035). Боевой состав здесь только предварительный: поколение перекладывает
    его заново (``_relay_combat``).
    """
    composition: dict[_Category, list[NodeKind]] = {}
    for category in _Category:
        slots = sum(1 for item in categories if item is category)
        if not slots:
            continue
        kinds = [kind for kind, _ in _CATEGORY_KINDS[category]]
        weights = [weight for _, weight in _CATEGORY_KINDS[category]]
        composition[category] = base.choices(kinds, weights=weights, k=slots)
    return composition


def _relay_combat(
    era: random.Random,
    categories: tuple[_Category, ...],
    composition: dict[_Category, list[NodeKind]],
) -> dict[_Category, list[NodeKind]]:
    """Переложить боевой состав локации под нынешнее поколение (ADR 0035).

    Сколько боевых узлов встретит стаю, а сколько - сильного одиночку, решает
    поколение: это и есть смена ритма. Находок и святилищ это не касается - на них
    завязаны задания, - а одна стая в локации есть всегда, иначе в ней бывали бы
    только эпические бои.
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


def _spanning_tree(era: random.Random, count: int) -> list[set[int]]:
    """Остовное дерево, растущее от входа. Перекладывается каждым поколением.

    * Обычные внутренние узлы (``1 .. count - 3``) цепляются к любому более
      раннему, поэтому каждый из них достижим от входа.
    * Босс (``count - 2``) висит на одном обычном узле в глубине - не на входе.
    * Выход (``count - 1``) привязан к обычному узлу, а не к боссу, - значит, к
      выходу можно пройти, не трогая хозяина логова (ADR 0035).
    """
    links: list[set[int]] = [set() for _ in range(count)]

    def join(left: int, right: int) -> None:
        links[left].add(right)
        links[right].add(left)

    for index in range(1, count - 2):
        join(index, era.randrange(index))

    join(count - 2, era.randrange(1, count - 2))  # босс - в глубине, не на входе
    join(count - 1, era.randrange(count - 2))  # выход - в обход логова
    return links


def _lay_paths(era: random.Random, count: int, tree: list[set[int]]) -> list[set[int]]:
    """Дерево плюс несколько коротких троп этого поколения.

    Ненаправленные тропы, добавленные поверх остовного дерева, связность сломать
    не могут: выход достижим от входа по самому дереву. Босса они не касаются -
    иначе короткая тропа подшила бы логово ко входу, и хозяин логова перестал бы
    держать конец локации (ADR 0035).
    """
    boss = count - 2
    links: list[set[int]] = [set(node) for node in tree]
    for _ in range(int(count * EXTRA_LINK_RATIO)):
        left = era.randrange(count)
        right = era.randrange(count)
        if left == right or boss in (left, right):
            continue
        links[left].add(right)
        links[right].add(left)
    return links


def _pick_name(era: random.Random, category: _Category) -> str:
    options = _CATEGORY_NAMES[category]
    return options[era.randrange(len(options))]


def _level_for(index: int, count: int, level_min: int, level_max: int) -> int:
    """Чем глубже узел, тем он тяжелее; выход стоит на верху полосы."""
    if count <= 1 or level_max <= level_min:
        return level_min
    step = (level_max - level_min) * index / (count - 1)
    return level_min + int(step)


def guaranteed_find_kinds(world_seed: str, city_id: str, slot: int) -> tuple[NodeKind, ...]:
    """Виды узлов-находок, которые это место держит в любом своём поколении.

    Сколько среди узлов находок и какого они вида — функция места, не поколения
    (ADR 0035): на этом стоят ``search``-задания и дело ``SEARCH`` сводки
    (ADR 0054). Поколение только тасует, в каком узле что. Здесь — тот же расчёт
    ``base``, что в :func:`generate_location`, оборванный до состава находок:
    ``era`` влияет лишь на порядок, поэтому его значение неважно.
    """
    seed = location_seed(world_seed, city_id, slot)
    base = rng(seed)
    era = rng(seed)
    count = base.randint(MIN_NODES, MAX_NODES)
    categories = _interior_categories(base, era, count)
    composition = _kind_composition(base, categories)
    return tuple(composition.get(_Category.FINDING, ()))


def node_level_span(location: GeneratedLocation) -> tuple[int, int]:
    levels = [node.level for node in location.nodes]
    return min(levels), max(levels)


def combat_nodes(location: GeneratedLocation) -> tuple[LocationNode, ...]:
    return tuple(node for node in location.nodes if node.kind.is_combat)
