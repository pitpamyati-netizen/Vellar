"""Screens for the game itself: menu, world, city, locations.

Each one opens with where the player is, then the detail, then what can be done -
in that order, because a player hears the message top to bottom and should be able
to stop listening as soon as they know enough (accessibility rule 4).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent, Location
from mmorpg.domain.entities.location import GeneratedLocation, LocationNode, NodeKind
from mmorpg.domain.rules.progression import experience_into_level
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

# Actions offered at a node, by node kind. The wording says what will happen, so
# nothing depends on an icon or a colour.
NODE_ACTIONS: dict[NodeKind, str] = {
    NodeKind.ENTRANCE: "Осмотреть вход",
    NodeKind.BATTLE: "Вступить в бой",
    NodeKind.ELITE_BATTLE: "Вызвать эпического противника",
    NodeKind.BOSS_BATTLE: "Вызвать хозяина логова",
    NodeKind.GATHER: "Собрать ресурсы",
    NodeKind.EVENT: "Разобраться с событием",
    NodeKind.CACHE: "Обыскать тайник",
    NodeKind.SHRINE: "Помолиться у святилища",
    # Distinct from the always-present "Покинуть локацию" button below it: two
    # buttons on one screen may never share a label, since routing is by text.
    NodeKind.EXIT: "Выйти через узел",
}

NODE_DESCRIPTIONS: dict[NodeKind, str] = {
    NodeKind.ENTRANCE: "Отсюда вы вошли",
    NodeKind.BATTLE: "здесь ждёт противник",
    NodeKind.ELITE_BATTLE: "здесь ждёт эпический противник, бой будет долгим",
    NodeKind.BOSS_BATTLE: "здесь ждёт хозяин логова, бой будет самым долгим",
    NodeKind.GATHER: "здесь есть что собрать",
    NodeKind.EVENT: "здесь что-то происходит",
    NodeKind.CACHE: "здесь спрятан тайник",
    NodeKind.SHRINE: "здесь можно перевести дух",
    NodeKind.EXIT: "отсюда можно уйти",
}

LEAVE_LOCATION = label("Покинуть локацию")

# Equipment slots, in the order they are read out. The ids are content keys; the
# names are what a player hears.
SLOT_NAMES: dict[str, str] = {
    "weapon": "Оружие",
    "head": "Голова",
    "body": "Тело",
    "hands": "Руки",
    "feet": "Ноги",
    "trinket": "Украшение",
}


def unequip_label(slot_name: str) -> Label:
    return label(f"Снять: {slot_name}")


def node_button(node: LocationNode) -> Label:
    """Node buttons carry their index, so two "Стычка" nodes stay distinguishable."""
    return label(f"Узел {node.index}: {node.name}")


def main_menu_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    city = content.city(character.city_id)
    earned, needed = experience_into_level(character.experience)
    progress = (
        f"Опыт: {amount(earned, needed, with_percent=False)} до следующего уровня."
        if needed
        else "Достигнут максимальный уровень."
    )
    lines = [
        notice or f"Главное меню. Вы в городе {city.name}.",
        f"{character.name}, уровень {character.level}, "
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {amount(character.health_or(stats.max_health), stats.max_health)}.",
        f"{stats.resource_name}: {amount(stats.max_resource, stats.max_resource)}.",
        progress,
        f"Золото: {gold(character.gold)}.",
    ]
    if character.unspent_stat_points or character.unspent_skill_points:
        lines.append(
            f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
            f"очков умений {character.unspent_skill_points}."
        )
    rows = [
        (labels.WORLD,),
        (labels.CHARACTER, labels.INVENTORY),
        (labels.SKILLS, labels.QUESTS),
        (labels.CRAFTS, labels.SETTINGS),
    ]
    # The introduction is offered while it has something left to say, and then it
    # goes away for good - a permanent "tutorial" button on a played character is
    # a button that answers a question nobody is asking any more.
    from mmorpg.domain.rules import tutorial as tutorial_rules

    if not tutorial_rules.finished(character):
        next_task = tutorial_rules.next_task(character)
        if next_task is not None:
            from mmorpg.presentation.telegram.screens.tutorial import CARDS

            lines.append(f"Обучение: {CARDS[next_task].title.lower()}.")
        rows.append((labels.TUTORIAL,))
    # The service door, and only for whoever keeps the game: an ordinary player
    # never hears this row at all.
    if character.is_admin:
        rows.append((labels.KEEPER,))
    return Screen(id=ScreenId.MAIN_MENU, lines=tuple(lines), rows=tuple(rows))


def world_screen(
    content: GameContent, character: Character, state: PageState, notice: str = ""
) -> Screen:
    # A keeper walks the whole road: the level gate is a rule for players.
    open_cities = (
        content.cities if character.is_admin else content.cities_available_at(character.level)
    )
    entries = [
        ListEntry(
            key=city.id,
            text=city.name,
            detail=(
                f"уровни с {city.level_min} по {city.level_max}"
                + (", вы здесь" if city.id == character.city_id else "")
            ),
        )
        for city in open_cities
    ]
    # Cities stand along one road in one order, and each one opens at a level.
    # Naming the next one turns "closed: 12" into something to aim at.
    ahead = [city for city in content.cities if city.unlock_level > character.level]
    next_city = min(ahead, key=lambda city: city.unlock_level) if ahead else None
    lead = [
        notice or f"Вы в городе {content.city(character.city_id).name}.",
        "Города стоят вдоль одной дороги по порядку: чем дальше, тем выше уровни.",
    ]
    if next_city is not None:
        lead.append(
            f"Следующий город {next_city.name} откроется на уровне {next_city.unlock_level}. "
            f"Ваш уровень: {character.level}. Закрыто городов: {len(ahead)}."
        )
    else:
        lead.append("Дорога открыта до конца: закрытых городов нет.")
    return paginated_screen(
        screen_id=ScreenId.WORLD,
        title="Мир",
        entries=entries,
        state=state,
        lead_lines=tuple(lead),
        empty_text="Пока не открыт ни один город.",
        show_filters=False,
    )


# Which button opens which declared service. A city that does not declare one
# does not show its button - and says so if the button arrives from an old
# keyboard (accessibility rule 12).
CITY_SERVICES: tuple[tuple[str, Label], ...] = (
    ("locations", labels.LOCATIONS),
    ("shop", labels.SHOP),
    ("dungeons", labels.DUNGEONS),
    ("tavern", labels.TAVERN),
    ("mentor", labels.MENTOR),
    ("bank", labels.BANK),
)


def city_screen(content: GameContent, city: City, character: Character, notice: str = "") -> Screen:
    offered = [item for service, item in CITY_SERVICES if service in city.services]
    rows: list[tuple[Label, ...]] = [
        tuple(offered[index : index + 2]) for index in range(0, len(offered), 2)
    ]
    return Screen(
        id=ScreenId.CITY,
        lines=(
            notice or f"Город {city.name}.",
            city.description,
            f"Уровни города: с {city.level_min} по {city.level_max}. "
            f"Ваш уровень: {character.level}.",
            "Доступно: " + ", ".join(item.text.lower() for item in offered) + ".",
        ),
        rows=tuple(rows),
    )


def location_list_screen(
    content: GameContent,
    city: City,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    entries = [
        ListEntry(
            key=str(location.slot),
            text=f"{location.slot}. {location.name}",
            detail=_location_fit(location, character.level),
        )
        for location in city.locations
    ]
    return paginated_screen(
        screen_id=ScreenId.LOCATION_LIST,
        title=f"Локации города {city.name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Ваш уровень: {character.level}.",
            "В локации ходят по узлам: бой платит опытом, золотом и добычей, "
            "тайники и заросли — находками. Раны остаются с вами до лекаря.",
            "Локации перекрываются по уровням: всегда есть куда идти и куда вернуться.",
        ),
        show_filters=False,
    )


def _location_fit(location: Location, level: int) -> str:
    """Whether this place is worth the walk right now, in one phrase."""
    band = f"уровни с {location.level_min} по {location.level_max}"
    if level < location.level_min:
        return f"{band}, вам сюда рано"
    if level > location.level_max:
        return f"{band}, платит мало на вашем уровне"
    return f"{band}, по вашему уровню"


def risk_line(node: LocationNode, character_level: int) -> str:
    """How this fight is likely to go, said before it is picked.

    A level number alone tells a player nothing until they have lost to it once.
    The gap is what matters, so the gap is what the screen names.
    """
    if not node.kind.is_combat:
        return ""
    gap = node.level - character_level
    if gap <= -3:
        risk = "противник слабее вас, бой короткий"
    elif gap <= 1:
        risk = "противник вам вровень"
    elif gap <= 4:
        risk = "противник сильнее вас, бой опасный"
    else:
        risk = "противник намного сильнее вас, сюда рано"
    tier = {
        NodeKind.ELITE_BATTLE: " Эпический противник держится вдвое дольше обычного.",
        NodeKind.BOSS_BATTLE: " Хозяин логова держится вчетверо дольше обычного.",
    }.get(node.kind, "")
    return f"Уровень узла {node.level}, ваш {character_level}: {risk}.{tier}"


def location_screen(
    location: GeneratedLocation,
    node: LocationNode,
    *,
    cleared: int,
    character_level: int = 1,
    notice: str = "",
) -> Screen:
    from mmorpg.domain.procgen.location import is_cleared

    neighbours = tuple(location.node(index) for index in node.links)
    # The doors are not spoils: they are never counted as things to do here, so
    # "пройдено 0 из 12" cannot mean a location that is already half walked.
    worth_doing = tuple(
        item for item in location.nodes if item.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT}
    )
    done_count = sum(1 for item in worth_doing if is_cleared(cleared, item.index))
    boss = next((item for item in location.nodes if item.kind is NodeKind.BOSS_BATTLE), None)

    lines = [
        notice or f"Локация {location.name}, узел {node.index}: {node.name}.",
        f"{NODE_DESCRIPTIONS[node.kind].capitalize()}.",
    ]
    risk = risk_line(node, character_level)
    if risk:
        lines.append(risk)
    if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        lines.append("Это дверь, а не дело: здесь ничего не найти.")
    elif is_cleared(cleared, node.index):
        lines.append("Этот узел вы уже прошли, второй раз он ничего не даст.")

    lines.append(f"Пройдено узлов: {done_count} из {len(worth_doing)}.")
    if done_count == 0:
        # Said once, at the start of a visit: what this place is and how it works.
        lines.append(
            "Узлы связаны тропами: чем дальше от входа, тем выше уровень и тем "
            "больше платят. Обходить можно что угодно, идти до конца не обязательно."
        )
    if boss is not None and not is_cleared(cleared, boss.index):
        lines.append(
            f"Хозяин логова стоит в узле {boss.index}, уровень {boss.level}. Мимо есть путь."
        )

    lines.append("Отсюда ведут тропы:")
    for neighbour in neighbours:
        mark = ", пройден" if is_cleared(cleared, neighbour.index) else ""
        lines.append(
            f"Узел {neighbour.index}: {neighbour.name}, {NODE_DESCRIPTIONS[neighbour.kind]}{mark}."
        )

    action = label(NODE_ACTIONS[node.kind])
    rows: list[tuple[Label, ...]] = [(action,)]
    rows.extend((node_button(neighbour),) for neighbour in neighbours)
    rows.append((LEAVE_LOCATION,))

    return Screen(id=ScreenId.LOCATION, lines=tuple(lines), rows=tuple(rows))


def character_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        notice or f"Персонаж {character.name}, уровень {character.level}.",
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {character.health_or(stats.max_health)} из {stats.max_health}. "
        f"{stats.resource_name}: {stats.max_resource}.",
        f"Броня: {stats.armor}. Точность: {stats.accuracy}. Уклонение: {stats.dodge} процентов.",
        f"Шанс крита: {stats.crit_chance} процентов. Инициатива: {stats.initiative}.",
    ]
    lines.extend(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items())
    if character.trait_ids:
        names = ", ".join(content.trait(trait_id).name for trait_id in character.trait_ids)
        lines.append(f"Особенности: {names}.")

    worn: list[tuple[Label, ...]] = []
    for slot, slot_name in SLOT_NAMES.items():
        item_id = character.equipment.item_in(slot)
        if item_id is None or not content.has_item(item_id):
            continue
        lines.append(f"{slot_name}: {content.item(item_id).name}.")
        worn.append((unequip_label(slot_name),))
    if not worn:
        lines.append("Ничего не надето. Снаряжение надевается из инвентаря.")

    if character.unspent_stat_points or character.unspent_skill_points:
        lines.append(
            f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
            f"очков умений {character.unspent_skill_points}."
        )
    if character.unspent_stat_points:
        lines.append("Очки характеристик вкладываются в разделе «Характеристики».")
    return Screen(
        id=ScreenId.CHARACTER,
        lines=tuple(lines),
        rows=((labels.STATS, labels.SKILLS), *worn),
    )


def spend_label(stat_name: str) -> Label:
    return label(f"Вложить: {stat_name}")


def _number(value: float) -> str:
    """A number as it is read aloud: no trailing zero, comma for the fraction."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def stat_effect_lines(content: GameContent, character: Character) -> tuple[str, ...]:
    """What one point in each stat actually does, in this character's numbers.

    Every number here is read from the rule constants rather than typed out, so a
    balance change cannot leave the explanation lying.
    """
    from mmorpg.domain.entities.stats import StatCode
    from mmorpg.domain.rules import economy
    from mmorpg.domain.rules import stats as stat_rules
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    klass = content.character_class(character.class_id)
    key_codes = tuple(klass.key_stats)
    key_names = ", ".join(STAT_NAMES[StatCode(code)].lower() for code in key_codes)
    damage_stat = STAT_NAMES[StatCode(key_codes[0])].lower() if key_codes else "ключевая"

    effects: dict[StatCode, str] = {
        StatCode.STR: "Сила: урон в бою, когда класс дерётся силой.",
        StatCode.AGI: (
            f"Ловкость: за очко плюс {_number(stat_rules.ACCURACY_PER_AGILITY)} к точности, "
            f"{_number(stat_rules.DODGE_PER_AGILITY)} процента уклонения и "
            f"{_number(stat_rules.INITIATIVE_PER_AGILITY)} инициативы."
        ),
        StatCode.END: (
            f"Выносливость: за очко плюс {_number(stat_rules.ARMOR_PER_ENDURANCE)} брони и "
            f"{_number(klass.health.per_endurance)} здоровья."
        ),
        StatCode.INT: "Интеллект: урон заклинаний и запас маны у магических классов.",
        StatCode.WIS: (
            f"Мудрость: за очко плюс {_number(stat_rules.RESOURCE_REGEN_PER_WISDOM)} ресурса "
            "за ход, и сильнее лечение."
        ),
        StatCode.CHA: (
            f"Харизма: за очко скидка {_number(economy.CHARISMA_DISCOUNT_PER_POINT)} процента "
            f"в лавке, до {_number(economy.MAX_CHARISMA_DISCOUNT)} процентов."
        ),
        StatCode.LCK: (
            f"Удача: за очко плюс {_number(stat_rules.CRIT_CHANCE_PER_LUCK)} процента к шансу "
            f"крита и к его силе, потолок шанса {_number(stat_rules.MAX_CRIT_CHANCE)} процентов."
        ),
    }
    lead = (
        f"Класс {klass.name} дерётся характеристикой {damage_stat}. Ключевые: {key_names}."
        if key_codes
        else f"Класс {klass.name} не объявил ключевых характеристик."
    )
    return (lead, *(effects[code] for code in STAT_NAMES))


