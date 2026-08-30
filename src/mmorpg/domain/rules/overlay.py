"""Правки смотрителя: какие бывают поля, что в них можно, и как это ложится на мир.

Модуль отвечает на три вопроса и ни на один больше.

*Что у сущности за поля.* :data:`FIELDS` — единственное описание: по нему рисуется
карточка, по нему же разбирается набранное значение. Экран не знает, что у задания
есть плата: он спрашивает здесь.

*Годится ли запись.* :func:`problems` возвращает отказы словами. Записи с отказами
не применяются — но и не пропадают: смотритель видит и правку, и причину, по
которой она пока не работает.

*Как выглядит мир с правками.* :func:`apply` собирает новое содержимое: TOML плюс
записи. Само содержимое неизменяемо, поэтому это именно новая сборка, а не запись
поверх старой — из-за чего правку и видно сразу всем, кто откроет экран после неё.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType

from mmorpg.domain.entities.content import (
    City,
    GameContent,
    Location,
    Npc,
    ProgressionRules,
    Trait,
)
from mmorpg.domain.entities.craft import Craft, CraftKind, Recipe, RecipeInput
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.location import EnemyArchetype, EnemyKind
from mmorpg.domain.entities.overlay import KEEPER_PREFIX, OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import ObjectiveKind, Quest
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS

#: Сколько мест под локации в городе. Пять кладёт содержимое, остальное — запас
#: смотрителя: город, в котором некуда добавить, — город, который нельзя править.
MAX_LOCATION_SLOT = 12

#: Потолок длины набранного значения. Экран читают вслух, поэтому потолок есть;
#: но условие задания — это несколько фраз, а не подпись, и на 240 знаках
#: смотритель упирался в отказ посреди обычного текста (``docs/accessibility.md``).
MAX_TEXT = 600

#: Потолок для того, что попадёт на кнопку: имя жителя, название задания,
#: локации, противника. Кнопка — это одна строка, и она должна оставаться одной.
NAME_LIMIT = 48

#: Узлы, которые задание может считать без боя. Те же слова, что в ``quests.toml``.
SEARCHABLE_NODES: tuple[str, ...] = ("gather", "cache", "shrine", "event")

#: Ключ единственной сущности разновидности ``META``. Опорные числа в игре одни,
#: и правит их одна карточка.
META_ID = "rules"

#: Потолок для скалярного опорного числа. Тюнинг — это сдвиг на проценты, а не
#: замена правил: очко характеристик за уровень больше сотни — это уже не тюнинг.
META_CEILING = 100

#: Сколько чисел влезает в поле-список опорных чисел (цена рангов, ступени ветви).
#: Двенадцать — с большим запасом от пяти рангов и четырёх ступеней.
META_LIST_LIMIT = 12


class FieldKind(StrEnum):
    """Как поле набирают.

    ``CHOICE`` и ``FLAG`` набирать не надо вовсе — их нажимают, и это не мелочь:
    выбор из кнопок нельзя опечатать.
    """

    TEXT = "text"
    NUMBER = "number"
    RATE = "rate"
    FLAG = "flag"
    CHOICE = "choice"
    LIST = "list"
    #: Поле «список чисел»: целые через запятую, «1, 2, 2, 3, 4». Цена рангов и
    #: ступени ветви — обе такие.
    NUMBERS = "numbers"
    #: Поле «ключ=число»: набор пар, где ключ выбирается из известного списка, а
    #: значение набирают. Прибавки черты и состав рецепта — обе такие.
    PAIRS = "pairs"


class Source(StrEnum):
    """Откуда берётся список вариантов, если он не записан в самом поле."""

    NONE = "none"
    CITY = "city"
    NPC = "npc"
    BIOME = "biome"
    ITEM = "item"
    QUEST = "quest"
    LOCATION = "location"
    #: Ключи прибавок, которые движок и правда считает (``modifiers.EFFECTIVE_KEYS``),
    #: а не широкий словарь ``traits.toml`` (``Claude.md``, правило 7).
    MODIFIER = "modifier"
    #: Ремёсла вида «работа»: рецепт вешают только на них.
    CRAFT = "craft"
    #: Разделы черт (``content.trait_categories``).
    TRAIT_CATEGORY = "trait_category"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Одно поле сущности: как называется, чем заполняется, обязательно ли."""

    key: str
    name: str
    kind: FieldKind = FieldKind.TEXT
    choices: tuple[str, ...] = ()
    source: Source = Source.NONE
    required: bool = False
    hint: str = ""
    #: Сколько знаков сюда влезает. Меньше общего потолка там, где значение
    #: окажется на кнопке, а кнопка — это одна строка.
    limit: int = MAX_TEXT
    #: Чем считать значение пары у поля ``PAIRS``: ``NUMBER`` — счёт в рецепте
    #: (целое, не меньше единицы), ``RATE`` — прибавка черты (доля, знак допустим).
    pair_value: FieldKind = FieldKind.NUMBER


#: Как разновидность называется в единственном и множественном числе. Первое —
#: заголовок карточки, второе — кнопка и заголовок списка.
TITLES: Mapping[OverlayKind, tuple[str, str]] = {
    OverlayKind.NPC: ("Житель", "Жители"),
    OverlayKind.QUEST: ("Задание", "Задания"),
    OverlayKind.LOCATION: ("Локация", "Локации"),
    OverlayKind.ENEMY: ("Противник", "Противники"),
    OverlayKind.CITY: ("Город", "Города"),
    OverlayKind.TRAIT: ("Черта", "Черты"),
    OverlayKind.CRAFT: ("Ремесло", "Ремёсла"),
    OverlayKind.RECIPE: ("Рецепт", "Рецепты"),
    OverlayKind.META: ("Опорные числа", "Опорные числа"),
}

#: Разновидности, которые нельзя убрать из игры: без них игра не собирается.
#: Опорные числа есть всегда — «убрать» их значения не имеет.
NON_REMOVABLE: frozenset[OverlayKind] = frozenset({OverlayKind.META})

#: Разновидности, которые смотритель заводит с нуля. Города и локации приходят из
#: ``world.toml`` вместе с проверкой уровней, и заводить город кнопкой — значит
#: обойти эту проверку; локацию в существующем городе добавить можно.
CREATABLE: frozenset[OverlayKind] = frozenset(
    {
        OverlayKind.NPC,
        OverlayKind.QUEST,
        OverlayKind.LOCATION,
        OverlayKind.ENEMY,
        OverlayKind.TRAIT,
        OverlayKind.CRAFT,
        OverlayKind.RECIPE,
    }
)

