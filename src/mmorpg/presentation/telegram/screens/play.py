"""Screens for the game itself: menu, world, city, locations.

Each one opens with where the player is, then the detail, then what can be done -
in that order, because a player hears the message top to bottom and should be able
to stop listening as soon as they know enough (accessibility rule 4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent, Location
from mmorpg.domain.entities.location import (
    GeneratedLocation,
    LocationNode,
    NodeKind,
    Presence,
)
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules.combat import blow_range
from mmorpg.domain.rules.nodes import Standing
from mmorpg.domain.rules.progression import LevelUp, experience_into_level
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import (
    amount,
    gold,
    head,
    number,
    percent,
    plural,
)
from mmorpg.presentation.telegram.screens.items import SLOT_NAMES
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
    # «Сырьё», а не «ресурсы»: узел для сбора отдаёт только материалы
    # (``rules/adventure.GATHER_SOURCES``), и ремесло зовёт их тем же словом.
    NodeKind.GATHER: "Собрать сырьё",
    NodeKind.EVENT: "Разобраться с событием",
    NodeKind.CACHE: "Обыскать тайник",
    NodeKind.SHRINE: "Передохнуть у святилища",
    # Distinct from the always-present "Покинуть локацию" button below it: two
    # buttons on one screen may never share a label, since routing is by text.
    NodeKind.EXIT: "Выйти через узел",
}

# Одна строка на узел, и она же читается в списке соседних узлов - поэтому
# короткая. Длинное описание места здесь стоило бы игроку целого экрана.
NODE_DESCRIPTIONS: dict[NodeKind, str] = {
    NodeKind.ENTRANCE: "Отсюда вы вошли",
    NodeKind.BATTLE: "здесь ждёт противник",
    NodeKind.ELITE_BATTLE: "здесь ждёт эпический противник, бой будет долгим",
    NodeKind.BOSS_BATTLE: "здесь ждёт хозяин логова, бой будет самым долгим",
    NodeKind.GATHER: "здесь берут сырьё",
    NodeKind.EVENT: "здесь что-то стряслось до вас",
    NodeKind.CACHE: "здесь что-то припрятано",
    NodeKind.SHRINE: "здесь переводят дух",
    NodeKind.EXIT: "отсюда можно уйти",
}

# What a node counts, said in the words of the thing itself: "Противников: 2 из 3"
# reads as a place with something in it, "осталось 2" reads as a leftover.
NODE_COUNT_WORDS: dict[NodeKind, str] = {
    NodeKind.BATTLE: "Противников",
    NodeKind.ELITE_BATTLE: "Эпических противников",
    NodeKind.BOSS_BATTLE: "Хозяев логова",
    NodeKind.GATHER: "Осталось собрать",
    NodeKind.EVENT: "Событий",
    NodeKind.CACHE: "Тайников",
    NodeKind.SHRINE: "Святилищ",
}

#: Узел, о котором экрану ничего не сказали: держит ноль и ничего не обещает.
EMPTY_NODE = Standing(index=0, size=0, left=0, taken=0, wave=0, refill_in=0)

LEAVE_LOCATION = label("Покинуть локацию")


def unequip_label(slot_name: str) -> Label:
    return label(f"Снять: {slot_name}")


def node_button(node: LocationNode) -> Label:
    """Node buttons carry their index, so two "Стычка" nodes stay distinguishable."""
    return label(f"Узел {node.index}: {node.name}")


def standing_in(content: GameContent, character: Character) -> City:
    """Город, в котором стоит персонаж, или первый на дороге.

    Смотритель может убрать город правкой, пока в нём кто-то стоит
    (``docs/keeper.md``). Главное меню обязано открыться и тогда: экран, который
    падает у всех жителей убранного города, — это игра, в которую они больше не
    могут войти (``Claude.md``, правило 8).
    """
    if content.has_city(character.city_id):
        return content.city(character.city_id)
    return content.cities[0]


def main_menu_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    city = standing_in(content, character)
    earned, needed = experience_into_level(character.experience)
    progress = (
        f"Опыт: {amount(earned, needed, with_percent=False)} до следующего уровня."
        if needed
        else "Достигнут максимальный уровень."
    )
    lines = [
        *head(f"Главное меню. Вы в городе {city.name}.", notice),
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
    # Служебного ряда здесь нет, и это единственный экран, где его нет: «Назад»
    # из главного меню вело в главное меню, а «Главное меню» - туда, где игрок
    # уже стоит. Две кнопки, которые ничего не делают, на самом слышимом экране
    # игры (``Claude.md``, правило 9). Обе команды - /назад и /меню - работают.
    return Screen(id=ScreenId.MAIN_MENU, lines=tuple(lines), rows=tuple(rows), service_row=False)


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
        notice or f"Вы в городе {standing_in(content, character).name}.",
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
    ("arena", labels.ARENA),
    ("chamber", labels.CHAMBER),
    ("tavern", labels.TAVERN),
    ("mentor", labels.MENTOR),
    ("bank", labels.BANK),
)


def city_screen(content: GameContent, city: City, character: Character, notice: str = "") -> Screen:
    offered = [item for service, item in CITY_SERVICES if service in city.services]
    # Жители - не услуга города, а люди в нём: кнопка есть там, где кто-то стоит,
    # и нет там, где никого нет. Объявлять их в world.toml незачем - они приходят
    # правкой смотрителя (``docs/keeper.md``).
    if content.npcs_in(city.id):
        offered.append(labels.NPCS)
    rows: list[tuple[Label, ...]] = [
        tuple(offered[index : index + 2]) for index in range(0, len(offered), 2)
    ]
    return Screen(
        id=ScreenId.CITY,
        lines=(
            *head(f"Город {city.name}.", notice),
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
    if location.pvp:
        band = f"{band}, вольная: здесь нападают друг на друга"
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


def attack_label(name: str) -> Label:
    return label(f"Напасть: {name}")


def node_left_line(standing: Standing, kind: NodeKind) -> str:
    """Сколько тут ещё есть — словами, без псевдографики.

    Узел больше не «пройден кем-то»: он держит волну, из неё берут по одному, и
    пустой узел через несколько минут снова полон (``domain/rules/nodes.py``).
    """
    if kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        return ""
    if standing.empty:
        minutes = max(1, (standing.refill_in + 59) // 60)
        return (
            f"Здесь пусто. Новое появится примерно через {minutes} "
            f"{plural(minutes, 'минуту', 'минуты', 'минут')}."
        )
    word = NODE_COUNT_WORDS[kind]
    return f"{word}: {standing.left} из {standing.size}."


def location_screen(
    location: GeneratedLocation,
    node: LocationNode,
    *,
    standing: Mapping[int, Standing],
    character_level: int = 1,
    others: Sequence[Presence] = (),
    pvp: bool = False,
    notice: str = "",
) -> Screen:
    neighbours = tuple(location.node(index) for index in node.links)

    def left_at(index: int) -> Standing:
        """Что стоит в узле. Про узел, о котором не сказали, экран не падает."""
        return standing.get(index, EMPTY_NODE)

    here = left_at(node.index)
    # The doors are never counted: they hold nothing, and counting them would make
    # a location look half empty the moment you walked in.
    worth_doing = tuple(
        item for item in location.nodes if item.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT}
    )
    busy = sum(1 for item in worth_doing if not left_at(item.index).empty)
    boss = next((item for item in location.nodes if item.kind is NodeKind.BOSS_BATTLE), None)

    lines = list(head(f"Локация {location.name}, узел {node.index}: {node.name}.", notice))
    if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        lines.append(f"{NODE_DESCRIPTIONS[node.kind].capitalize()}.")
        lines.append("Это дверь, а не дело: здесь ничего не найти.")
    elif here.empty:
        # "Здесь есть что собрать" сразу под "здесь пусто" - это две строки,
        # которые спорят друг с другом. Пустой узел говорит только одно.
        lines.append(node_left_line(here, node.kind))
    else:
        lines.append(f"{NODE_DESCRIPTIONS[node.kind].capitalize()}.")
        risk = risk_line(node, character_level)
        if risk:
            lines.append(risk)
        lines.append(node_left_line(here, node.kind))

    lines.append(f"Узлов, где ещё что-то есть: {busy} из {len(worth_doing)}.")
    if busy == len(worth_doing):
        # Said once, at the start of a visit: what this place is and how it works.
        lines.append(
            "Узлы связаны тропами: чем дальше от входа, тем выше уровень и тем "
            "больше платят. Обходить можно что угодно, идти до конца не обязательно."
        )
        lines.append(
            "Локация никуда не денется: карта у неё одна и та же всегда, а вот "
            "кто и что в узлах — заводится заново через несколько минут после того, "
            "как узел вычистили."
        )
    if boss is not None and not left_at(boss.index).empty:
        lines.append(
            f"Хозяин логова стоит в узле {boss.index}, уровень {boss.level}. Мимо есть путь."
        )

    lines.append("Отсюда ведут тропы:")
    for neighbour in neighbours:
        # Двери не пустеют: у них и не было ничего, и говорить о них "сейчас
        # пусто" значит обещать, что там когда-то что-то будет.
        door = neighbour.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}
        mark = "" if door or not left_at(neighbour.index).empty else ", сейчас пусто"
        lines.append(
            f"Узел {neighbour.index}: {neighbour.name}, {NODE_DESCRIPTIONS[neighbour.kind]}{mark}."
        )

    # Who else is here comes after the way out and before the neighbours: it is
    # news, not navigation, and on a free location it is the news that matters.
    if others:
        lines.append("Здесь же:")
        lines.extend(f"{person.name}, уровень {person.level}." for person in others)
    elif pvp:
        lines.append("Здесь вольная земля: на этом узле могут напасть. Сейчас никого нет.")

    action = label(NODE_ACTIONS[node.kind])
    rows: list[tuple[Label, ...]] = [(action,)]
    if pvp:
        rows.extend((attack_label(person.name),) for person in others)
    rows.extend((node_button(neighbour),) for neighbour in neighbours)
    rows.append((LEAVE_LOCATION,))

    return Screen(
        id=ScreenId.LOCATION, lines=tuple(line for line in lines if line), rows=tuple(rows)
    )


def character_screen(
    content: GameContent, character: Character, stats: DerivedStats, notice: str = ""
) -> Screen:
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        *head(f"Персонаж {character.name}, уровень {character.level}.", notice),
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {character.health_or(stats.max_health)} из {stats.max_health}. "
        f"{stats.resource_name}: {stats.max_resource}.",
        f"Удар: {damage_line(content, character)}.",
        *derived_lines(stats),
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


def level_up_report(
    content: GameContent, character: Character, stats: DerivedStats, level_up: LevelUp
) -> str:
    """Всё, что принёс новый уровень, - отдельным сообщением.

    Единственное место, где одно действие отвечает двумя сообщениями
    (``docs/accessibility.md``, правило 3). Так и задумано: уровень тонул
    строкой между добычей и здоровьем после боя, и то, что открылось умение или
    город, игрок узнавал случайно, через сутки. Новость такого веса стоит
    своего сообщения; клавиатура при этом та же, что и на экране боя, так что
    игрок никуда не уходит.
    """
    previous = level_up.previous_level
    new = level_up.new_level
    lines = [
        f"Новый уровень: {new}."
        if level_up.levels_gained == 1
        else f"Взято уровней: {level_up.levels_gained}. Теперь {new}.",
        f"Очков характеристик: плюс {level_up.stat_points}, "
        f"нераспределено {character.unspent_stat_points}.",
        f"Очков умений: плюс {level_up.skill_points}, "
        f"нераспределено {character.unspent_skill_points}.",
        f"Здоровье: {stats.max_health}. {stats.resource_name}: {stats.max_resource}.",
    ]

    opened = [
        skill.name
        for skill in content.skills_of(f"class:{character.class_id}")
        if previous < skill.level <= new
    ]
    if opened:
        lines.append(f"Открылись умения: {', '.join(opened)}.")

    cities = [city.name for city in content.cities if previous < city.unlock_level <= new]
    if cities:
        lines.append(f"Открылся город: {', '.join(cities)}.")

    lines.append("Очки вкладываются в разделах «Характеристики» и «Умения».")
    return "\n".join(lines)


def derived_lines(stats: DerivedStats) -> tuple[str, ...]:
    """Всё, что движок считает сам, - целиком и одними и теми же словами.

    Карточка называла шесть значений из девяти: сила крита, восстановление
    ресурса и лечение по ходам считались в каждом бою и не были сказаны нигде.
    Число, которое движок считает, а экран молчит, ничем не лучше числа, которое
    экран обещает, а движок не считает (``Claude.md``, правило 7).
    """
    lines = [
        f"Броня: {stats.armor}. Точность: {number(stats.accuracy)}. "
        f"Уклонение: {percent(stats.dodge)}.",
        f"Шанс крита: {percent(stats.crit_chance)}. "
        f"Сила крита: {percent(stats.crit_damage)} от урона. "
        f"Инициатива: {number(stats.initiative)}.",
        f"{stats.resource_name} за ход: {number(stats.resource_regen)}.",
    ]
    if stats.health_regen_percent:
        lines.append(f"Здоровья за ход в бою: {percent(stats.health_regen_percent)} от максимума.")
    return tuple(lines)


def damage_line(content: GameContent, character: Character) -> str:
    """Что герой бьёт этим оружием — границами, и чем именно он бьёт.

    Границы, а не среднее: урон бросается по костям, и одно число обещало бы
    точность, которой нет. Голые руки называются голыми руками — по-другому
    игрок не поймёт, почему удар вдруг втрое меньше.
    """
    low, high = blow_range(content, character)
    weapon = gear.weapon_of(content, character)
    held = weapon.name.lower() if weapon is not None else "голыми руками"
    return f"от {low} до {high}, {held}"


def spend_label(stat_name: str) -> Label:
    return label(f"Вложить: {stat_name}")


def stat_effect_lines(content: GameContent, character: Character) -> tuple[str, ...]:
    """What one point in each stat actually does, in this character's numbers.

    Every number here is read from the rule constants rather than typed out, so a
    balance change cannot leave the explanation lying. What has no constant behind
    it is not written at all: the old list promised carried weight, resistances,
    loot rarity and stronger healing, and the engine counted none of the four.

    Two lines carry the whole answer to "куда вкладывать": what this class strikes
    from, and what its reserve is filled from. Both are read off the class, so a
    warrior and a mage get different sentences and neither gets the circular one
    that stood here before - "Сила: урон в бою, когда класс дерётся силой."
    """
    from mmorpg.domain.entities.stats import StatCode
    from mmorpg.domain.rules import economy
    from mmorpg.domain.rules import stats as stat_rules
    from mmorpg.presentation.telegram.screens.creation import STAT_GENITIVE, STAT_NAMES

    klass = content.character_class(character.class_id)
    key_codes = tuple(klass.key_stats)
    key_names = ", ".join(STAT_NAMES[StatCode(code)].lower() for code in key_codes)
    blow = StatCode(key_codes[0]) if key_codes else None
    pool = StatCode(klass.resource.stat)

    effects: dict[StatCode, str] = {
        StatCode.STR: "Сила: тяжесть удара в ближнем бою.",
        StatCode.AGI: (
            f"Ловкость: за очко плюс {number(stat_rules.ACCURACY_PER_AGILITY)} к точности, "
            f"{percent(stat_rules.DODGE_PER_AGILITY)} уклонения и "
            f"{number(stat_rules.INITIATIVE_PER_AGILITY)} инициативы — это ещё и очередь удара."
        ),
        StatCode.END: (
            f"Выносливость: за очко плюс {number(stat_rules.ARMOR_PER_ENDURANCE)} брони и "
            f"{number(klass.health.per_endurance)} здоровья."
        ),
        StatCode.INT: "Интеллект: сила чар.",
        StatCode.WIS: (
            f"Мудрость: за очко плюс {number(stat_rules.RESOURCE_REGEN_PER_WISDOM)} ресурса в ход."
        ),
        StatCode.CHA: (
            f"Харизма: за очко в лавке уступают "
            f"{percent(economy.CHARISMA_DISCOUNT_PER_POINT)}, "
            f"и так до {percent(economy.MAX_CHARISMA_DISCOUNT)}."
        ),
        StatCode.LCK: (
            f"Удача: за очко плюс {number(stat_rules.CRIT_CHANCE_PER_LUCK)} процента к шансу "
            f"крита и столько же к его силе; выше {percent(stat_rules.MAX_CRIT_CHANCE)} "
            "шанс не поднимется."
        ),
    }
    lead = [klass.power] if klass.power else []
    if blow is not None:
        lead.append(
            f"Ваш удар растёт от {STAT_GENITIVE[blow]}, "
            f"а {klass.resource.name.lower()} — от {STAT_GENITIVE[pool]}."
            if pool is not blow
            else f"От {STAT_GENITIVE[blow]} у вас и удар, и {klass.resource.name.lower()}."
        )
    if key_names:
        lead.append(f"Ключевые: {key_names}.")
    return (*lead, *(effects[code] for code in STAT_NAMES))


def stats_screen(
    content: GameContent,
    character: Character,
    stats: DerivedStats,
    notice: str = "",
    *,
    verbose: bool = True,
) -> Screen:
    """Every primary stat: what it is now, what it does, and where a point goes.

    ``verbose`` is the «Подробные описания» switch from the settings screen. It
    was stored, toggled and read back aloud for months while no screen in the game
    ever looked at it - a switch that answers nothing is the same bug as a button
    that does nothing (``Claude.md``, правило 9). Here it decides whether the seven
    explanations come with the seven numbers.
    """
    from mmorpg.domain.rules.stats import primary_stats
    from mmorpg.presentation.telegram.screens.creation import STAT_NAMES

    primary = primary_stats(content, character)
    lines = [
        *head(
            f"Характеристики. Свободных очков: {character.unspent_stat_points}.",
            notice,
        ),
        "Вложенное очко назад не берут.",
        # Числа целиком - на карточке персонажа: здесь их место заняли бы
        # объяснения, ради которых этот экран и открывают.
        f"Здоровье {stats.max_health}, броня {stats.armor}, "
        f"точность {number(stats.accuracy)}, уклонение {percent(stats.dodge)}.",
        *(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items()),
        *(
            stat_effect_lines(content, character)
            if verbose
            else (
                content.character_class(character.class_id).power,
                "Подробные описания выключены в настройках: что даёт очко, там же и включается.",
            )
        ),
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