def stats_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    """Every primary stat: what it is now, what it does, and where a point goes."""
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        notice or f"Характеристики. Свободных очков: {character.unspent_stat_points}.",
        "Очко вкладывается навсегда, обратно оно не берётся.",
        f"Здоровье {stats.max_health}, броня {stats.armor}, "
        f"точность {_number(stats.accuracy)}, уклонение {_number(stats.dodge)} процентов.",
        *(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items()),
        "На что они влияют.",
        *stat_effect_lines(content, character),
    ]
    rows: list[tuple[Label, ...]] = []
    if character.unspent_stat_points:
        stat_names = list(STAT_NAMES.values())
        rows = [
            tuple(spend_label(name) for name in stat_names[index : index + 2])
            for index in range(0, len(stat_names), 2)
        ]
    else:
        lines.append("Свободных очков нет: их даёт новый уровень.")
    return Screen(id=ScreenId.STATS, lines=tuple(lines), rows=tuple(rows))


def stub_screen(title: str, notice: str = "") -> Screen:
    """A not-yet-built feature.

    A stub is a real screen with a working "Назад", never silence - see the
    accessibility rules: the game must always answer.
    """
    return Screen(
        id=ScreenId.STUB,
        lines=(
            f"{title}. Этот раздел ещё не готов.",
            notice or "Он появится в одном из следующих обновлений.",
            "Нажмите «Назад», чтобы вернуться, или «Главное меню».",
        ),
    )