FIELDS: Mapping[OverlayKind, tuple[FieldSpec, ...]] = {
    OverlayKind.NPC: (
        FieldSpec("name", "Имя", required=True, limit=NAME_LIMIT),
        FieldSpec("city", "Город", FieldKind.CHOICE, source=Source.CITY, required=True),
        FieldSpec("role", "Занятие", hint="писарь заставы, гуртовщица", limit=NAME_LIMIT),
        FieldSpec("text", "Что говорит", hint="одна-две фразы при встрече"),
    ),
    OverlayKind.QUEST: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec("city", "Город", FieldKind.CHOICE, source=Source.CITY, required=True),
        FieldSpec("npc", "Кто даёт", FieldKind.CHOICE, source=Source.NPC),
        # Спрашивается, только пока житель не выбран (``fields_for``). Раньше поле
        # стояло на карточке всегда, сразу под «Кто даёт», и панель выглядела так,
        # будто спрашивает нанимателя дважды; имя всё равно берётся у жителя, если
        # он назван (``_quest_from``).
        FieldSpec("giver", "Имя нанимателя", hint="когда задание не от жителя"),
        FieldSpec("intro", "Как встречает"),
        FieldSpec("terms", "Условие словами", required=True),
        FieldSpec(
            "objective",
            "Что считать",
            FieldKind.CHOICE,
            choices=tuple(kind.value for kind in ObjectiveKind),
            required=True,
        ),
        FieldSpec("target_kind", "Кого именно", FieldKind.CHOICE, choices=()),
        FieldSpec("target_count", "Сколько по счёту", FieldKind.NUMBER, required=True),
        FieldSpec(
            "location_slot",
            "Где делают",
            FieldKind.CHOICE,
            source=Source.LOCATION,
            hint="локация того же города; без неё задание не говорит, куда идти",
        ),
        FieldSpec("level", "С какого уровня", FieldKind.NUMBER),
        FieldSpec("reward_gold", "Плата золотом", FieldKind.NUMBER),
        FieldSpec("reward_experience", "Плата опытом", FieldKind.NUMBER),
        FieldSpec("reward_item", "Что дают сверху", FieldKind.CHOICE, source=Source.ITEM),
        FieldSpec("follows", "После какого задания", FieldKind.CHOICE, source=Source.QUEST),
    ),
    OverlayKind.LOCATION: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec("city", "Город", FieldKind.CHOICE, source=Source.CITY, required=True),
        FieldSpec("slot", "Место в списке", FieldKind.NUMBER, required=True),
        FieldSpec("biome", "Местность", FieldKind.CHOICE, source=Source.BIOME, required=True),
        FieldSpec("level_min", "Уровень от", FieldKind.NUMBER, required=True),
        FieldSpec("level_max", "Уровень до", FieldKind.NUMBER, required=True),
        FieldSpec("pvp", "Вольная земля", FieldKind.FLAG),
    ),
    OverlayKind.ENEMY: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec(
            "kind",
            "Порода",
            FieldKind.CHOICE,
            choices=tuple(kind.value for kind in EnemyKind),
            required=True,
        ),
        FieldSpec("biomes", "Где водится", FieldKind.LIST, source=Source.BIOME, required=True),
        FieldSpec("health", "Здоровье, доля", FieldKind.RATE, hint="1 — обычное, 1,5 — крепкий"),
        FieldSpec("damage", "Урон, доля", FieldKind.RATE),
        FieldSpec("armor", "Броня, доля", FieldKind.RATE),
        FieldSpec("initiative", "Инициатива, доля", FieldKind.RATE),
        # Чем бьёт. По роду урона считается сопротивление цели; не выбрано —
        # решает порода (``location.DEFAULT_DAMAGE_TYPES``).
        FieldSpec(
            "element",
            "Чем бьёт",
            FieldKind.CHOICE,
            choices=tuple(one.value for one in DamageType),
            hint="не выбрано — по породе",
        ),
        FieldSpec("loot", "Что падает", FieldKind.LIST, source=Source.ITEM),
        # Только под землёй: порода уходит в dungeon-пул захода и пропадает из
        # встреч на дороге (ADR 0042).
        FieldSpec("dungeon", "Только подземелья", FieldKind.FLAG),
    ),
    OverlayKind.CITY: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec("description", "Описание"),
    ),
    OverlayKind.TRAIT: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec(
            "category", "Раздел", FieldKind.CHOICE, source=Source.TRAIT_CATEGORY, required=True
        ),
        FieldSpec("text", "Что обещает", hint="одна фраза при выборе"),
        FieldSpec(
            "modifiers",
            "Прибавки",
            FieldKind.PAIRS,
            source=Source.MODIFIER,
            pair_value=FieldKind.RATE,
            required=True,
            hint="stat_STR=1, crit_chance_percent=5",
        ),
    ),
    OverlayKind.CRAFT: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec(
            "kind",
            "Что делает",
            FieldKind.CHOICE,
            choices=tuple(one.value for one in CraftKind),
            required=True,
        ),
        FieldSpec(
            "stat",
            "На чём держится",
            FieldKind.CHOICE,
            choices=tuple(code.value for code in StatCode),
            required=True,
        ),
        FieldSpec("description", "Чем занимаются"),
    ),
    OverlayKind.RECIPE: (
        FieldSpec("craft", "Ремесло", FieldKind.CHOICE, source=Source.CRAFT, required=True),
        FieldSpec("rank", "С какого ранга", FieldKind.NUMBER, required=True),
        FieldSpec(
            "inputs",
            "Из чего",
            FieldKind.PAIRS,
            source=Source.ITEM,
            pair_value=FieldKind.NUMBER,
            required=True,
            hint="iron_scrap=2, oak_plank=1",
        ),
        FieldSpec("output", "Что выходит", FieldKind.CHOICE, source=Source.ITEM, required=True),
        FieldSpec("output_count", "Сколько за раз", FieldKind.NUMBER, required=True),
        FieldSpec("experience", "Опыт за работу", FieldKind.NUMBER),
    ),
    # Опорные числа: белый список ``ProgressionRules``. Только то, что двигает
    # баланс числом, — не то, что держит дорогу (число уровней, счёт слотов,
    # уровни развилок): такое остаётся за файлами (``Claude.md``, правило 7).
    OverlayKind.META: (
        FieldSpec(
            "base_stat_value",
            "Базовая характеристика",
            FieldKind.NUMBER,
            hint="с чего начинается каждая характеристика",
        ),
        FieldSpec(
            "free_points_at_creation",
            "Свободные очки при создании",
            FieldKind.NUMBER,
        ),
        FieldSpec(
            "stat_points_per_level",
            "Очков характеристик за уровень",
            FieldKind.NUMBER,
        ),
        FieldSpec(
            "skill_point_per_level",
            "Очков умений за уровень",
            FieldKind.NUMBER,
        ),
        FieldSpec(
            "rank_costs",
            "Цена рангов умений",
            FieldKind.NUMBERS,
            hint="1, 2, 2, 3, 4 — по одному числу на ранг",
        ),
        FieldSpec(
            "branch_gates",
            "Очки на ступени ветви",
            FieldKind.NUMBERS,
            hint="0, 6, 14, 24 — первая ступень открыта сразу",
        ),
    ),
}

#: Порода противника и узлы поиска — разные списки, и какой из них нужен, зависит
#: от того, что задание считает. Единственное поле, чьи варианты зависят от соседа.
#:
#: Для боевых заданий список не кончается породами. «Убей пятерых кабанов» — это
#: не «убей пятерых зверей», и пока в выборе стояли одни породы, смотритель писал
#: кабанов в условие словами, а счёт шёл по любому зверью. Поэтому следом за
#: породами идут поимённо все противники мира, включая заведённых этой же панелью
#: (``options``), а счёт умеет и то и другое (``domain/rules/quests``).
_TARGETS: Mapping[ObjectiveKind, tuple[str, ...]] = {
    ObjectiveKind.KILL: tuple(kind.value for kind in EnemyKind),
    ObjectiveKind.ELITE: tuple(kind.value for kind in EnemyKind),
    ObjectiveKind.SEARCH: SEARCHABLE_NODES,
}


def spec_of(kind: OverlayKind, key: str) -> FieldSpec | None:
    return next((spec for spec in FIELDS[kind] if spec.key == key), None)


def fields_for(record: OverlayRecord) -> tuple[FieldSpec, ...]:
    """Поля этой карточки — без тех, которые сейчас ничего не значат.

    Список полей у разновидности один, но вопрос, у которого уже есть ответ,
    задавать не надо: у задания, выданного жителю, имя нанимателя берётся у
    жителя, и отдельная строка «Имя нанимателя» под строкой «Кто даёт» читается
    как второй такой же вопрос.
    """
    fields = FIELDS[record.kind]
    if record.kind is not OverlayKind.QUEST:
        return fields
    hidden: set[str] = set()
    if record.value("npc"):
        hidden.add("giver")
    # Сравнивается строка, а не разобранное значение: в поле лежит то, что набрал
    # смотритель, и оно вполне может не быть ни одним из известных слов
    # (``Claude.md``, правило 8 - сохранённому не верят).
    if record.value("objective") == ObjectiveKind.CRAFT:
        # Изготовление делают руками, где угодно; локация тут ничего не назначает.
        hidden.add("location_slot")
    return tuple(spec for spec in fields if spec.key not in hidden)


