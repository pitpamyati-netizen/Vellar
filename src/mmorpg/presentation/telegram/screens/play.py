"""Screens for the game itself: menu, world, city, locations.

Each one opens with where the player is, then the detail, then what can be done -
in that order, because a player hears the message top to bottom and should be able
to stop listening as soon as they know enough (accessibility rule 4).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent
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
    NodeKind.EXIT: "Выйти через этот узел",
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
    return Screen(
        id=ScreenId.MAIN_MENU,
        lines=(
            notice or f"Главное меню. Вы в городе {city.name}.",
            f"{character.name}, уровень {character.level}, "
            f"{content.race(character.race_id).name}, "
            f"{content.character_class(character.class_id).name}.",
            f"Здоровье: {amount(stats.max_health, stats.max_health)}.",
            f"{stats.resource_name}: {amount(stats.max_resource, stats.max_resource)}.",
            progress,
            f"Золото: {gold(character.gold)}.",
        ),
        rows=(
            (labels.WORLD,),
            (labels.CHARACTER, labels.INVENTORY),
            (labels.SKILLS, labels.SETTINGS),
        ),
    )


def world_screen(
    content: GameContent, character: Character, state: PageState, notice: str = ""
) -> Screen:
    entries = [
        ListEntry(
            key=city.id,
            text=city.name,
            detail=f"уровни с {city.level_min} по {city.level_max}",
        )
        for city in content.cities_available_at(character.level)
    ]
    locked = len(content.cities) - len(entries)
    return paginated_screen(
        screen_id=ScreenId.WORLD,
        title="Мир",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Вы в городе {content.city(character.city_id).name}.",
            f"Закрыто городов: {locked}. Они откроются с уровнем.",
        ),
        empty_text="Пока не открыт ни один город.",
        show_filters=False,
    )


def city_screen(content: GameContent, city: City, character: Character, notice: str = "") -> Screen:
    return Screen(
        id=ScreenId.CITY,
        lines=(
            notice or f"Город {city.name}.",
            city.description,
            f"Уровни города: с {city.level_min} по {city.level_max}. "
            f"Ваш уровень: {character.level}.",
            "Доступно: лавка, локации, данжи, таверна, наставник, банк.",
        ),
        rows=(
            (labels.LOCATIONS,),
            (labels.SHOP, labels.DUNGEONS),
            (labels.TAVERN, labels.MENTOR),
            (labels.BANK,),
        ),
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
            detail=(
                f"уровни с {location.level_min} по {location.level_max}"
                + ("" if location.covers(character.level) else ", не по вашему уровню")
            ),
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
            "Локации перекрываются по уровням: всегда есть куда идти и куда вернуться.",
        ),
        show_filters=False,
    )


def location_screen(
    location: GeneratedLocation,
    node: LocationNode,
    *,
    cleared: int,
    notice: str = "",
) -> Screen:
    from mmorpg.domain.procgen.location import is_cleared

    done = is_cleared(cleared, node.index)
    neighbours = tuple(location.node(index) for index in node.links)

    lines = [
        notice or f"Локация {location.name}, узел {node.index}: {node.name}.",
        f"{NODE_DESCRIPTIONS[node.kind].capitalize()}. Уровень узла: {node.level}.",
        "Этот узел уже пройден." if done else "Узел ещё не пройден.",
        f"Отсюда можно перейти в {len(neighbours)} "
        + ("узел." if len(neighbours) == 1 else "узла." if len(neighbours) < 5 else "узлов."),
    ]
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
        f"Здоровье: {stats.max_health}. {stats.resource_name}: {stats.max_resource}.",
        f"Броня: {stats.armor}. Точность: {stats.accuracy}. Уклонение: {stats.dodge} процентов.",
        f"Шанс крита: {stats.crit_chance} процентов. Инициатива: {stats.initiative}.",
    ]
    lines.extend(f"{name}: {primary[code]}." for code, name in STAT_NAMES.items())
    if character.trait_ids:
        names = ", ".join(content.trait(trait_id).name for trait_id in character.trait_ids)
        lines.append(f"Особенности: {names}.")
    if character.unspent_stat_points or character.unspent_skill_points:
        lines.append(
            f"Нераспределено: очков характеристик {character.unspent_stat_points}, "
            f"очков умений {character.unspent_skill_points}."
        )
    return Screen(id=ScreenId.CHARACTER, lines=tuple(lines), rows=((labels.SKILLS,),))


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
