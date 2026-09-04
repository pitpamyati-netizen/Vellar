"""Сборка локации: слои в глубину, тупики под находки, логово в самом низу.

Локация - это от 16 до 28 узлов, разложенных **по слоям**: вход стоит наверху,
за ним слой ближних мест, за ним следующий, и так до последнего, где ждут логово
и выход. Узел цепляется к узлу слоя над собой, поэтому граф - это дерево плюс
несколько коротких троп, связность и достижимость выхода обеспечены самой
постройкой, а **глубина узла - это то, сколько шагов до него от входа**, а не
его номер в списке.

Раскладка слоями - то, к чему пришли все, кто раскладывает уровни сам: сперва
ставят вход и выход, потом решают, где будут награды, и только потом заполняют
остальное (Dead Cells); тупик держит награду, а самый дальний тупик держит
хозяина (The Binding of Isaac). Здесь ровно это:

* **Уровень узла считается от слоя, а не от номера.** Раньше «чем глубже, тем
  тяжелее» было обещанием: номер узла в списке ничего не говорил о том, сколько
  до него идти, и двадцатый узел мог висеть в одном шаге от входа.
* **Награда лежит в тупике.** Узел, из которого дальше хода нет, получает
  находку или святилище раньше, чем стычку: за то, что игрок свернул с дороги,
  он получает дело, а не ещё один бой.
* **Короткие тропы тупиков не касаются.** Тропа, подшитая к тупику, перестаёт
  быть тупиком, и награда за поворот превращается в проходной узел.

**Расположение узлов перекладывается поколениями.** Округа стоит, пока в ней
есть что брать, и заселяется заново, когда её выработали: другое дерево, места
встают в другом порядке, короткие тропы ложатся иначе, у мест другие имена.
Номер поколения считает выработка, а не время
(``domain/rules/nodes.location_epoch``, ADR 0035).

**Постоянного в локации - только то, чего игрок не слышит как карту.** От
``location_seed`` зависят число узлов, **форма слоёв** (сколько их и сколько
узлов в каждом), набор категорий среди узлов и кривая уровней по глубине. Набор
видов находок («сбор, сбор, тайник») тоже постоянен - на нём держатся
``search``-задания (ADR 0035). Всё это - функция места, не поколения.

**Босс держит конец локации.** Хозяин логова висит на узле последнего слоя
(``count - 2``), и у каждой локации он ровно один. По дороге к выходу он не
стоит: выход привязан к обычному узлу в обход логова, поэтому драться с боссом -
решение, а не пошлина.

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

#: Сколько слоёв бывает между входом и последним. Меньше пяти - и локация звучит
#: плоской: всё стоит в двух шагах от входа. Больше восьми - и она вытягивается в
#: кишку, где сворачивать некуда.
MIN_DEPTH = 5
MAX_DEPTH = 8

#: Сколько коротких троп кладётся поверх дерева, долей от числа узлов. Меньше,
#: чем было: тропа, кладущаяся куда попало, съедала тупики, а вместе с ними и
#: причину сворачивать с дороги.
EXTRA_LINK_RATIO = 0.25


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

#: Категории, которые кладутся в тупик первыми: за поворот платят находкой, а не
#: стычкой.
_QUIET_CATEGORIES: frozenset[_Category] = frozenset({_Category.FINDING, _Category.SHRINE})

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

    ``epoch`` решает всю раскладку: кто чей сосед, где какая категория и вид узла
    стоит, боевой состав, короткие тропы и имена узлов. От места (не от поколения)
    зависят число узлов, форма слоёв, набор категорий и видов находок среди них и
    кривая уровней по глубине. При ``epoch == 0`` и без выработки округа стоит в
    исходном виде.
    """
    seed = location_seed(world_seed, city_id, slot)
    base = rng(seed)
    era = rng(epoch_seed(seed, epoch))

    # Порядок обращений к сиду места менять нельзя: на составе находок стоят
    # ``search``-задания, и лишний бросок между ними пересобрал бы каждую локацию
    # мира. Форма слоёв бросается последней - её здесь раньше не было.
    count = base.randint(MIN_NODES, MAX_NODES)
    pool = _interior_pool(base, count)
    composition = _kind_composition(base, (*pool, _Category.BOSS))
    sizes = _layers(base, count - 3)

    tree, depths, dead_ends = _grow(era, count, sizes)
    categories = _place_categories(era, pool, dead_ends, count - 3)
    composition = _relay_combat(era, categories, composition)
    kinds = _lay_kinds(era, categories, composition)
    names = tuple(_pick_name(era, category) for category in categories)
    links = _lay_paths(era, count, tree, depths, dead_ends)

    ordered_kinds = (NodeKind.ENTRANCE, *kinds, NodeKind.EXIT)
    ordered_names = (_DOOR_NAMES[NodeKind.ENTRANCE], *names, _DOOR_NAMES[NodeKind.EXIT])
    deepest = max(depths)

    nodes = tuple(
        LocationNode(
            index=index,
            kind=ordered_kinds[index],
            name=ordered_names[index],
            level=_level_for(depths[index], deepest, level_min, level_max),
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


def _layers(base: random.Random, interior: int) -> tuple[int, ...]:
    """Сколько у локации слоёв и сколько узлов в каждом - функция места.

    Форма постоянна нарочно: на ней стоит кривая уровней, а её поколение не
    трогает (ADR 0035). Слои неровные - широкий слой звучит как развилка, узкий
    как горловина, - и это то, чем одна округа отличается от другой.
    """
    depth = max(1, min(base.randint(MIN_DEPTH, MAX_DEPTH), interior))
    sizes = [1] * depth
    for _ in range(interior - depth):
        sizes[base.randrange(depth)] += 1
    return tuple(sizes)


def _interior_pool(base: random.Random, count: int) -> tuple[_Category, ...]:
    """Из чего состоит середина локации: сколько в ней боёв, находок и святилищ.

    Это мультимножество, а не раскладка: где именно что встанет, решает поколение
    (``_place_categories``). Набор не должен вырождаться от поколения к поколению,
    иначе ``search``-задание на вид узла встало бы намертво. Хотя бы один бой в
    середине есть всегда.
    """
    population = [category for category, _ in _INTERIOR_CATEGORIES]
    weights = [weight for _, weight in _INTERIOR_CATEGORIES]
    interior = base.choices(population, weights=weights, k=count - 3)
    if _Category.COMBAT not in interior:
        interior[base.randrange(len(interior))] = _Category.COMBAT
    return tuple(interior)


def _grow(
    era: random.Random, count: int, sizes: tuple[int, ...]
) -> tuple[list[set[int]], list[int], tuple[int, ...]]:
    """Дерево по слоям: кто чей сосед - решает поколение.

    Узел слоя цепляется к узлу слоя над собой, поэтому от входа к нему ровно
    столько шагов, каков его слой, - на этом и стоит кривая уровней. Логово и
    выход висят на узлах последнего слоя: логово - в стороне, выход - в обход
    логова, чтобы уйти из локации можно было, не трогая хозяина (ADR 0035).

    Возвращает дерево, глубину каждого узла и тупики - узлы, из которых дальше
    хода нет.
    """
    links: list[set[int]] = [set() for _ in range(count)]
    depths = [0] * count

    def join(child: int, parent: int) -> None:
        links[child].add(parent)
        links[parent].add(child)

    parents: set[int] = set()
    above = [0]
    cursor = 1
    for depth, size in enumerate(sizes, start=1):
        layer = list(range(cursor, cursor + size))
        cursor += size
        for index in layer:
            parent = above[era.randrange(len(above))]
            join(index, parent)
            parents.add(parent)
            depths[index] = depth
        above = layer

    for door in (count - 2, count - 1):
        parent = above[era.randrange(len(above))]
        join(door, parent)
        parents.add(parent)
        depths[door] = len(sizes) + 1

    dead_ends = tuple(index for index in range(1, count - 2) if index not in parents)
    return links, depths, dead_ends


def _place_categories(
    era: random.Random,
    pool: tuple[_Category, ...],
    dead_ends: tuple[int, ...],
    interior: int,
) -> tuple[_Category, ...]:
    """Разложить середину по узлам этого поколения, наградой в тупик.

    Тупик получает находку или святилище раньше, чем стычку: свернувший с дороги
    должен получить дело, а не ещё один бой. Тихих категорий может не хватить на
    все тупики - тогда в остальные встанет бой, и это честно: состав локации
    постоянен, а тупиков в разных поколениях разное число.
    """
    quiet = [category for category in pool if category in _QUIET_CATEGORIES]
    rest = [category for category in pool if category not in _QUIET_CATEGORIES]
    era.shuffle(quiet)
    era.shuffle(rest)

    ends = list(dead_ends)
    era.shuffle(ends)
    placed: dict[int, _Category] = {}
    for index in ends:
        if not quiet:
            break
        placed[index] = quiet.pop()

    left = [*quiet, *rest]
    era.shuffle(left)
    for index in range(1, interior + 1):
        if index not in placed:
            placed[index] = left.pop()
    return (*(placed[index] for index in range(1, interior + 1)), _Category.BOSS)


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


def _lay_paths(
    era: random.Random,
    count: int,
    tree: list[set[int]],
    depths: list[int],
    dead_ends: tuple[int, ...],
) -> list[set[int]]:
    """Дерево плюс несколько коротких троп этого поколения.

    Тропа ложится только между соседними слоями и только между проходными узлами:
    тупик, к которому подшили тропу, перестаёт быть тупиком, а вместе с ним
    пропадает и причина туда сворачивать. Логова и выхода тропы не касаются -
    иначе короткая тропа подшила бы логово ко входу, и хозяин логова перестал бы
    держать конец локации (ADR 0035).
    """
    links: list[set[int]] = [set(node) for node in tree]
    closed = {count - 2, count - 1, *dead_ends}
    open_nodes = [index for index in range(count) if index not in closed]
    if len(open_nodes) < 2:
        return links
    for _ in range(int(count * EXTRA_LINK_RATIO)):
        left = open_nodes[era.randrange(len(open_nodes))]
        right = open_nodes[era.randrange(len(open_nodes))]
        if left == right or abs(depths[left] - depths[right]) > 1:
            continue
        links[left].add(right)
        links[right].add(left)
    return links


def _pick_name(era: random.Random, category: _Category) -> str:
    options = _CATEGORY_NAMES[category]
    return options[era.randrange(len(options))]


def _level_for(depth: int, deepest: int, level_min: int, level_max: int) -> int:
    """Чем дальше от входа узел, тем он тяжелее. Считается по слою, не по номеру."""
    if deepest <= 0 or level_max <= level_min:
        return level_min
    return level_min + int((level_max - level_min) * depth / deepest)


def guaranteed_find_kinds(world_seed: str, city_id: str, slot: int) -> tuple[NodeKind, ...]:
    """Виды узлов-находок, которые это место держит в любом своём поколении.

    Сколько среди узлов находок и какого они вида — функция места, не поколения
    (ADR 0035): на этом стоят ``search``-задания и дело ``SEARCH`` сводки
    (ADR 0054). Поколение только решает, в каком узле что встанет. Здесь — тот же
    расчёт ``base``, что в :func:`generate_location`, оборванный до состава
    находок.
    """
    base = rng(location_seed(world_seed, city_id, slot))
    count = base.randint(MIN_NODES, MAX_NODES)
    pool = _interior_pool(base, count)
    composition = _kind_composition(base, (*pool, _Category.BOSS))
    return tuple(composition.get(_Category.FINDING, ()))


def node_level_span(location: GeneratedLocation) -> tuple[int, int]:
    levels = [node.level for node in location.nodes]
    return min(levels), max(levels)


def combat_nodes(location: GeneratedLocation) -> tuple[LocationNode, ...]:
    return tuple(node for node in location.nodes if node.kind.is_combat)