def biomes(content: GameContent) -> tuple[str, ...]:
    """Все местности мира. Противника селят в ту, что уже где-то есть."""
    found = {location.biome for city in content.cities for location in city.locations}
    return tuple(sorted(found))


def options(content: GameContent, spec: FieldSpec, record: OverlayRecord) -> tuple[str, ...]:
    """Что можно выбрать в этом поле, значениями.

    Пустая строка среди вариантов не нужна: «ничего» — это отдельная кнопка на
    экране поля, а не вариант в списке.
    """
    if spec.key == "target_kind":
        return _target_options(content, record)
    match spec.source:
        case Source.CITY:
            return tuple(city.id for city in content.cities)
        case Source.NPC:
            city = record.value("city")
            listed = content.npcs_in(city) if city else content.npcs
            return tuple(npc.id for npc in listed)
        case Source.BIOME:
            return biomes(content)
        case Source.ITEM:
            return tuple(item.id for item in content.items)
        case Source.QUEST:
            city = record.value("city")
            return tuple(quest.id for quest in content.quests if not city or quest.city_id == city)
        case Source.LOCATION:
            city = record.value("city")
            if not content.has_city(city):
                return ()
            return tuple(str(location.slot) for location in content.city(city).locations)
        case Source.MODIFIER:
            return tuple(sorted(EFFECTIVE_KEYS))
        case Source.CRAFT:
            return tuple(craft.id for craft in content.crafts_of_kind(CraftKind.MAKING))
        case Source.TRAIT_CATEGORY:
            return tuple(content.trait_categories)
        case _:
            return spec.choices


def _target_options(content: GameContent, record: OverlayRecord) -> tuple[str, ...]:
    """Что задание может считать поимённо: породы, потом сами противники.

    Породы идут первыми, потому что «любое зверьё» — это по-прежнему нормальное
    условие; за ними именами идут все противники, чтобы можно было заказать
    именно кабанов, в том числе тех, которых смотритель завёл сам.
    """
    objective = record.value("objective")
    if objective not in _TARGETS:
        # Изготовление считает вещи, а не противников; всё прочее - ничего.
        if objective == ObjectiveKind.CRAFT:
            return tuple(item.id for item in content.items)
        return ()
    kinds = _TARGETS[ObjectiveKind(objective)]
    if objective == ObjectiveKind.SEARCH:
        return kinds
    return (*kinds, *(enemy.id for enemy in content.enemy_archetypes))


def option_name(
    content: GameContent, spec: FieldSpec, value: str, record: OverlayRecord | None = None
) -> str:
    """Как вариант зовут по-русски. Идентификатор без имени никому ничего не говорит.

    Запись нужна там, где одно и то же значение в разных карточках значит разное:
    место 3 — это разная локация в разных городах.
    """
    if not value:
        return "не выбрано"
    if spec.key == "target_kind":
        return _target_name(content, value)
    match spec.source:
        case Source.CITY:
            return content.city(value).name if content.has_city(value) else value
        case Source.NPC:
            return content.npc(value).title if content.has_npc(value) else value
        case Source.ITEM:
            return content.item(value).name if content.has_item(value) else value
        case Source.QUEST:
            return content.quest(value).name if content.has_quest(value) else value
        case Source.LOCATION:
            return _location_name(content, value, record)
        case Source.CRAFT:
            return content.craft(value).name if content.has_craft(value) else value
        case Source.TRAIT_CATEGORY:
            return content.trait_categories.get(value, value)
        case Source.MODIFIER:
            return value
        case _:
            return _WORDS.get(value, value)


def _target_name(content: GameContent, value: str) -> str:
    """Порода, названный противник или вещь — одним словом, каким его слышат."""
    if value in _WORDS:
        return _WORDS[value]
    named = next((enemy for enemy in content.enemy_archetypes if enemy.id == value), None)
    if named is not None:
        return named.name
    return content.item(value).name if content.has_item(value) else value


def _location_name(content: GameContent, value: str, record: OverlayRecord | None) -> str:
    """Номер места плюс название: в списке города локация стоит под этим номером."""
    city_id = record.value("city") if record is not None else ""
    if not content.has_city(city_id):
        return value
    for location in content.city(city_id).locations:
        if str(location.slot) == value:
            return f"{location.slot}. {location.name}"
    return value


#: Служебные слова содержимого по-русски. Смотритель читает экран, а не схему.
_WORDS: Mapping[str, str] = {
    "kill": "победить противников",
    "elite": "победить сильных",
    "search": "разобраться без боя",
    "beast": "зверьё",
    "humanoid": "люди",
    "undead": "мертвяки",
    "elemental": "стихийные",
    "aberration": "твари",
    "gather": "сборы",
    "cache": "тайники",
    "shrine": "святилища",
    "event": "события",
}


def shown(content: GameContent, spec: FieldSpec, record: OverlayRecord) -> str:
    """Значение поля так, как его слышат: не ключ, а слово."""
    value = record.value(spec.key)
    match spec.kind:
        case FieldKind.FLAG:
            return "да" if record.flag(spec.key) else "нет"
        case FieldKind.CHOICE:
            return option_name(content, spec, value, record)
        case FieldKind.LIST:
            named = [option_name(content, spec, part, record) for part in record.listed(spec.key)]
            return ", ".join(named) if named else "не выбрано"
        case FieldKind.PAIRS:
            shown_pairs = [
                f"{option_name(content, spec, key, record)} {val}"
                for key, val in record.pairs(spec.key)
            ]
            return ", ".join(shown_pairs) if shown_pairs else "не заполнено"
        case FieldKind.NUMBERS:
            listed = record.listed(spec.key)
            return ", ".join(listed) if listed else "не заполнено"
        case _:
            return value or "не заполнено"


# --- что уже есть ------------------------------------------------------


def listing(content: GameContent, kind: OverlayKind) -> tuple[tuple[str, str], ...]:
    """Сущности этой разновидности: идентификатор и как её назвать в списке."""
    match kind:
        case OverlayKind.NPC:
            return tuple(
                (npc.id, f"{npc.title} — {_city_name(content, npc.city_id)}")
                for npc in content.npcs
            )
        case OverlayKind.QUEST:
            return tuple(
                (quest.id, f"{quest.name} — {_city_name(content, quest.city_id)}")
                for quest in content.quests
            )
        case OverlayKind.LOCATION:
            return tuple(
                (location.id, f"{location.name} — {city.name}, место {location.slot}")
                for city in content.cities
                for location in city.locations
            )
        case OverlayKind.ENEMY:
            return tuple(
                (enemy.id, f"{enemy.name} — {_WORDS.get(enemy.kind.value, enemy.kind.value)}")
                for enemy in content.enemy_archetypes
            )
        case OverlayKind.TRAIT:
            return tuple(
                (trait.id, f"{trait.name} — {content.trait_categories.get(trait.category, '—')}")
                for trait in content.traits
            )
        case OverlayKind.CRAFT:
            return tuple(
                (craft.id, f"{craft.name} — {'сбор' if craft.gathers else 'работа'}")
                for craft in content.crafts
            )
        case OverlayKind.RECIPE:
            return tuple((recipe.id, _recipe_title(content, recipe)) for recipe in content.recipes)
        case OverlayKind.META:
            return ((META_ID, "Опорные числа игры"),)
        case _:
            return tuple(
                (city.id, f"{city.name} — уровни с {city.level_min} по {city.level_max}")
                for city in content.cities
            )


