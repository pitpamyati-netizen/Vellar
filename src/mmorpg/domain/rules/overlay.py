"""Правки смотрителя: какие бывают поля, что в них можно, и как это ложится на мир.

Модуль отвечает на три вопроса и ни на один больше.

*Что у сущности за поля.* :data:`FIELDS` — единственное описание: по нему рисуется
карточка, по нему же разбирается набранное значение. Экран не знает, что у подряда
есть плата: он спрашивает здесь.

*Годится ли запись.* :func:`problems` возвращает отказы словами. Записи с отказами
не применяются — но и не пропадают: смотритель видит и правку, и причину, по
которой она пока не работает.

*Как выглядит мир с правками.* :func:`apply` собирает новое содержимое: TOML плюс
записи. Само содержимое неизменяемо, поэтому это именно новая сборка, а не запись
поверх старой — из-за чего правку и видно сразу всем, кто откроет экран после неё.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.content import City, GameContent, Location, Npc
from mmorpg.domain.entities.location import EnemyArchetype, EnemyKind
from mmorpg.domain.entities.overlay import KEEPER_PREFIX, OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import ObjectiveKind, Quest

#: Сколько мест под локации в городе. Пять кладёт содержимое, остальное — запас
#: смотрителя: город, в котором некуда добавить, — город, который нельзя править.
MAX_LOCATION_SLOT = 12

#: Потолок длины набранного значения. Экран читают вслух, и абзац в кнопке — это
#: абзац, который слушают целиком (``docs/accessibility.md``).
MAX_TEXT = 240

#: Потолок для того, что попадёт на кнопку: имя жителя, название подряда,
#: локации, противника. Кнопка — это одна строка, и она должна оставаться одной.
NAME_LIMIT = 48

#: Узлы, которые подряд может считать без боя. Те же слова, что в ``quests.toml``.
SEARCHABLE_NODES: tuple[str, ...] = ("gather", "cache", "shrine", "event")


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


class Source(StrEnum):
    """Откуда берётся список вариантов, если он не записан в самом поле."""

    NONE = "none"
    CITY = "city"
    NPC = "npc"
    BIOME = "biome"
    ITEM = "item"
    QUEST = "quest"


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


#: Как разновидность называется в единственном и множественном числе. Первое —
#: заголовок карточки, второе — кнопка и заголовок списка.
TITLES: Mapping[OverlayKind, tuple[str, str]] = {
    OverlayKind.NPC: ("Житель", "Жители"),
    OverlayKind.QUEST: ("Подряд", "Подряды"),
    OverlayKind.LOCATION: ("Локация", "Локации"),
    OverlayKind.ENEMY: ("Противник", "Противники"),
    OverlayKind.CITY: ("Город", "Города"),
}

#: Разновидности, которые смотритель заводит с нуля. Города и локации приходят из
#: ``world.toml`` вместе с проверкой уровней, и заводить город кнопкой — значит
#: обойти эту проверку; локацию в существующем городе добавить можно.
CREATABLE: frozenset[OverlayKind] = frozenset(
    {OverlayKind.NPC, OverlayKind.QUEST, OverlayKind.LOCATION, OverlayKind.ENEMY}
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
        FieldSpec("giver", "Имя нанимателя", hint="если подряд не от жителя"),
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
        FieldSpec("level", "С какого уровня", FieldKind.NUMBER),
        FieldSpec("reward_gold", "Плата золотом", FieldKind.NUMBER),
        FieldSpec("reward_experience", "Плата опытом", FieldKind.NUMBER),
        FieldSpec("reward_item", "Что дают сверху", FieldKind.CHOICE, source=Source.ITEM),
        FieldSpec("follows", "После какого подряда", FieldKind.CHOICE, source=Source.QUEST),
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
        FieldSpec("initiative", "Прыть, доля", FieldKind.RATE),
        FieldSpec("loot", "Что падает", FieldKind.LIST, source=Source.ITEM),
    ),
    OverlayKind.CITY: (
        FieldSpec("name", "Название", required=True, limit=NAME_LIMIT),
        FieldSpec("description", "Описание"),
    ),
}

#: Порода противника и узлы поиска — разные списки, и какой из них нужен, зависит
#: от того, что подряд считает. Единственное поле, чьи варианты зависят от соседа.
_TARGETS: Mapping[ObjectiveKind, tuple[str, ...]] = {
    ObjectiveKind.KILL: tuple(kind.value for kind in EnemyKind),
    ObjectiveKind.ELITE: tuple(kind.value for kind in EnemyKind),
    ObjectiveKind.SEARCH: SEARCHABLE_NODES,
}


def spec_of(kind: OverlayKind, key: str) -> FieldSpec | None:
    return next((spec for spec in FIELDS[kind] if spec.key == key), None)


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
        objective = record.value("objective")
        return _TARGETS.get(ObjectiveKind(objective), ()) if objective in _TARGETS else ()
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
        case _:
            return spec.choices


def option_name(content: GameContent, spec: FieldSpec, value: str) -> str:
    """Как вариант зовут по-русски. Идентификатор без имени никому ничего не говорит."""
    if not value:
        return "не выбрано"
    match spec.source:
        case Source.CITY:
            return content.city(value).name if content.has_city(value) else value
        case Source.NPC:
            return content.npc(value).title if content.has_npc(value) else value
        case Source.ITEM:
            return content.item(value).name if content.has_item(value) else value
        case Source.QUEST:
            return content.quest(value).name if content.has_quest(value) else value
        case _:
            return _WORDS.get(value, value)


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
            return option_name(content, spec, value)
        case FieldKind.LIST:
            named = [option_name(content, spec, part) for part in record.listed(spec.key)]
            return ", ".join(named) if named else "не выбрано"
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
        case _:
            return tuple(
                (city.id, f"{city.name} — уровни с {city.level_min} по {city.level_max}")
                for city in content.cities
            )


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
        "loot": ", ".join(enemy.loot),
    }


def _rate(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


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
    return []


def _shape_problems(content: GameContent, record: OverlayRecord) -> list[str]:
    """Проверки, которые нельзя сделать по одному полю."""
    match record.kind:
        case OverlayKind.QUEST:
            if record.number("target_count") < 1:
                return ["Считать меньше одного нельзя: подряд закроется сам собой."]
            if record.value("follows") == record.entity_id:
                return ["Подряд не может идти после себя самого."]
            if not record.value("npc") and not record.value("giver"):
                return ["Некому платить: выберите жителя или впишите имя нанимателя."]
        case OverlayKind.LOCATION:
            return _location_problems(content, record)
        case OverlayKind.ENEMY:
            if not record.listed("biomes"):
                return ["Противнику негде водиться: выберите хотя бы одну местность."]
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
    """Убрать можно почти всё. Почти — это не последняя локация города."""
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
    локации и люди, — и только потом то, что на них ссылается: подряд может быть
    выдан жителю, которого завели той же панелью час назад, а противник —
    поселён в локацию, которой в ``world.toml`` нет.
    """
    if not records:
        return content

    cities = _apply_cities(content, _good(content, records, OverlayKind.CITY))
    cities = _apply_locations(cities, _good(content, records, OverlayKind.LOCATION))
    npcs = _apply_npcs(content, _good(content, records, OverlayKind.NPC))
    staged = _rebuilt(content, cities=cities, npcs=npcs)

    enemies = _apply_enemies(content, _good(staged, records, OverlayKind.ENEMY))
    quests = _apply_quests(staged, npcs, _good(staged, records, OverlayKind.QUEST))
    return _rebuilt(content, cities=cities, npcs=npcs, quests=quests, enemies=enemies)


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
) -> GameContent:
    return GameContent.build(
        races=content.races,
        classes=content.classes,
        traits=content.traits,
        items=content.items,
        skills=content.skills,
        cities=cities,
        rarities=content.rarities,
        enemy_archetypes=content.enemy_archetypes if enemies is None else enemies,
        elite_titles=content.elite_titles,
        trait_categories=content.trait_categories,
        inverted_modifiers=content.inverted_modifiers,
        rules=content.rules,
        craft_rules=content.craft_rules,
        quests=content.quests if quests is None else quests,
        crafts=content.crafts,
        recipes=content.recipes,
        npcs=npcs,
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
    """Подряд из записи. Имя нанимателя берётся у жителя, если он назван.

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
            loot=record.listed("loot"),
        )
    return tuple(by_id.values())


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