def _recipe_title(content: GameContent, recipe: Recipe) -> str:
    out = recipe.output_id
    if content.has_item(recipe.output_id):
        out = content.item(recipe.output_id).name
    craft = recipe.craft_id
    if content.has_craft(recipe.craft_id):
        craft = content.craft(recipe.craft_id).name
    tail = f" ×{recipe.output_count}" if recipe.output_count != 1 else ""
    return f"{out}{tail} — {craft}"


def _city_name(content: GameContent, city_id: str) -> str:
    return content.city(city_id).name if content.has_city(city_id) else city_id


def snapshot(content: GameContent, kind: OverlayKind, entity_id: str) -> dict[str, str]:
    """Поля сущности такими, какие они сейчас.

    Правка начинается не с чистого листа: смотритель открывает карточку и видит,
    что там стоит, — иначе исправить одно число значило бы набрать все.
    """
    match kind:
        case OverlayKind.NPC if content.has_npc(entity_id):
            return _npc_fields(content.npc(entity_id))
        case OverlayKind.QUEST if content.has_quest(entity_id):
            return _quest_fields(content.quest(entity_id))
        case OverlayKind.LOCATION:
            return _location_fields(content, entity_id)
        case OverlayKind.ENEMY:
            found = next((e for e in content.enemy_archetypes if e.id == entity_id), None)
            return _enemy_fields(found) if found is not None else {}
        case OverlayKind.CITY if content.has_city(entity_id):
            city = content.city(entity_id)
            return {"name": city.name, "description": city.description}
        case OverlayKind.TRAIT if content.has_trait(entity_id):
            return _trait_fields(content.trait(entity_id))
        case OverlayKind.CRAFT if content.has_craft(entity_id):
            return _craft_fields(content.craft(entity_id))
        case OverlayKind.RECIPE:
            recipe = next((r for r in content.recipes if r.id == entity_id), None)
            return _recipe_fields(recipe) if recipe is not None else {}
        case OverlayKind.META:
            return _meta_fields(content.rules)
        case _:
            return {}


def _npc_fields(npc: Npc) -> dict[str, str]:
    return {"name": npc.name, "city": npc.city_id, "role": npc.role, "text": npc.text}


def _quest_fields(quest: Quest) -> dict[str, str]:
    return {
        "name": quest.name,
        "city": quest.city_id,
        "npc": quest.giver_id,
        "giver": quest.giver,
        "intro": quest.intro,
        "terms": quest.terms,
        "objective": quest.objective.value,
        "target_kind": quest.target_kind,
        "target_count": str(quest.target_count),
        "level": str(quest.level),
        "reward_gold": str(quest.reward_gold),
        "reward_experience": str(quest.reward_experience),
        "reward_item": quest.reward_item,
        "follows": quest.follows,
    }


def _location_fields(content: GameContent, location_id: str) -> dict[str, str]:
    for city in content.cities:
        for location in city.locations:
            if location.id != location_id:
                continue
            return {
                "name": location.name,
                "city": city.id,
                "slot": str(location.slot),
                "biome": location.biome,
                "level_min": str(location.level_min),
                "level_max": str(location.level_max),
                "pvp": "да" if location.pvp else "нет",
            }
    return {}


def _enemy_fields(enemy: EnemyArchetype) -> dict[str, str]:
    return {
        "name": enemy.name,
        "kind": enemy.kind.value,
        "biomes": ", ".join(enemy.biomes),
        "health": _rate(enemy.health),
        "damage": _rate(enemy.damage),
        "armor": _rate(enemy.armor),
        "initiative": _rate(enemy.initiative),
        "element": enemy.element.value if enemy.element is not None else "",
        "loot": ", ".join(enemy.loot),
        "dungeon": "да" if enemy.dungeon else "нет",
    }


def _rate(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _pairs_str(items: Iterable[tuple[str, str]]) -> str:
    """Пары обратно в строку поля: «ключ=значение, ключ=значение»."""
    return ", ".join(f"{key}={value}" for key, value in items)


def _trait_fields(trait: Trait) -> dict[str, str]:
    return {
        "name": trait.name,
        "category": trait.category,
        "text": trait.text,
        "modifiers": _pairs_str((key, _rate(value)) for key, value in trait.modifiers.items()),
    }


def _craft_fields(craft: Craft) -> dict[str, str]:
    return {
        "name": craft.name,
        "kind": craft.kind.value,
        "stat": craft.stat.value,
        "description": craft.description,
    }


def _recipe_fields(recipe: Recipe) -> dict[str, str]:
    return {
        "craft": recipe.craft_id,
        "rank": str(recipe.rank),
        "inputs": _pairs_str((part.item_id, str(part.count)) for part in recipe.inputs),
        "output": recipe.output_id,
        "output_count": str(recipe.output_count),
        "experience": str(recipe.experience),
    }


def _meta_fields(rules: ProgressionRules) -> dict[str, str]:
    return {
        "base_stat_value": str(rules.base_stat_value),
        "free_points_at_creation": str(rules.free_points_at_creation),
        "stat_points_per_level": str(rules.stat_points_per_level),
        "skill_point_per_level": str(rules.skill_point_per_level),
        "rank_costs": ", ".join(str(cost) for cost in rules.rank_costs),
        "branch_gates": ", ".join(str(gate) for gate in rules.branch_gates),
    }


def held(
    records: Sequence[OverlayRecord], kind: OverlayKind, entity_id: str
) -> OverlayRecord | None:
    """Правка этой сущности, если она есть."""
    return next(
        (record for record in records if record.kind is kind and record.entity_id == entity_id),
        None,
    )


def effective(
    content: GameContent,
    records: Sequence[OverlayRecord],
    kind: OverlayKind,
    entity_id: str,
) -> OverlayRecord:
    """Сущность так, как её сейчас видно, — одной записью.

    Три случая складываются в один ответ: строка из ``content/`` без правок,
    строка с правкой, и сущность, которой в ``content/`` не было вовсе. Карточке
    незачем их различать — ей нужны поля, а откуда они, говорит отдельная строка.

    Убранная сущность из содержимого пропала, поэтому её поля берутся из самой
    записи: иначе «вернуть в игру» возвращало бы пустое место.
    """
    stored = held(records, kind, entity_id)
    fields: dict[str, str] = snapshot(content, kind, entity_id)
    if stored is not None:
        fields.update(stored.fields)
        return replace(stored, fields=MappingProxyType(fields))
    return OverlayRecord(kind=kind, entity_id=entity_id, fields=MappingProxyType(fields))


def next_id(kind: OverlayKind, records: Sequence[OverlayRecord]) -> str:
    """Свободное имя для новой сущности.

    Считается, а не выдумывается: смотрителю не нужно придумывать ключ, а ключ
    смотрителя никогда не спорит с ключом из ``content/``.
    """
    taken = {record.entity_id for record in records if record.kind is kind}
    for number in range(1, len(taken) + 2):
        candidate = f"{KEEPER_PREFIX}{kind.value}_{number}"
        if candidate not in taken:
            return candidate
    raise AssertionError  # pragma: no cover - цикл всегда находит свободное имя


# --- проверка ----------------------------------------------------------


def problems(content: GameContent, record: OverlayRecord) -> tuple[str, ...]:
    """Почему запись пока не работает. Пустой ответ — работает.

    Проверка идёт против *исходного* содержимого: правка, которая ссылается на
    другую правку, — это две записи, и вторая начинает работать после первой.
    """
    if record.removed:
        return _removal_problems(content, record)

    found: list[str] = []
    for spec in FIELDS[record.kind]:
        value = record.value(spec.key).strip()
        if spec.required and not value:
            found.append(f"Не заполнено: {spec.name.lower()}.")
            continue
        if not value:
            continue
        found.extend(_field_problems(content, record, spec, value))
    found.extend(_shape_problems(content, record))
    return tuple(found)


#: Сколько знаков негодного значения попадает в отказ. Отказ должен назвать то,
#: что не годится, а не пересказать его: экран читают вслух.
QUOTED = 40


def clipped(value: str, limit: int = QUOTED) -> str:
    """Значение, укороченное до того, что можно произнести одной фразой."""
    return value if len(value) <= limit else f"{value[:limit].rstrip()}…"


def _field_problems(
    content: GameContent, record: OverlayRecord, spec: FieldSpec, value: str
) -> list[str]:
    if len(value) > spec.limit:
        return [f"{spec.name}: длиннее {spec.limit} знаков, экран это не прочитает."]
    match spec.kind:
        case FieldKind.NUMBER if not _is_number(value):
            return [f"{spec.name}: нужно целое число, а стоит «{clipped(value)}»."]
        case FieldKind.RATE if not _is_rate(value):
            return [f"{spec.name}: нужна доля вроде «1,2», а стоит «{clipped(value)}»."]
        case FieldKind.CHOICE:
            allowed = options(content, spec, record)
            if value not in allowed:
                return [f"{spec.name}: «{clipped(value)}» больше нет в игре."]
        case FieldKind.LIST:
            allowed = options(content, spec, record)
            unknown = [clipped(part) for part in record.listed(spec.key) if part not in allowed]
            if unknown:
                return [f"{spec.name}: неизвестно — {clipped(', '.join(unknown), MAX_TEXT // 2)}."]
        case FieldKind.PAIRS:
            return _pairs_problems(content, record, spec, value)
        case FieldKind.NUMBERS:
            return _numbers_problems(spec, value)
    return []


def _numbers_problems(spec: FieldSpec, value: str) -> list[str]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts or any(not _is_number(part) for part in parts):
        return [f"{spec.name}: нужны целые числа через запятую, а стоит «{clipped(value)}»."]
    if len(parts) > META_LIST_LIMIT:
        return [f"{spec.name}: не больше {META_LIST_LIMIT} чисел."]
    return []


def _pairs_problems(
    content: GameContent, record: OverlayRecord, spec: FieldSpec, value: str
) -> list[str]:
    raw_pairs = record.pairs(spec.key)
    if not raw_pairs:
        return [f"{spec.name}: нужны пары вида «ключ=число», а стоит «{clipped(value)}»."]
    allowed = options(content, spec, record)
    found: list[str] = []
    unknown = [clipped(key) for key, _ in raw_pairs if key not in allowed]
    if unknown:
        found.append(f"{spec.name}: неизвестно — {clipped(', '.join(unknown), MAX_TEXT // 2)}.")
    for key, val in raw_pairs:
        if spec.pair_value is FieldKind.RATE and not _is_rate(val):
            found.append(f"{spec.name}: у «{clipped(key)}» нужна доля, а стоит «{clipped(val)}».")
        elif spec.pair_value is FieldKind.NUMBER and not _is_number(val):
            found.append(
                f"{spec.name}: у «{clipped(key)}» нужно целое число, а стоит «{clipped(val)}»."
            )
    return found


def _shape_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    """Проверки, которые нельзя сделать по одному полю."""
    match record.kind:
        case OverlayKind.QUEST:
            if record.number("target_count") < 1:
                return ["Считать меньше одного нельзя: задание закроется само собой."]
            if record.value("follows") == record.entity_id:
                return ["Задание не может идти после себя самого."]
            if not record.value("npc") and not record.value("giver"):
                return ["Некому платить: выберите жителя или впишите имя нанимателя."]
            return _quest_place_problems(content, record)
        case OverlayKind.LOCATION:
            return _location_problems(content, record)
        case OverlayKind.ENEMY:
            if not record.listed("biomes"):
                return ["Противнику негде водиться: выберите хотя бы одну местность."]
        case OverlayKind.TRAIT:
            category = record.value("category")
            if category and category not in content.trait_categories:
                return [f"Раздел: «{clipped(category)}» такого нет."]
        case OverlayKind.CRAFT:
            return _craft_problems(record)
        case OverlayKind.RECIPE:
            return _recipe_problems(content, record)
        case OverlayKind.META:
            return _meta_problems(content, record)
    return []


def _meta_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    """Опорное число можно двигать, но не ломать: отрицательных нет, потолок есть,
    а цена рангов должна покрыть все ранги.
    """
    found: list[str] = []
    for spec in FIELDS[OverlayKind.META]:
        raw = record.value(spec.key).strip()
        if not raw:
            continue
        if spec.kind is FieldKind.NUMBER:
            number = record.number(spec.key)
            if number < 0:
                found.append(f"{spec.name}: меньше нуля не бывает.")
            elif number > META_CEILING:
                found.append(f"{spec.name}: больше {META_CEILING} — это уже не тюнинг.")
            continue
        listed = record.numbers(spec.key)
        if any(one < 0 for one in listed):
            found.append(f"{spec.name}: отрицательных чисел здесь нет.")
        if spec.key == "rank_costs" and 0 < len(listed) < content.rules.max_rank:
            found.append(
                f"Цена рангов умений: нужно хотя бы {content.rules.max_rank} чисел — "
                "по одному на ранг."
            )
        if spec.key == "branch_gates":
            if listed and listed[0] != 0:
                found.append("Очки на ступени ветви: первое число всегда 0 — она открыта сразу.")
            if any(earlier > later for earlier, later in pairwise(listed)):
                found.append("Очки на ступени ветви: числа идут по возрастанию.")
    return found


def _craft_problems(record: OverlayRecord) -> list[str]:
    if record.value("kind") and record.value("kind") not in {one.value for one in CraftKind}:
        return ["Что делает: выберите «сбор» или «работа»."]
    if record.value("stat") and record.value("stat") not in {code.value for code in StatCode}:
        return ["На чём держится: выберите характеристику из списка."]
    return []


def _recipe_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    found: list[str] = []
    craft_id = record.value("craft")
    if craft_id and not content.has_craft(craft_id):
        found.append(f"Ремесло: «{clipped(craft_id)}» такого нет.")
    elif craft_id and content.craft(craft_id).gathers:
        found.append("Ремесло: рецепт вешают на работу, а это сбор.")
    if record.value("output") and not content.has_item(record.value("output")):
        found.append(f"Что выходит: «{clipped(record.value('output'))}» такой вещи нет.")
    rank = record.number("rank")
    if not 1 <= rank <= content.craft_rules.max_rank:
        found.append(f"С какого ранга: от 1 до {content.craft_rules.max_rank}, а стоит {rank}.")
    if record.value("output_count") and record.number("output_count") < 1:
        found.append("Сколько за раз: меньше одного не выйдет.")
    for key, raw in record.pairs("inputs"):
        try:
            count = int(raw)
        except ValueError:
            continue
        if count < 1:
            found.append(f"Из чего: «{clipped(key)}» — меньше одного не берут.")
    return found


def _quest_place_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    """Место, куда задание посылает, должно быть в том же городе."""
    slot = record.number("location_slot")
    if not slot:
        return []
    city_id = record.value("city")
    if not content.has_city(city_id):
        return []
    city = content.city(city_id)
    if not any(location.slot == slot for location in city.locations):
        return [f"Где делают: в городе {city.name} нет места {slot}."]
    return []


def _location_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    slot = record.number("slot")
    if not 1 <= slot <= MAX_LOCATION_SLOT:
        return [f"Место в списке: от 1 до {MAX_LOCATION_SLOT}, а стоит {slot}."]
    if record.number("level_min") > record.number("level_max"):
        return ["Уровень «от» больше уровня «до»: в такую локацию никто не попадёт."]
    if record.number("level_min") < 1:
        return ["Уровень «от» меньше первого."]
    city_id = record.value("city")
    if not content.has_city(city_id):
        return []
    taken = content.city(city_id).locations
    if any(other.slot == slot and other.id != record.entity_id for other in taken):
        occupied = next(other for other in taken if other.slot == slot)
        return [f"Место {slot} в городе занято локацией «{occupied.name}»."]
    return []


def _removal_problems(content: GameContent, record: OverlayRecord) -> tuple[str, ...]:
    """Убрать можно почти всё. Почти — это не последняя локация города и не
    опорные числа: без них игра не собирается вовсе.
    """
    if record.kind in NON_REMOVABLE:
        return ("Опорные числа есть всегда: их правят, а не убирают.",)
    if record.kind is not OverlayKind.LOCATION:
        return ()
    for city in content.cities:
        if any(location.id == record.entity_id for location in city.locations):
            if len(city.locations) <= 1:
                return (f"Это последняя локация города {city.name}: без неё в город незачем идти.",)
            return ()
    return ()


def _is_number(value: str) -> bool:
    return value.lstrip("-").isdigit()


def _is_rate(value: str) -> bool:
    try:
        float(value.replace(",", "."))
    except ValueError:
        return False
    return True


# --- применение --------------------------------------------------------


def apply(content: GameContent, records: Sequence[OverlayRecord]) -> GameContent:
    """Мир с правками: то же содержимое, собранное заново.

    Записи с отказами пропускаются целиком. Полуприменённая правка — это мир,
    про который нельзя сказать, что в нём стоит, поэтому её не бывает: запись
    либо работает вся, либо не работает вовсе.

    Порядок здесь есть, и он не декоративный. Сначала встаёт мир — города,
    локации и люди, — и только потом то, что на них ссылается: задание может быть
    выдан жителю, которого завели той же панелью час назад, а противник —
    поселён в локацию, которой в ``world.toml`` нет.
    """
    if not records:
        return content

    # Опорные числа, черты и ремёсла ни от чего не зависят — встают первыми.
    # Рецепты зависят и от ремёсел (вешаются на «работу»), и от вещей, поэтому
    # идут после ремёсел.
    rules = _apply_meta(content, _good(content, records, OverlayKind.META))
    traits = _apply_traits(content, _good(content, records, OverlayKind.TRAIT))
    crafts = _apply_crafts(content, _good(content, records, OverlayKind.CRAFT))

    cities = _apply_cities(content, _good(content, records, OverlayKind.CITY))
    cities = _apply_locations(cities, _good(content, records, OverlayKind.LOCATION))
    npcs = _apply_npcs(content, _good(content, records, OverlayKind.NPC))
    staged = _rebuilt(content, cities=cities, npcs=npcs, traits=traits, crafts=crafts, rules=rules)

    enemies = _apply_enemies(content, _good(staged, records, OverlayKind.ENEMY))
    # Противники встают до заданий, а не рядом с ними: задание может заказывать
    # именно того противника, которого смотритель завёл этой же панелью, и
    # проверять такое задание надо против мира, в котором тот уже есть.
    staged = _rebuilt(
        content,
        cities=cities,
        npcs=npcs,
        traits=traits,
        crafts=crafts,
        enemies=enemies,
        rules=rules,
    )
    quests = _apply_quests(staged, npcs, _good(staged, records, OverlayKind.QUEST))
    recipes = _apply_recipes(staged, _good(staged, records, OverlayKind.RECIPE))
    return _rebuilt(
        content,
        cities=cities,
        npcs=npcs,
        traits=traits,
        crafts=crafts,
        quests=quests,
        enemies=enemies,
        recipes=recipes,
        rules=rules,
    )


def _good(
    against: GameContent, records: Iterable[OverlayRecord], kind: OverlayKind
) -> tuple[OverlayRecord, ...]:
    return tuple(
        record for record in records if record.kind is kind and not problems(against, record)
    )


def _rebuilt(
    content: GameContent,
    *,
    cities: Sequence[City],
    npcs: Sequence[Npc],
    quests: Sequence[Quest] | None = None,
    enemies: Sequence[EnemyArchetype] | None = None,
    traits: Sequence[Trait] | None = None,
    crafts: Sequence[Craft] | None = None,
    recipes: Sequence[Recipe] | None = None,
    rules: ProgressionRules | None = None,
) -> GameContent:
    return GameContent.build(
        races=content.races,
        classes=content.classes,
        traits=content.traits if traits is None else traits,
        items=content.items,
        skills=content.skills,
        cities=cities,
        rarities=content.rarities,
        slots=content.slots,
        weapon_types=content.weapon_types,
        armor_types=content.armor_types,
        # Всё, что раньше молча терялось при любой правке: снаряжение глубокого
        # спуска собиралось из этих справочников, а Палата спрашивала из turnings.
        gear_tiers=content.gear_tiers,
        gear_archetypes=content.gear_archetypes,
        special_properties=content.special_properties,
        enemy_archetypes=content.enemy_archetypes if enemies is None else enemies,
        elite_titles=content.elite_titles,
        affixes=content.affixes,
        trait_categories=content.trait_categories,
        inverted_modifiers=content.inverted_modifiers,
        rules=content.rules if rules is None else rules,
        craft_rules=content.craft_rules,
        quests=content.quests if quests is None else quests,
        crafts=content.crafts if crafts is None else crafts,
        recipes=content.recipes if recipes is None else recipes,
        npcs=npcs,
        turnings=content.turnings,
        open_turning_id=content.open_turning_id,
    )


def _apply_cities(content: GameContent, records: Sequence[OverlayRecord]) -> tuple[City, ...]:
    """Город правится только словами: имя и описание.

    Уровни, порядок и услуги остаются за ``world.toml``, потому что они держат
    дорогу целиком — сдвинуть их кнопкой значит порвать её в середине.
    """
    patches = {record.entity_id: record for record in records if not record.removed}
    return tuple(
        replace(
            city,
            name=patches[city.id].value("name", city.name),
            description=patches[city.id].value("description", city.description),
        )
        if city.id in patches
        else city
        for city in content.cities
    )


def _apply_locations(cities: Sequence[City], records: Sequence[OverlayRecord]) -> tuple[City, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    edits = [record for record in records if not record.removed]

    rebuilt: list[City] = []
    for city in cities:
        kept = [location for location in city.locations if location.id not in dropped]
        mine = [record for record in edits if record.value("city") == city.id]
        by_id = {location.id: location for location in kept}
        for record in mine:
            # Два места под одним номером — это список, в котором вторая строка
            # недостижима: кнопка ведёт к первой. Побеждает та, что записана раньше.
            slot = record.number("slot")
            if any(loc.slot == slot and key != record.entity_id for key, loc in by_id.items()):
                continue
            by_id[record.entity_id] = _location_from(record, city.id)
        # Локация из другого города, переселённая сюда, уходит оттуда сама: её
        # больше нет ни в одном списке, кроме нового.
        moved = {record.entity_id for record in edits if record.value("city") != city.id}
        ordered = sorted(
            (loc for key, loc in by_id.items() if key not in moved), key=lambda loc: loc.slot
        )
        # Пустой город — город, в который незачем идти, и до этого нельзя дойти
        # даже несколькими правками подряд: каждая из них по отдельности законна,
        # а вместе они оставили бы список локаций пустым.
        if not ordered and city.locations:
            ordered = [city.locations[0]]
        rebuilt.append(replace(city, locations=tuple(ordered)))
    return tuple(rebuilt)


def _location_from(record: OverlayRecord, city_id: str) -> Location:
    return Location(
        id=record.entity_id,
        slot=record.number("slot"),
        name=record.value("name"),
        biome=record.value("biome"),
        level_min=record.number("level_min", 1),
        level_max=record.number("level_max", 1),
        city_id=city_id,
        pvp=record.flag("pvp"),
    )


def _apply_npcs(content: GameContent, records: Sequence[OverlayRecord]) -> tuple[Npc, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {npc.id: npc for npc in content.npcs if npc.id not in dropped}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = Npc(
            id=record.entity_id,
            city_id=record.value("city"),
            name=record.value("name"),
            role=record.value("role"),
            text=record.value("text"),
        )
    return tuple(by_id.values())


def _apply_quests(
    content: GameContent, npcs: Sequence[Npc], records: Sequence[OverlayRecord]
) -> tuple[Quest, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {quest.id: quest for quest in content.quests if quest.id not in dropped}
    named = {npc.id: npc for npc in npcs}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = _quest_from(record, named)
    return tuple(by_id.values())


def _quest_from(record: OverlayRecord, npcs: Mapping[str, Npc]) -> Quest:
    """Задание из записи. Имя нанимателя берётся у жителя, если он назван.

    Иначе оно осталось бы в двух местах сразу и однажды разошлось бы: житель
    переименован, а на доске всё ещё старое имя.
    """
    giver_id = record.value("npc")
    giver = npcs[giver_id].title if giver_id in npcs else record.value("giver")
    objective = record.value("objective")
    return Quest(
        id=record.entity_id,
        city_id=record.value("city"),
        level=max(1, record.number("level", 1)),
        name=record.value("name"),
        giver=giver,
        intro=record.value("intro"),
        terms=record.value("terms"),
        objective=ObjectiveKind(objective),
        target_count=record.number("target_count", 1),
        target_kind=record.value("target_kind"),
        reward_gold=record.number("reward_gold"),
        reward_experience=record.number("reward_experience"),
        reward_item=record.value("reward_item"),
        follows=record.value("follows"),
        giver_id=giver_id,
        # Изготовление делают руками где угодно, так же как в ``quests.toml``.
        location_slot=0 if objective == ObjectiveKind.CRAFT else record.number("location_slot"),
    )


def _apply_enemies(
    content: GameContent, records: Sequence[OverlayRecord]
) -> tuple[EnemyArchetype, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {enemy.id: enemy for enemy in content.enemy_archetypes if enemy.id not in dropped}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = EnemyArchetype(
            id=record.entity_id,
            name=record.value("name"),
            kind=EnemyKind(record.value("kind")),
            biomes=record.listed("biomes"),
            health=record.rate("health"),
            damage=record.rate("damage"),
            armor=record.rate("armor"),
            initiative=record.rate("initiative"),
            element=(DamageType(chosen) if (chosen := record.value("element")) else None),
            loot=record.listed("loot"),
            dungeon=record.flag("dungeon"),
        )
    return tuple(by_id.values())


def _apply_meta(content: GameContent, records: Sequence[OverlayRecord]) -> ProgressionRules:
    """Опорные числа с правками. Правок больше одной не бывает (сущность одна),
    но цикл всё равно проходит все: последняя запись побеждает, как и везде.
    """
    rules = content.rules
    for record in records:
        if record.removed:
            continue
        rules = _rules_from(rules, record)
    return rules


def _rules_from(rules: ProgressionRules, record: OverlayRecord) -> ProgressionRules:
    """Опорные числа из записи. Незаполненное поле оставляет то, что в файлах —
    правка меняет ровно названное. Поля те же, что в ``FIELDS[META]``.
    """

    def num(key: str, current: int) -> int:
        return record.number(key, current) if record.value(key).strip() else current

    def nums(key: str, current: tuple[int, ...]) -> tuple[int, ...]:
        return record.numbers(key) if record.value(key).strip() else current

    return replace(
        rules,
        base_stat_value=num("base_stat_value", rules.base_stat_value),
        free_points_at_creation=num("free_points_at_creation", rules.free_points_at_creation),
        stat_points_per_level=num("stat_points_per_level", rules.stat_points_per_level),
        skill_point_per_level=num("skill_point_per_level", rules.skill_point_per_level),
        rank_costs=nums("rank_costs", rules.rank_costs),
        branch_gates=nums("branch_gates", rules.branch_gates),
    )


def _apply_traits(content: GameContent, records: Sequence[OverlayRecord]) -> tuple[Trait, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {trait.id: trait for trait in content.traits if trait.id not in dropped}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = _trait_from(content, record)
    return tuple(by_id.values())


def _trait_from(content: GameContent, record: OverlayRecord) -> Trait:
    # Теги черты движок правил не читает (только её прибавки), но у написанной
    # черты они есть, и правка имени их не роняет.
    tags = content.trait(record.entity_id).tags if content.has_trait(record.entity_id) else ()
    modifiers = {
        key: float(raw.replace(",", ".")) for key, raw in record.pairs("modifiers") if _is_rate(raw)
    }
    return Trait(
        id=record.entity_id,
        name=record.value("name"),
        category=record.value("category"),
        tags=tags,
        modifiers=modifiers,
        text=record.value("text"),
    )


def _apply_crafts(content: GameContent, records: Sequence[OverlayRecord]) -> tuple[Craft, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {craft.id: craft for craft in content.crafts if craft.id not in dropped}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = _craft_from(content, record)
    return tuple(by_id.values())


def _craft_from(content: GameContent, record: OverlayRecord) -> Craft:
    # Находки сбора — вложенные списки биомов, их правят в ``crafts.toml``; правка
    # из панели их сохраняет, а заведённое смотрителем ремесло начинается без них.
    yields = content.craft(record.entity_id).yields if content.has_craft(record.entity_id) else ()
    return Craft(
        id=record.entity_id,
        name=record.value("name"),
        kind=CraftKind(record.value("kind")),
        stat=StatCode(record.value("stat")),
        description=record.value("description"),
        yields=yields,
    )


def _apply_recipes(content: GameContent, records: Sequence[OverlayRecord]) -> tuple[Recipe, ...]:
    dropped = {record.entity_id for record in records if record.removed}
    by_id = {recipe.id: recipe for recipe in content.recipes if recipe.id not in dropped}
    for record in records:
        if record.removed:
            continue
        by_id[record.entity_id] = _recipe_from(record)
    return tuple(by_id.values())


def _recipe_from(record: OverlayRecord) -> Recipe:
    inputs = tuple(
        RecipeInput(item_id=item_id, count=int(raw))
        for item_id, raw in record.pairs("inputs")
        if _is_number(raw)
    )
    return Recipe(
        id=record.entity_id,
        craft_id=record.value("craft"),
        rank=record.number("rank", 1),
        inputs=inputs,
        output_id=record.value("output"),
        output_count=record.number("output_count", 1),
        experience=record.number("experience"),
    )


# --- выгрузка в content/ ---------------------------------------------------
#
# Правка живёт в базе (``content_overlay``), и снять её начисто можно только
# перенеся в файл. Эти функции печатают фрагмент TOML в форме нужного файла —
# как ``scripts/broadcast.py`` печатает changelog, — чтобы смотритель вставил
# его руками, проверил и снял правку. Round-trip тут не самоцель: это заготовка,
# а не генератор.

#: У какой разновидности есть дом в ``content/``. У жителей его нет вовсе
#: (``docs/keeper.md``), поэтому их правка так и остаётся в базе.
EXPORTABLE: frozenset[OverlayKind] = frozenset(
    {
        OverlayKind.QUEST,
        OverlayKind.LOCATION,
        OverlayKind.ENEMY,
        OverlayKind.CITY,
        OverlayKind.TRAIT,
        OverlayKind.CRAFT,
        OverlayKind.RECIPE,
        OverlayKind.META,
    }
)

_TOML_FILE: Mapping[OverlayKind, str] = {
    OverlayKind.QUEST: "content/quests.toml",
    OverlayKind.LOCATION: "content/world.toml (под нужный [[city]])",
    OverlayKind.ENEMY: "content/enemies.toml",
    OverlayKind.CITY: "content/world.toml (правкой существующего [[city]])",
    OverlayKind.TRAIT: "content/traits.toml",
    OverlayKind.CRAFT: "content/crafts.toml",
    OverlayKind.RECIPE: "content/crafts.toml",
    OverlayKind.META: "нескольких файлов [meta] по одному числу",
}

_TOML_SECTION: Mapping[OverlayKind, str] = {
    OverlayKind.QUEST: "[[quest]]",
    OverlayKind.LOCATION: "[[city.location]]",
    OverlayKind.ENEMY: "[[enemy]]",
    OverlayKind.CITY: "[[city]]",
    OverlayKind.TRAIT: "[[trait]]",
    OverlayKind.CRAFT: "[[craft]]",
    OverlayKind.RECIPE: "[[recipe]]",
}


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _toml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_str(one) for one in values) + "]"


def _toml_num(value: str) -> str:
    return value.strip() or "0"


def to_toml(content: GameContent, record: OverlayRecord) -> str:
    """Фрагмент TOML для правки — заготовка, которую смотритель вставит в файл.

    ``record`` — уже сведённая запись (``effective``). Жители возвращают короткую
    строку: их в ``content/`` нет.
    """
    if record.kind is OverlayKind.NPC:
        return "# Жители в content/ не хранятся — эта правка так и живёт в базе."
    if record.kind is OverlayKind.META:
        return _meta_toml(content, record)

    header = (
        f"# правка {record.entity_id} — проверьте и вставьте в {_TOML_FILE[record.kind]}\n"
        f"{_TOML_SECTION[record.kind]}"
    )
    builders: Mapping[OverlayKind, Callable[[GameContent, OverlayRecord], list[str]]] = {
        OverlayKind.QUEST: _quest_toml,
        OverlayKind.ENEMY: _enemy_toml,
        OverlayKind.TRAIT: _trait_toml,
        OverlayKind.CRAFT: _craft_toml,
        OverlayKind.RECIPE: _recipe_toml,
        OverlayKind.LOCATION: _location_toml,
        OverlayKind.CITY: _city_toml,
    }
    body = builders[record.kind](content, record)
    return "\n".join([header, f"id = {_toml_str(record.entity_id)}", *body])


def _quest_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    lines = [
        f"city = {_toml_str(record.value('city'))}",
        f"level = {_toml_num(record.value('level') or '1')}",
        f"name = {_toml_str(record.value('name'))}",
    ]
    giver_id = record.value("npc")
    giver = content.npc(giver_id).title if content.has_npc(giver_id) else record.value("giver")
    if giver:
        lines.append(f"giver = {_toml_str(giver)}")
    if record.value("intro"):
        lines.append(f"intro = {_toml_str(record.value('intro'))}")
    lines.append(f"terms = {_toml_str(record.value('terms'))}")
    lines.append(f"objective = {_toml_str(record.value('objective'))}")
    if record.value("target_kind"):
        lines.append(f"target_kind = {_toml_str(record.value('target_kind'))}")
    if record.value("location_slot"):
        lines.append(f"location = {_toml_num(record.value('location_slot'))}")
    lines.append(f"target_count = {_toml_num(record.value('target_count') or '1')}")
    for key, name in (
        ("reward_gold", "reward_gold"),
        ("reward_experience", "reward_experience"),
    ):
        if record.value(key):
            lines.append(f"{name} = {_toml_num(record.value(key))}")
    if record.value("reward_item"):
        lines.append(f"reward_item = {_toml_str(record.value('reward_item'))}")
    if record.value("follows"):
        lines.append(f"follows = {_toml_str(record.value('follows'))}")
    return lines


def _enemy_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    lines = [
        f"name = {_toml_str(record.value('name'))}",
        f"kind = {_toml_str(record.value('kind'))}",
        f"biomes = {_toml_list(record.listed('biomes'))}",
    ]
    for key in ("health", "damage", "armor", "initiative"):
        if record.value(key):
            lines.append(f"{key} = {record.value(key).replace(',', '.')}")
    if record.value("element"):
        lines.append(f"element = {_toml_str(record.value('element'))}")
    if record.listed("loot"):
        lines.append(f"loot = {_toml_list(record.listed('loot'))}")
    if record.flag("dungeon"):
        lines.append("dungeon = true")
    return lines


def _trait_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    tags = content.trait(record.entity_id).tags if content.has_trait(record.entity_id) else ()
    pairs = ", ".join(f"{key} = {val.replace(',', '.')}" for key, val in record.pairs("modifiers"))
    lines = [
        f"name = {_toml_str(record.value('name'))}",
        f"category = {_toml_str(record.value('category'))}",
    ]
    if tags:
        lines.append(f"tags = {_toml_list(tags)}")
    lines.append("modifiers = { " + pairs + " }")
    if record.value("text"):
        lines.append(f"text = {_toml_str(record.value('text'))}")
    return lines


def _craft_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    lines = [
        f"name = {_toml_str(record.value('name'))}",
        f"kind = {_toml_str(record.value('kind'))}",
        f"stat = {_toml_str(record.value('stat'))}",
    ]
    if record.value("description"):
        lines.append(f"description = {_toml_str(record.value('description'))}")
    return lines


def _recipe_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    inputs = ", ".join(
        f"{{ item = {_toml_str(item_id)}, count = {_toml_num(raw)} }}"
        for item_id, raw in record.pairs("inputs")
    )
    return [
        f"craft = {_toml_str(record.value('craft'))}",
        f"rank = {_toml_num(record.value('rank') or '1')}",
        f"inputs = [{inputs}]",
        "output = { item = "
        + _toml_str(record.value("output"))
        + ", count = "
        + _toml_num(record.value("output_count") or "1")
        + " }",
        f"experience = {_toml_num(record.value('experience') or '0')}",
    ]


def _location_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    lines = [
        f"slot = {_toml_num(record.value('slot') or '1')}",
        f"name = {_toml_str(record.value('name'))}",
        f"biome = {_toml_str(record.value('biome'))}",
        f"level_min = {_toml_num(record.value('level_min') or '1')}",
        f"level_max = {_toml_num(record.value('level_max') or '1')}",
    ]
    if record.flag("pvp"):
        lines.append("pvp = true")
    return lines


def _city_toml(content: GameContent, record: OverlayRecord) -> list[str]:
    return [
        f"name = {_toml_str(record.value('name'))}",
        f"description = {_toml_str(record.value('description'))}",
    ]


def _meta_toml(content: GameContent, record: OverlayRecord) -> str:
    """Опорные числа правятся не одним блоком — печатаем их построчно с адресом."""
    rules = _rules_from(content.rules, record)
    return "\n".join(
        [
            "# опорные числа лежат по разным файлам [meta] — перенесите нужные строки:",
            f"# content/skills.toml   rank_costs = {list(rules.rank_costs)}",
            f"# content/skills.toml   branch_gates = {list(rules.branch_gates)}",
            f"# content/classes.toml  stat_points_per_level = {rules.stat_points_per_level}",
            f"# content/classes.toml  skill_point_per_level = {rules.skill_point_per_level}",
            f"# base_stat_value = {rules.base_stat_value}  "
            f"free_points_at_creation = {rules.free_points_at_creation}",
        ]
    )


def orphaned_biomes(content: GameContent) -> tuple[str, ...]:
    """Местности, в которых после правок некому водиться.

    Пустая местность — это локация, в которой не с кем драться, и заметить это
    надо смотрителю, а не игроку, который туда зашёл.
    """
    return tuple(
        biome
        for biome in biomes(content)
        if not any(enemy.fits(biome) for enemy in content.enemy_archetypes)
    )
