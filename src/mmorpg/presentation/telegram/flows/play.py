"""Перемещение и всякое действие вне боя как чистый автомат.

Форма та же, что у создания: ``advance(state, message) -> state``, без
ввода-вывода и без часов. То, как выглядит мир, приходит значениями - нынешняя
волна локации, переворот лавки, момент, на который судят сбор, - и это то, что
делает сборку воспроизводимой (``docs/procgen.md``).

Ветка не пишет ничего. Когда шаг должен изменить сохранённое - золото, набор
умений, задание, сумку, - он кладёт итог в :class:`PendingWrite`, а сохраняет
хендлер. Именно это и позволяет проверять всю игру без базы
(``docs/architecture.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from mmorpg import economy_log
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, Dungeon, GameContent, Item
from mmorpg.domain.entities.location import (
    Enemy,
    Engagement,
    GeneratedLocation,
    LocationNode,
    LocationState,
    NodeKind,
    Presence,
)
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.domain.procgen.location import generate_location
from mmorpg.domain.procgen.seeds import derive, location_seed, rng
from mmorpg.domain.rules import adventure, economy
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules import dungeon as dungeon_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import houses as house_rules
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import mood as mood_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.domain.rules import roamer as roamer_rules
from mmorpg.domain.rules import salvage as salvage_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import tools as tool_rules
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.presentation.telegram.flows import combat as fight_flow
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.state import (
    LIST_PAGE_FIELD,
    Clock,
    Descent,
    Goods,
    LocationSession,
    PendingWrite,
    PlayState,
    go_back,
    list_page,
    page_move,
    with_list_page,
)
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Command, Intent, resolve
from mmorpg.presentation.telegram.screens import arena as arena_screens
from mmorpg.presentation.telegram.screens import chamber as chamber_screens
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import crafts as craft_screens
from mmorpg.presentation.telegram.screens import dungeon as dungeon_screens
from mmorpg.presentation.telegram.screens import format as format_screens
from mmorpg.presentation.telegram.screens import guild as guild_screens
from mmorpg.presentation.telegram.screens import house as house_screens
from mmorpg.presentation.telegram.screens import items as item_screens
from mmorpg.presentation.telegram.screens import party as party_screens
from mmorpg.presentation.telegram.screens import play as screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import settings as settings_screens
from mmorpg.presentation.telegram.screens import shop as shop_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens import transfer as transfer_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.creation import STAT_NAMES
from mmorpg.presentation.telegram.screens.guild import GuildView
from mmorpg.presentation.telegram.screens.keeper import KeeperView
from mmorpg.presentation.telegram.screens.paginated import (
    SEARCH_PROMPT,
    PageState,
    filters_screen,
)
from mmorpg.presentation.telegram.screens.party import PartyView
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import NavigationStack

DEFAULT_SETTINGS = AccessibilitySettings()

# Экраны, состояние которых живёт не здесь: панель смотрителя и разговор с
# жителем города (``flows/keeper.py``).
KEEPER_SCREENS = keeper_flow.SCREENS

# Какая кнопка какую городскую службу открывает и под каким идентификатором город её
# объявляет.
SERVICES: dict[str, tuple[str, ScreenId]] = {
    labels.LOCATIONS.text: ("locations", ScreenId.LOCATION_LIST),
    labels.SHOP.text: ("shop", ScreenId.SHOP),
    labels.DUNGEONS.text: ("dungeons", ScreenId.DUNGEON),
    labels.ARENA.text: ("arena", ScreenId.ARENA),
    labels.CHAMBER.text: ("chamber", ScreenId.CHAMBER),
    labels.HOUSE.text: ("house", ScreenId.HOUSE),
    labels.TAVERN.text: ("tavern", ScreenId.TAVERN),
    labels.SUMMARY.text: ("summary", ScreenId.SUMMARY),
    labels.MENTOR.text: ("mentor", ScreenId.MENTOR),
    labels.BANK.text: ("bank", ScreenId.BANK),
    labels.FORGE.text: ("forge", ScreenId.FORGE),
}

# Говорится, когда экран называет локацию, которую игра больше не может собрать.
LOST_VISIT = "Та вылазка уже закончилась. Выберите локацию заново."


def begin(character: Character) -> PlayState:
    return PlayState(city_id=character.city_id)


def known_city(content: GameContent, city_id: str, fallback_id: str) -> City:
    """Город, который называет этот идентификатор, или тот, в котором стоит персонаж.

    Хранилище живёт дольше содержимого: идентификатор города, записанный прежним
    выпуском, сегодня может не называть ничего. Игрок получает свой город, а не
    падение (правило доступности 12).
    """
    for candidate in (city_id, fallback_id):
        if content.has_city(candidate):
            return content.city(candidate)
    return content.cities[0]


def _location_allows_pvp(content: GameContent, session: LocationSession) -> bool:
    """Одно ли это из вольных мест (``domain/rules/pvp.py``)."""
    if not location_known(content, session):
        return False
    return content.city(session.city_id).location(session.slot).pvp


def location_known(content: GameContent, session: LocationSession) -> bool:
    """Можно ли ещё собрать вылазку этой сессии из содержимого."""
    return (
        session.active
        and content.has_city(session.city_id)
        and content.city(session.city_id).has_location(session.slot)
    )


def build_location(
    content: GameContent,
    world_seed: str,
    session: LocationSession,
    *,
    epoch: int = 0,
) -> GeneratedLocation:
    """Собрать локацию, в которой стоит игрок, в её нынешнем поколении округи.

    ``epoch`` считается из общего состояния локации (``node_rules.location_epoch``):
    от него зависит вся раскладка - дерево троп, места узлов, их имена. От места
    не зависит только число узлов и набор категорий. О самой локации не хранится
    ничего.
    """
    city = content.city(session.city_id)
    location = city.location(session.slot)
    return generate_location(
        world_seed=world_seed,
        city_id=city.id,
        slot=location.slot,
        name=location.name,
        biome=location.biome,
        level_min=location.level_min,
        level_max=location.level_max,
        epoch=epoch,
    )


def visit_seed(world_seed: str, session: LocationSession) -> bytes:
    """Сид самого места. Карта не меняется, значит, не меняется и он."""
    return location_seed(world_seed, session.city_id, session.slot)


def node_standing(
    content: GameContent,
    world_seed: str,
    session: LocationSession,
    state: LocationState,
    now: int,
) -> dict[int, node_rules.Standing]:
    """Что осталось в каждом узле локации, по которой идут."""
    location = build_location(content, world_seed, session, epoch=node_rules.location_epoch(state))
    return node_rules.standing(visit_seed(world_seed, session), location, state, now)


def node_fight_seed(
    world_seed: str, session: LocationSession, wave: int, index: int | None = None
) -> bytes:
    """Сид боёв, стоящих в узле в его нынешней волне.

    Волна входит в него нарочно: стая, пришедшая после того, как пала прошлая, -
    это другая стая (``domain/rules/nodes.py``).
    """
    return derive(
        visit_seed(world_seed, session),
        "fight",
        session.node if index is None else index,
        wave,
    )


def node_pack_seed(
    world_seed: str, session: LocationSession, *, index: int, wave: int, place: int
) -> bytes:
    """Сид одной стаи - той, что стоит на этом месте волны (ADR 0065).

    Место, а не счёт взятого: стая с третьего места собирается одной и той же,
    сколько бы соседних до неё ни пало.
    """
    return derive(node_fight_seed(world_seed, session, wave, index), place)


def node_pack(
    content: GameContent,
    *,
    world_seed: str,
    session: LocationSession,
    location: GeneratedLocation,
    index: int,
    wave: int,
    place: int,
    state: LocationState,
) -> tuple[Enemy, ...]:
    """Стая, стоящая на этом месте волны. Та же, какую соберёт бой.

    Одна функция на экран и на бой: экран, назвавший другую стаю, обещал бы то,
    чего в узле нет (``Claude.md``, правило 7).
    """
    node = location.node(index)
    odds = dungeon_rules.affix_odds(node.kind.rank, mood_rules.mood_of(state))
    return fight_flow.spawn_for_node(
        content,
        seed=node_pack_seed(world_seed, session, index=index, wave=wave, place=place),
        biome=location.biome,
        level=max(1, node.level),
        rank=node.kind.rank,
        affix_chance=odds.chance,
        affix_count=odds.count,
    )


def node_watch(
    content: GameContent,
    *,
    world_seed: str,
    session: LocationSession,
    location: GeneratedLocation,
    index: int,
    standing: Mapping[int, node_rules.Standing],
    state: LocationState,
    tool: Item | None = None,
) -> str:
    """Что стоит в этом узле - названное словами (ADR 0063).

    Узел ничего не хранит, а стая, находка и её ступень - чистые функции от сида
    (``Claude.md``, правило 8), поэтому назвать их можно **до** нажатия и ровно
    теми же числами, какими их соберёт бой.

    Пусто - о таком узле сказать нечего: дверь, пустая волна, святилище.
    """
    node = location.node(index)
    left = standing.get(index)
    if left is None or left.empty or node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        return ""
    if node.kind.is_combat:
        return screens.pack_line(
            node_pack(
                content,
                world_seed=world_seed,
                session=session,
                location=location,
                index=index,
                wave=left.wave,
                place=left.free[0],
                state=state,
            )
        )
    if node.kind is NodeKind.GATHER:
        return _vein_line(content, location, node, tool)
    return ""


def _watched(
    content: GameContent,
    *,
    world_seed: str,
    session: LocationSession,
    location: GeneratedLocation,
    standing: Mapping[int, node_rules.Standing],
    state: LocationState,
    tool: Item | None = None,
) -> dict[int, str]:
    """Что стоит в этом узле и в тех, куда отсюда ведут тропы (ADR 0063).

    Дальше соседей экран не смотрит: узлов в локации до двадцати восьми, а
    собирать стаю для каждого - работа впустую.
    """
    here = location.node(session.node)
    return {
        index: line
        for index in (here.index, *here.links)
        if (
            line := node_watch(
                content,
                world_seed=world_seed,
                session=session,
                location=location,
                index=index,
                standing=standing,
                state=state,
                tool=tool,
            )
        )
    }


def node_foes(
    content: GameContent,
    *,
    world_seed: str,
    session: LocationSession,
    location: GeneratedLocation,
    index: int,
    standing: Mapping[int, node_rules.Standing],
    state: LocationState,
    fights: Sequence[Engagement] = (),
) -> tuple[screens.NodeFoe, ...]:
    """Стаи, стоящие в этом узле, каждая на своём месте волны (ADR 0065).

    Занятая - это та, за которую уже дерутся: её называют именем того, чей это
    бой, и зовут не напасть, а вмешаться. Всё остальное считается из сида, как и
    прежде: узел не хранит ни одной из этих стай.
    """
    node = location.node(index)
    left = standing.get(index)
    if left is None or left.empty or not node.kind.is_combat:
        return ()
    busy = {one.slot: one.name for one in fights if one.node == index and one.wave == left.wave}
    return tuple(
        screens.NodeFoe(
            place=place,
            line=screens.pack_line(
                node_pack(
                    content,
                    world_seed=world_seed,
                    session=session,
                    location=location,
                    index=index,
                    wave=left.wave,
                    place=place,
                    state=state,
                )
            ),
            level=max(1, node.level),
            fighter=busy.get(place, ""),
        )
        for place in left.free
    )


#: Сколько находок жила называет на экране. Безымянная жила отдаёт то, что берёт
#: инструмент, и без инструмента их набирается по одной на каждое ремесло: пять
#: имён в кнопке - это не подсказка, а список.
VEIN_NAMES = 3


def _vein_line(
    content: GameContent, location: GeneratedLocation, node: LocationNode, tool: Item | None = None
) -> str:
    """Что лежит в этой жиле: род сырья и та его ступень, что берут на этой глубине.

    Считается ровно так же, как считает сам сбор (``adventure._gather``): жила,
    назвавшая своё сырьё, стоит на своём, а безымянная отдаёт то, что берёт
    инструмент в руках.
    """
    wanted = adventure.GATHER_SOURCES.get(node.name, "")
    sources = (wanted,) if wanted else (tool_rules.sources_of(content, tool) if tool else ())
    here = craft_rules.best_per_source(
        content,
        craft_rules.yields_here(
            content, level=node.level, biomes=frozenset({location.biome}), sources=sources
        ),
    )
    if not here:
        here = craft_rules.best_per_source(
            content, craft_rules.yields_here(content, level=node.level, sources=sources)
        )
    named = ", ".join(content.item(item_id).name for item_id in here[:VEIN_NAMES])
    return f"{named} и другое" if len(here) > VEIN_NAMES else named


def dungeon_run_seed(world_seed: str, descent: Descent) -> bytes:
    """Сид всего захода: из него растут и развилки, и условия (ADR 0036).

    У блуждающего подземелья (ADR 0037) сид привязан к его личности - месту, слоту
    и окну появления, - а не к городскому входу.
    """
    if descent.roamer:
        return roamer_rules.run_seed(
            world_seed,
            descent.city_id,
            descent.slot,
            descent.stamp,
            dungeon_rules.difficulty_of(descent.difficulty),
        )
    return dungeon_rules.run_seed(
        world_seed,
        descent.city_id,
        descent.dungeon_id,
        dungeon_rules.difficulty_of(descent.difficulty),
        descent.started_at,
    )


def descent_fight_seed(world_seed: str, descent: Descent) -> bytes:
    """Сид боя в комнате нынешнего слоя."""
    return derive(dungeon_run_seed(world_seed, descent), "room", descent.layer)


def dungeon_open(city: City, character: Character, dungeon: Dungeon) -> bool:
    """Открыт ли этот спуск игроку (ADR 0041).

    У глубокого спуска порог - не его уровень, а самая глубокая локация города
    (ADR 0019). Обычный спуск открывает своё ``unlock_level``; у большинства это
    ноль, то есть он открыт вместе с городом.
    """
    return dungeon_rules.dungeon_unlocked(
        deep=dungeon.deep,
        unlock_level=dungeon.unlock_level,
        char_level=character.level,
        deep_threshold=city.locations[-1].level_min,
    )


def open_dungeons(city: City, character: Character) -> tuple[Dungeon, ...]:
    """Подземелья города, открытые этому игроку."""
    return tuple(one for one in city.dungeons if dungeon_open(city, character, one))


# --- отрисовка --------------------------------------------------------


def render(
    content: GameContent,
    character: Character,
    state: PlayState,
    *,
    world_seed: str,
    goods: Goods | None = None,
    settings: AccessibilitySettings | None = None,
    clock: Clock | None = None,
    neighbours: Sequence[Presence] = (),
    fights: Sequence[Engagement] = (),
    arena_table: Sequence[Character] = (),
    tally: Mapping[str, int] | None = None,
    keeper: KeeperView | None = None,
    party: PartyView | None = None,
    guild: GuildView | None = None,
    location_state: LocationState | None = None,
    digest_view: city_screens.DigestView | None = None,
) -> Screen:
    screen = _render(
        content,
        character,
        state,
        world_seed=world_seed,
        goods=goods,
        settings=settings,
        clock=clock,
        neighbours=neighbours,
        fights=fights,
        arena_table=arena_table,
        tally=tally,
        keeper=keeper,
        party=party,
        guild=guild,
        location_state=location_state,
        digest_view=digest_view,
    )
    # Подсказка незакрытого шага обучения — строка в теле экрана, не весть
    # (правило доступности 4). Свой notice экрана её не трогает (ADR 0038). На
    # переполненную страницу списка подсказку не клеят: там уже нет места.
    hint = tutorial_screens.hint_line(state.screen, character)
    if hint and hint not in screen.lines:
        with_hint = replace(screen, lines=(*screen.lines, hint))
        if with_hint.fits_message_limit():
            return with_hint
    return screen


def _render(
    content: GameContent,
    character: Character,
    state: PlayState,
    *,
    world_seed: str,
    goods: Goods | None = None,
    settings: AccessibilitySettings | None = None,
    clock: Clock | None = None,
    neighbours: Sequence[Presence] = (),
    fights: Sequence[Engagement] = (),
    arena_table: Sequence[Character] = (),
    tally: Mapping[str, int] | None = None,
    keeper: KeeperView | None = None,
    party: PartyView | None = None,
    guild: GuildView | None = None,
    location_state: LocationState | None = None,
    digest_view: city_screens.DigestView | None = None,
) -> Screen:
    shelf = goods or Goods(gold=character.gold)
    clock = clock or Clock()
    here_now = location_state or LocationState()
    city = known_city(content, state.city_id, character.city_id)
    # Панель смотрителя и разговор с жителем рисуются своим модулем: это не
    # исключение из правил экрана, а просто другая половина того же автомата.
    if state.screen in KEEPER_SCREENS:
        return keeper_flow.render(content, character, state, keeper or KeeperView())
    match state.screen:
        case ScreenId.PARTY:
            return party_screens.party_screen(party or PartyView(), state.notice)
        case ScreenId.PARTY_INVITE:
            return party_screens.invite_screen(party or PartyView(), state.notice)
        case ScreenId.GUILD:
            return guild_screens.guild_screen(guild or GuildView(), state.notice)
        case ScreenId.GUILD_FOUND:
            return guild_screens.found_screen(guild or GuildView(), state.notice)
        case ScreenId.GUILD_INVITE:
            return guild_screens.invite_screen(guild or GuildView(), state.notice)
        case ScreenId.GUILD_ROSTER:
            return guild_screens.roster_screen(guild or GuildView(), state.list_page, state.notice)
        case ScreenId.GUILD_VAULT:
            return guild_screens.vault_screen(guild or GuildView(), state.notice)
        case ScreenId.TRANSFER_TO:
            return transfer_screens.recipients_screen(
                state.transfer_scope,
                _transfer_recipients(state, character, party, guild),
                state.list_page,
                state.notice,
            )
        case ScreenId.TRANSFER_ITEM:
            return transfer_screens.bag_screen(
                content, shelf.owned, state.transfer_to, state.list_page, state.notice
            )
        case ScreenId.TRANSFER_AMOUNT if content.has_item(state.transfer_item):
            return transfer_screens.amount_screen(
                content.item(state.transfer_item),
                _owned_count(shelf, state.transfer_item),
                state.transfer_to,
                state.notice,
            )
        # Вещь пропала из содержимого или из сумки: игрока возвращают к выбору
        # вещи, а не к падению (``Claude.md``, правило 8).
        case ScreenId.TRANSFER_AMOUNT:
            return transfer_screens.bag_screen(
                content, shelf.owned, state.transfer_to, state.list_page, state.notice
            )
        case ScreenId.SETTINGS:
            return settings_screens.settings_screen(settings or DEFAULT_SETTINGS, state.notice)
        case ScreenId.LIST_FILTERS:
            target = _filtered_screen(state)
            return filters_screen(
                screen_id=ScreenId.LIST_FILTERS,
                title="Разделы списка",
                categories=list_sections(content, target),
                current=_page_for(state, target).filters,
            )
        case ScreenId.INVENTORY:
            return shop_screens.inventory_screen(
                content, shelf.owned, state.list_page, gold=shelf.gold, notice=state.notice
            )
        case ScreenId.ITEM if content.has_item(state.item_id):
            return item_screens.item_screen(
                content,
                character,
                content.item(state.item_id),
                quantity=_owned_count(shelf, state.item_id),
                sale=economy.sell_price(content, content.item(state.item_id)),
                notice=state.notice,
            )
        # Вещи с таким ключом больше нет: правка смотрителя или новая выкатка.
        # Игрок возвращается в сумку, а не встречает ошибку (правило 12).
        case ScreenId.ITEM:
            return shop_screens.inventory_screen(
                content, shelf.owned, state.list_page, gold=shelf.gold, notice=state.notice
            )
        case ScreenId.SHOP_ITEM if content.has_item(state.item_id):
            item = content.item(state.item_id)
            return item_screens.shop_item_screen(
                content,
                character,
                item,
                price=shelf.prices.get(item.id, item.price),
                gold=shelf.gold,
                notice=state.notice,
            )
        case ScreenId.SHOP | ScreenId.SHOP_ITEM:
            return shop_screens.shop_screen(
                content,
                shelf.stock,
                dict(shelf.prices),
                state.list_page,
                gold=shelf.gold,
                city_name=city.name,
                notice=state.notice,
            )
        case ScreenId.SELL:
            return shop_screens.sell_screen(
                content,
                shelf.owned,
                _sale_prices(content, shelf.owned),
                state.list_page,
                gold=shelf.gold,
                city_name=city.name,
                notice=state.notice,
            )
        case ScreenId.WORLD:
            return screens.world_screen(content, character, state.world_page, state.notice)
        case ScreenId.CITY:
            return screens.city_screen(content, city, character, state.notice)
        case ScreenId.LOCATION_LIST:
            return screens.location_list_screen(
                content, city, character, state.location_page, state.notice
            )
        case ScreenId.LOCATION if location_known(content, state.session):
            location = build_location(
                content, world_seed, state.session, epoch=node_rules.location_epoch(here_now)
            )
            standing_here = location.node(state.session.node)
            counted = node_standing(content, world_seed, state.session, here_now, clock.now)
            return screens.location_screen(
                location,
                standing_here,
                standing=counted,
                watch=_watched(
                    content,
                    world_seed=world_seed,
                    session=state.session,
                    location=location,
                    standing=counted,
                    state=here_now,
                    tool=tool_rules.tool_of(content, character),
                ),
                foes=node_foes(
                    content,
                    world_seed=world_seed,
                    session=state.session,
                    location=location,
                    index=state.session.node,
                    standing=counted,
                    state=here_now,
                    fights=fights,
                ),
                character_level=character.level,
                others=neighbours,
                pvp=_location_allows_pvp(content, state.session),
                roamer=here_now.roamer,
                mood=mood_rules.mood_of(here_now),
                tool_note=craft_screens.tool_line(
                    content, character, adventure.GATHER_SOURCES.get(standing_here.name, "")
                ),
                notice=state.notice,
            )
        # Локации, которой в содержимом больше нет, игрок не видит: его возвращают к
        # списку, из которого он вошёл, а не оставляют с падением (правило 12).
        case ScreenId.LOCATION:
            return screens.location_list_screen(
                content, city, character, state.location_page, state.notice or LOST_VISIT
            )
        case ScreenId.CHARACTER:
            return screens.character_screen(
                content, character, derived_stats(content, character), state.notice
            )
        case ScreenId.STATS:
            return screens.stats_screen(
                content,
                character,
                derived_stats(content, character),
                state.notice,
                verbose=(settings or DEFAULT_SETTINGS).verbose,
            )
        case ScreenId.TUTORIAL:
            return tutorial_screens.tutorial_screen(character, state.notice)
        case ScreenId.ARENA:
            return arena_screens.arena_screen(character, arena_table, state.notice)
        case ScreenId.CHAMBER:
            return chamber_screens.chamber_screen(content, character, state.notice)
        case ScreenId.TURNING:
            return chamber_screens.turning_screen(
                content, character, tally=tally or {}, notice=state.notice
            )
        case ScreenId.CHAMBER_REMORT:
            return chamber_screens.remort_screen(content, character, state.notice)
        case ScreenId.HOUSE:
            return house_screens.house_screen(content, character, city, state.notice)
        case ScreenId.SKILLS:
            return skill_screens.skills_screen(content, character, state.skill_page, state.notice)
        case ScreenId.SKILL_SLOTS:
            return skill_screens.slots_screen(content, character, state.notice)
        case ScreenId.SKILL_PICK:
            return skill_screens.pick_screen(
                content,
                character,
                state.pick_slot,
                state.list_page,
                state.notice,
            )
        case ScreenId.SKILL_EDGE if content.has_skill(state.edge_skill):
            return skill_screens.edge_screen(content, character, content.skill(state.edge_skill))
        case ScreenId.SKILL_EDGE:
            return skill_screens.skills_screen(content, character, state.skill_page, state.notice)
        case ScreenId.CRAFT if content.has_craft(state.craft_id):
            here = known_city(content, state.city_id, character.city_id)
            return craft_screens.craft_screen(
                content,
                character,
                content.craft(state.craft_id),
                _bag(shelf),
                page=state.list_page,
                biomes=here.biomes,
                place=here.name,
                notice=state.notice,
            )
        # Ремесло, которого в содержимом больше нет, - не ошибка: игрока возвращают к
        # списку, из которого он пришёл (правило доступности 12).
        case ScreenId.CRAFTS | ScreenId.CRAFT:
            return craft_screens.crafts_screen(content, character, state.notice)
        case ScreenId.QUESTS:
            return quest_screens.journal_screen(content, character, state.notice)
        case ScreenId.QUEST_BOARD:
            return quest_screens.board_screen(
                content, character, state.board_page, state.notice, city_id=city.id
            )
        case ScreenId.QUEST_OFFER if content.has_quest(state.quest_id):
            return quest_screens.offer_screen(
                content, content.quest(state.quest_id), character, state.notice
            )
        case ScreenId.QUEST_OFFER:
            return quest_screens.board_screen(
                content, character, state.board_page, state.notice, city_id=city.id
            )
        case ScreenId.TAVERN:
            return city_screens.tavern_screen(content, character, city, state.notice)
        case ScreenId.SUMMARY:
            view = digest_view or city_screens.DigestView()
            return city_screens.summary_screen(
                content,
                city,
                character,
                digest_rules.digest(
                    content,
                    world_seed,
                    city.id,
                    clock.shop_rotation,
                    character.level,
                    moods=view.moods,
                ),
                claimed=view.claimed,
                roamer_place=view.roamer_place,
                notice=state.notice,
            )
        case ScreenId.MENTOR:
            return city_screens.mentor_screen(
                content, character, city, state.mentor_page, state.notice
            )
        case ScreenId.BANK:
            return city_screens.bank_screen(content, character, city, state.notice)
        case ScreenId.FORGE:
            return city_screens.forge_screen(content, character, city, state.notice)
        case ScreenId.SALVAGE:
            return city_screens.salvage_screen(
                content,
                character,
                shelf.owned,
                state.list_page,
                city_name=city.name,
                notice=state.notice,
            )
        case ScreenId.REFORGE:
            return city_screens.reforge_screen(
                content,
                character,
                shelf.owned,
                state.list_page,
                city_name=city.name,
                notice=state.notice,
            )
        case ScreenId.DUNGEON:
            return city_screens.dungeon_list_screen(
                content,
                character,
                city,
                page=state.list_page,
                base_depth=dungeon_rules.DESCENT_DEPTH,
                notice=state.notice,
            )
        case ScreenId.DUNGEON_PICK:
            base_depth = dungeon_rules.DESCENT_DEPTH
            if not city.has_dungeon(state.dungeon_pick):
                return city_screens.dungeon_list_screen(
                    content,
                    character,
                    city,
                    page=state.list_page,
                    base_depth=base_depth,
                    notice="Это подземелье пропало. Выберите заново.",
                )
            return city_screens.dungeon_pick_screen(
                content,
                character,
                city,
                city.dungeon(state.dungeon_pick),
                base_depth=base_depth,
                notice=state.notice,
            )
        case _:
            return screens.main_menu_screen(
                content, character, derived_stats(content, character), state.notice
            )


def list_sections(content: GameContent, screen: ScreenId) -> tuple[str, ...]:
    """По каким разделам режется список на этом экране. Пусто - не режется."""
    match screen:
        case ScreenId.INVENTORY | ScreenId.SHOP | ScreenId.SELL | ScreenId.TRANSFER_ITEM:
            return shop_screens.ITEM_SECTIONS
        case ScreenId.SKILLS:
            return skill_screens.SKILL_SECTIONS
        case ScreenId.CRAFT:
            return craft_screens.CRAFT_SECTIONS
        case _:
            return ()


def _search_and_filters(
    content: GameContent, state: PlayState, command: Command
) -> PlayState | None:
    """Поиск, разделы и сброс - одинаково на всех списках. ``None`` - не про них.

    Разбор один и живёт здесь: кнопка, которую никто не разбирает, - это баг
    (``Claude.md``, правило 9).
    """
    if command.intent is Intent.SEARCH:
        return replace(state, searching=True).with_notice(SEARCH_PROMPT)
    if command.intent is Intent.RESET_FILTERS:
        cleared = list_page(state).filters.cleared()
        return with_list_page(state, PageState(filters=cleared)).with_notice(
            "Фильтры сняты, список показан целиком."
        )
    if command.intent is Intent.FILTERS:
        if not list_sections(content, state.screen):
            return state.with_notice("Этот список не делится на разделы. Есть поиск.")
        return state.at(ScreenId.LIST_FILTERS)
    return None


def _searched(state: PlayState, text: str) -> PlayState:
    """Строка поиска, набранная сообщением. Пустая строка снимает поиск."""
    query = text.strip()
    filters = replace(list_page(state).filters, query=query)
    found = with_list_page(replace(state, searching=False), PageState(filters=filters))
    if not query:
        return found.with_notice("Поиск снят, список показан целиком.")
    return found.with_notice(f"Ищу «{query}».")


def _handle_list_filters(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Разделы списка: одна кнопка - один раздел, и обратно к списку."""
    if command.intent is Intent.RESET_FILTERS:
        back = go_back(replace(state, searching=False))
        cleared = list_page(back).filters.cleared()
        return with_list_page(back, PageState(filters=cleared)).with_notice(
            "Фильтры сняты, список показан целиком."
        )
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите раздел или «Назад».")
    for name in list_sections(content, _filtered_screen(state)):
        if name != command.argument:
            continue
        back = go_back(state)
        chosen = replace(list_page(back).filters, category=name)
        return with_list_page(back, PageState(filters=chosen)).with_notice(f"Раздел «{name}».")
    return state.with_notice("Нажмите раздел или «Назад».")


def _filtered_screen(state: PlayState) -> ScreenId:
    """Экран со списком, ради которого открыли разделы."""
    _, previous = state.stack.pop()
    return previous or ScreenId.INVENTORY


def _page_for(state: PlayState, screen: ScreenId) -> PageState:
    return list_page(replace(state, screen=screen))


def _owned_count(goods: Goods, item_id: str) -> int:
    return next((held.quantity for held in goods.owned if held.item_id == item_id), 0)


def _transfer_recipients(
    state: PlayState,
    character: Character,
    party: PartyView | None,
    guild: GuildView | None,
) -> tuple[str, ...]:
    """Кому игрок может передать вещь: состав объединения минус он сам."""
    if state.transfer_scope == "guild":
        names: tuple[str, ...] = tuple(name for name, _ in guild.members) if guild else ()
    else:
        names = tuple(party.members) if party else ()
    return tuple(name for name in names if name != character.name)


def _sale_prices(content: GameContent, owned: tuple[OwnedItem, ...]) -> dict[str, int]:
    return {held.item_id: economy.sell_price(content, content.item(held.item_id)) for held in owned}


# --- шаги -------------------------------------------------------------


def advance(
    content: GameContent,
    character: Character,
    state: PlayState,
    text: str,
    *,
    world_seed: str,
    clock: Clock | None = None,
    goods: Goods | None = None,
    settings: AccessibilitySettings | None = None,
    neighbours: Sequence[Presence] = (),
    fights: Sequence[Engagement] = (),
    keeper: KeeperView | None = None,
    party: PartyView | None = None,
    guild: GuildView | None = None,
    location_state: LocationState | None = None,
) -> PlayState:
    """Применить одно сообщение. Отвечает всегда; на неожиданный ввод не падает."""
    # Вылазку, которую больше не собрать, выбрасывают до того, как её кто-нибудь
    # прочитает, поэтому обратно записывается работающее состояние: лечение в одном
    # лишь ``render`` оставило бы сохранённый экран сломанным навсегда.
    if state.screen is ScreenId.LOCATION and not location_known(content, state.session):
        # Мёртвый экран уходит и из стопки, поэтому «Назад» шагает дальше, в город, а не
        # обратно в место, которого больше нет.
        walked, _ = (
            state.stack.pop()
            if state.stack.current is ScreenId.LOCATION
            else (
                state.stack,
                None,
            )
        )
        healed = (
            replace(
                state,
                session=LocationSession(),
                stack=walked,
                pending=PendingWrite(),
                fight="",
            )
            .at(ScreenId.LOCATION_LIST)
            .with_notice(LOST_VISIT)
        )
        # Кнопки, бывшие на экране, принадлежат месту, которого больше нет, поэтому это
        # нажатие только объясняет и выдаёт работающую клавиатуру (правило 12) - кроме
        # служебного ряда, который везде значит то, что на нём написано.
        intent = resolve(text, render(content, character, healed, world_seed=world_seed)).intent
        if intent is Intent.MAIN_MENU:
            return replace(
                healed,
                screen=ScreenId.MAIN_MENU,
                stack=NavigationStack((ScreenId.MAIN_MENU,)),
                notice="",
            )
        if intent is Intent.BACK:
            return go_back(healed)
        return healed

    view = keeper or KeeperView()
    screen = render(
        content,
        character,
        state,
        world_seed=world_seed,
        goods=goods,
        settings=settings,
        clock=clock,
        neighbours=neighbours,
        fights=fights,
        keeper=view,
        party=party,
        guild=guild,
        location_state=location_state,
    )
    command = resolve(text, screen)

    # Набранное значение на экране поля - это не «неизвестная кнопка», а ответ на
    # заданный вопрос, и разобрать его может только та ветка, что его задавала.
    if keeper_flow.awaits_text(state, command):
        return keeper_flow.typed(content, character, state, text.strip(), view)

    # То же самое для списков: набранное после «Поиска» - это строка поиска, а
    # не незнакомая кнопка.
    if state.searching and command.intent is Intent.UNKNOWN:
        return _searched(replace(state, pending=PendingWrite(), fight=""), text)

    if command.intent is Intent.LOOK:
        return replace(state, notice="", pending=PendingWrite(), fight="")
    if command.intent is Intent.MAIN_MENU:
        return replace(
            state,
            screen=ScreenId.MAIN_MENU,
            stack=NavigationStack((ScreenId.MAIN_MENU,)),
            notice="",
            pending=PendingWrite(),
            fight="",
            searching=False,
        )
    if command.intent is Intent.BACK:
        return go_back(state)

    # Страницы считает сам экран, и только он: сколько записей влезло в одно
    # сообщение, зависит от их длины (``screens/paginated.py``). Второй счёт по
    # числу записей расходился бы с первым, и «Следующая страница» переставала бы
    # работать на середине списка.
    if state.screen in LIST_PAGE_FIELD:
        turned = page_move(command, list_page(state), int(screen.metadata.get("pages", 1)))
        if turned is not None:
            return with_list_page(state, turned).with_notice("")

    state = replace(
        state,
        pending=PendingWrite(),
        fight="",
        invite=0,
        invite_name="",
        party_action="",
        guild_action="",
        guild_arg="",
        transfer_amount=0,
    )
    shelf = goods or Goods(gold=character.gold)
    ticking = clock or Clock()

    # Отряд отвечает откуда угодно: зов приходит туда, где игрок сейчас стоит
    # (``domain/rules/party.py``). Но не с панели смотрителя: там «Расформировать
    # отряд» и «Распустить гильдию» правят чужое объединение, а не своё.
    on_panel = state.screen in keeper_flow.PANEL
    if not on_panel and (with_party := _party_intent(state, command)) is not None:
        return with_party

    if not on_panel and (with_guild := _guild_intent(state, command)) is not None:
        return with_guild

    if (with_transfer := _transfer_intent(state, command)) is not None:
        return with_transfer

    if state.screen in KEEPER_SCREENS:
        return keeper_flow.advance(content, character, state, command, view)

    match state.screen:
        case ScreenId.LIST_FILTERS:
            return _handle_list_filters(content, character, state, command)
        case ScreenId.SETTINGS:
            return _handle_settings(state, command, settings or DEFAULT_SETTINGS)
        case ScreenId.TUTORIAL:
            return _handle_tutorial(content, character, state, command)
        case ScreenId.ARENA:
            return _handle_arena(character, state, command)
        case ScreenId.CHAMBER:
            return _handle_chamber(content, character, state, command)
        case ScreenId.HOUSE:
            return _handle_house(content, character, state, command)
        case ScreenId.TURNING:
            return _handle_turning(content, character, state, command)
        case ScreenId.CHAMBER_REMORT:
            return _handle_remort(content, character, state, command)
        case ScreenId.INVENTORY:
            return _handle_inventory(content, character, state, command, shelf)
        case ScreenId.ITEM:
            return _handle_item(content, character, state, command, shelf)
        case ScreenId.SHOP:
            return _handle_shop(content, character, state, command, shelf)
        case ScreenId.SHOP_ITEM:
            return _handle_shop_item(content, character, state, command, shelf)
        case ScreenId.SELL:
            return _handle_sell(content, character, state, command, shelf)
        case ScreenId.MAIN_MENU:
            return _handle_main_menu(content, character, state, command, clock=ticking)
        case ScreenId.WORLD:
            return _handle_world(content, character, state, command)
        case ScreenId.CITY:
            return _handle_city(content, character, state, command)
        case ScreenId.CHARACTER:
            return _handle_character(content, character, state, command)
        case ScreenId.STATS:
            return _handle_stats(character, state, command)
        case ScreenId.SKILLS:
            return _handle_skills(content, character, state, command)
        case ScreenId.SKILL_SLOTS:
            return _handle_slots(content, character, state, command)
        case ScreenId.SKILL_PICK:
            return _handle_pick(content, character, state, command)
        case ScreenId.SKILL_EDGE:
            return _handle_edge(content, character, state, command)
        case ScreenId.TAVERN:
            return _handle_tavern(content, character, state, command)
        case ScreenId.SUMMARY:
            return _handle_summary(state, command)
        case ScreenId.CRAFTS:
            return _handle_crafts(content, character, state, command, clock=ticking)
        case ScreenId.CRAFT:
            return _handle_craft(
                content, character, state, command, shelf, clock=ticking, world_seed=world_seed
            )
        case ScreenId.QUEST_BOARD:
            return _handle_board(content, character, state, command)
        case ScreenId.QUEST_OFFER:
            return _handle_offer(content, character, state, command)
        case ScreenId.MENTOR:
            return _handle_mentor(content, character, state, command)
        case ScreenId.BANK:
            return _handle_bank(content, character, state, command)
        case ScreenId.FORGE:
            return _handle_forge(content, character, state, command)
        case ScreenId.SALVAGE:
            return _handle_salvage(content, character, state, command, shelf)
        case ScreenId.REFORGE:
            return _handle_reforge(
                content, character, state, command, shelf, clock=ticking, world_seed=world_seed
            )
        case ScreenId.DUNGEON:
            return _handle_dungeon(content, character, state, command)
        case ScreenId.DUNGEON_PICK:
            return _handle_dungeon_pick(content, character, state, command, clock=ticking)
        case ScreenId.PARTY:
            return state.with_notice("Нажмите кнопку отряда.")
        case ScreenId.PARTY_INVITE:
            return _handle_party_invite(state, command, text)
        case ScreenId.GUILD:
            return state.with_notice("Нажмите кнопку гильдии.")
        case ScreenId.GUILD_FOUND:
            return _handle_guild_text(state, command, text, action="found")
        case ScreenId.GUILD_INVITE:
            return _handle_guild_text(state, command, text, action="invite")
        case ScreenId.GUILD_ROSTER:
            return _handle_guild_roster(state, command)
        case ScreenId.GUILD_VAULT:
            return _handle_guild_vault(state, command)
        case ScreenId.TRANSFER_TO:
            return _handle_transfer_to(
                state, command, _transfer_recipients(state, character, party, guild)
            )
        case ScreenId.TRANSFER_ITEM:
            return _handle_transfer_item(content, state, command, shelf)
        case ScreenId.TRANSFER_AMOUNT:
            return _handle_transfer_amount(content, state, command, text, shelf)
        case ScreenId.LOCATION_LIST:
            return _handle_location_list(content, character, state, command)
        case ScreenId.LOCATION:
            return _handle_location(
                content,
                character,
                state,
                command,
                world_seed=world_seed,
                neighbours=neighbours,
                fights=fights,
                location_state=location_state or LocationState(),
                now=ticking.now,
            )
        case _:
            return state.with_notice("Нажмите «Назад» или «Главное меню».")


def mark_task(state: PlayState, character: Character, task: TutorialTask) -> PlayState:
    """Отметить дело вступления на том шаге, который его и закончил.

    Шаг может уже сохранять изменённого персонажа - покупку, задание, - и тогда
    отметка ложится на него; иначе на того, каким его загрузили. Уже сделанное
    дело ничего не меняет и ничего не говорит.
    """
    base = state.pending.character or character
    marked = tutorial_rules.complete(base, task)
    if marked is None:
        return state
    line = tutorial_screens.completion_line(task, marked)
    return replace(
        state,
        pending=replace(state.pending, character=marked),
        notice=f"{state.notice} {line}".strip() if state.notice else line,
    )


def _handle_arena(character: Character, state: PlayState, command: Command) -> PlayState:
    """Одна кнопка, и она стоит денег: ставку берут до боя."""
    if command.intent is not Intent.SELECT or not arena_screens.ARENA_FIGHT.matches(
        command.argument
    ):
        return state.with_notice("Нажмите «Выйти на арену» или «Назад».")
    refused = arena_rules.refusal(character)
    if refused:
        return state.with_notice(refused)
    # Противника выбирает и ставку берёт хендлер: только он может прочитать из хранилища
    # другого персонажа.
    return replace(state, fight="arena").at(ScreenId.COMBAT)


def _handle_chamber(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Управа: две двери — новое имя и голосование."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из списка или «Назад».")
    if labels.TURNING.matches(command.argument):
        refused = turning_rules.refusal(character)
        if refused:
            return state.with_notice(refused)
        return state.at(ScreenId.CHAMBER_REMORT)
    if labels.TURNING_QUESTION.matches(command.argument):
        return state.at(ScreenId.TURNING)
    return state.with_notice("Нажмите кнопку из списка или «Назад».")


def _handle_house(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Двор дома: вступить за взнос или уйти бесплатно."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из списка или «Назад».")
    city = known_city(content, state.city_id, character.city_id)
    if labels.HOUSE_JOIN.matches(command.argument):
        joined = house_rules.join(content, character, city.id)
        if joined is None:
            return state.with_notice(house_rules.join_refusal(content, character, city.id))
        house = house_rules.house_of_city(content, city.id)
        assert house is not None
        fee = format_screens.gold(house_rules.JOIN_FEE)
        return state.storing(
            PendingWrite(character=joined).because(economy_log.SERVICE)
        ).with_notice(
            f"Вы вступили в дом: {house.name}. Взнос {fee} ушёл. "
            f"Техника «{house.technique.name}» при вас."
        )
    if labels.HOUSE_LEAVE.matches(command.argument):
        left = house_rules.leave(character)
        if left is None:
            return state.with_notice("Вы ни в каком доме не состоите.")
        return state.storing(PendingWrite(character=left)).with_notice(
            "Вы ушли из дома. Техника закрылась."
        )
    return state.with_notice("Нажмите кнопку из списка или «Назад».")


def _handle_turning(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Голосование: по кнопке на ответ, и голос весит столько, сколько уходов."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите ответ или «Назад».")
    turning = content.open_turning()
    if turning is None:
        return state.with_notice("Совет сейчас ни о чём не спрашивает.")
    for option in turning.options:
        if not chamber_screens.answer_label(option.name).matches(command.argument):
            continue
        voted = turning_rules.answer(character, turning, option.id)
        if voted is None:
            if not turning_rules.may_answer(character):
                return state.with_notice("Голос дают за уход: сперва новое имя, потом ответ.")
            return state.with_notice(f"Ваш голос уже отдан за: {option.name}.")
        weight = turning_rules.voice(voted)
        return state.storing(PendingWrite(character=voted)).with_notice(
            f"Голос отдан за: {option.name}. Он весит {weight} "
            f"{format_screens.plural(weight, 'уход', 'ухода', 'уходов')}."
        )
    return state.with_notice("Нажмите ответ или «Назад».")


def _handle_remort(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Новое имя. Нажатие здесь сбрасывает уровень до первого: экран предупреждал."""
    if command.intent is not Intent.SELECT or not labels.CONFIRM.matches(command.argument):
        return state.with_notice("Нажмите «Подтвердить» или «Назад».")
    reborn = turning_rules.become(character)
    if reborn is None:
        return state.at(ScreenId.CHAMBER).with_notice(turning_rules.refusal(character))
    return (
        state.storing(PendingWrite(character=reborn.character))
        .at(ScreenId.CHAMBER)
        .with_notice(chamber_screens.reborn_line(reborn))
    )


def _walk_to_tutorial_step(
    content: GameContent, character: Character, state: PlayState
) -> PlayState:
    """Дойти до экрана, на котором делается нынешний шаг обучения.

    Дорогу до городской службы проходят за игрока, а не описывают ему. На самом
    экране шага висит подсказка (``tutorial_screens.hint_line``), пока шаг не
    закрыт.
    """
    task = tutorial_rules.next_task(character)
    if task is None:
        return state.with_notice("Все шаги обучения сделаны.")

    card = tutorial_screens.card_for(task)
    city = known_city(content, state.city_id, character.city_id)
    needed = {
        ScreenId.QUEST_BOARD: "tavern",
        ScreenId.TAVERN: "tavern",
        ScreenId.SHOP: "shop",
        ScreenId.LOCATION_LIST: "locations",
    }.get(card.screen)
    if needed is not None and needed not in city.services:
        return state.with_notice(
            f"В городе {city.name} этого нет. Шаг обучения можно сделать в другом городе."
        )
    fresh = replace(
        state,
        city_id=city.id,
        list_page=PageState(),
        board_page=PageState(),
        location_page=PageState(),
    )
    opened = fresh.at(card.screen).with_notice(f"Шаг обучения: {card.title}. {card.text}")
    if card.screen is ScreenId.STATS:
        # Прочитать их *и есть* дело, а экран теперь открыт.
        return mark_task(opened, character, TutorialTask.STATS)
    return opened


def _handle_summary(state: PlayState, command: Command) -> PlayState:
    """Сводка сама ничего не делает: её кнопки уводят к делу (ADR 0053)."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из сводки.")
    if labels.LOCATIONS.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.LOCATION_LIST)
    if labels.ROAD.matches(command.argument):
        return replace(state, world_page=PageState()).at(ScreenId.WORLD)
    if labels.DUNGEONS.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.DUNGEON)
    return state.with_notice("Нажмите кнопку из сводки.")


def _handle_tutorial(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Экран-обзор обучения: одна кнопка ведёт к нынешнему шагу."""
    if command.intent is not Intent.SELECT or not tutorial_screens.DO_TASK.matches(
        command.argument
    ):
        return state.with_notice("Нажмите «Перейти к шагу» или «Назад».")
    return _walk_to_tutorial_step(content, character, state)


# --- меню, мир, город -------------------------------------------------


def _handle_main_menu(
    content: GameContent, character: Character, state: PlayState, command: Command, *, clock: Clock
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из меню.")
    if labels.WORLD.matches(command.argument):
        # «Мир» - это город, в котором игрок стоит: выбирать его заново каждый раз
        # незачем. На большую дорогу уводит «Дорога» уже из города (ADR 0051).
        return replace(state, city_id=character.city_id).at(ScreenId.CITY)
    if labels.CHARACTER.matches(command.argument):
        return state.at(ScreenId.CHARACTER)
    if labels.INVENTORY.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.INVENTORY)
    if labels.SKILLS.matches(command.argument):
        return replace(state, skill_page=PageState()).at(ScreenId.SKILLS)
    if labels.QUESTS.matches(command.argument):
        return state.at(ScreenId.QUESTS)
    if labels.CRAFTS.matches(command.argument):
        return state.at(ScreenId.CRAFTS)
    if labels.SETTINGS.matches(command.argument):
        return state.at(ScreenId.SETTINGS)
    if labels.TUTORIAL.matches(command.argument):
        # Не экран-обзор, а сразу дело: кнопка ведёт туда, где шаг делается, и
        # там его объясняет подсказка (ADR 0038).
        return _walk_to_tutorial_step(content, character, state)
    # Нажатая со старой клавиатуры тем, кто больше не смотритель, эта кнопка просто
    # перестаёт быть кнопкой.
    if labels.KEEPER.matches(command.argument) and character.is_admin:
        return state.at(ScreenId.KEEPER)
    return state.with_notice("Нажмите кнопку из меню.")


# --- crafts -----------------------------------------------------------


def _bag(goods: Goods) -> dict[str, int]:
    """Сумка в том виде, в каком её хотят правила ремесла: вещь - и сколько её."""
    return {held.item_id: held.quantity for held in goods.owned}


def _handle_crafts(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    clock: Clock,
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите ремесло из списка.")
    for craft in content.crafts:
        if command.argument.startswith(craft.name):
            return replace(state, craft_id=craft.id).at(ScreenId.CRAFT)
    return state.with_notice("Не узнал ремесло. Нажмите ремесло из списка.")


def _handle_craft(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
    *,
    clock: Clock,
    world_seed: str,
) -> PlayState:
    # Разбор нажатия отступает на список ремёсел так же, как ``render``: нажатие
    # на экране исчезнувшего ремесла иначе падало (``Claude.md``, правило 8).
    if not content.has_craft(state.craft_id):
        return go_back(state).with_notice("Такого ремесла в игре больше нет.")
    craft = content.craft(state.craft_id)
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        # У собирающего ремесла кнопок нет вовсе: сырьё берут у жилы и только
        # инструментом (ADR 0056), поэтому и отвечать ему нужно об этом, а не о
        # кнопках, которых на экране нет.
        if craft.gathers:
            return state.with_notice("Сырьё берут в локации, у жилы, а не отсюда.")
        return state.with_notice("Нажмите кнопку работы или «Назад».")

    owned = _bag(goods)
    # Только те работы, что мастеру по руке: рецепт выше ранга кнопки не рисует, и
    # нажать его нечем (``screens/crafts.open_recipes``).
    for recipe in craft_screens.open_recipes(content, character, craft):
        if not command.argument.startswith(craft_screens.output_name(content, recipe)):
            continue
        # Уже сделанная работа входит в сид, поэтому две партии подряд - не одна и та же
        # партия дважды.
        experience = character.crafts.progress(craft.id).experience
        seed = derive(world_seed, "craft", character.id, recipe.id, clock.now, experience)
        worked, made = craft_rules.make(content, character, recipe, owned, seed=seed)
        line = craft_screens.made_line(content, made)
        if not made.ok:
            return state.with_notice(line)
        # Кто-то в городе может ждать ровно этого: задание на сделанные вещи
        # засчитывается здесь, там, где случается работа.
        log, steps = quest_rules.record_craft(content, worked, made.item_id, made.count)
        worked = replace(worked, quests=log)
        for step in steps:
            line += f" Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}."
        write = PendingWrite(character=worked).with_items(*made.spent, (made.item_id, made.count))
        return state.storing(write).with_notice(line)

    return state.with_notice("Не узнал работу. Нажмите кнопку из списка.")


def _handle_world(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    # Клавиатура, которой отвечает смотритель, перечисляет все города, поэтому шаг,
    # который её читает, обязан с ней сходиться (screens.world_screen).
    available = (
        content.cities if character.is_admin else content.cities_available_at(character.level)
    )

    if command.intent is Intent.SELECT:
        here = known_city(content, state.city_id, character.city_id)
        for city in available:
            if city.name != command.argument:
                continue
            if city.id == here.id:
                return state.with_notice(f"Вы и так в городе {city.name}.")
            # Смотритель ходит по дороге даром; игрок платит за повозку и охрану.
            fare = (
                0
                if character.is_admin
                else economy.travel_price(character.level, abs(city.order - here.order))
            )
            if character.gold < fare:
                return state.with_notice(
                    f"Дорога до города {city.name} стоит {fare} золота, у вас {character.gold}."
                )
            moved = replace(character.with_gold(-fare), city_id=city.id)
            arrived = replace(
                state,
                city_id=city.id,
                pending=PendingWrite(character=moved, gold_flow=economy_log.SERVICE),
            )
            note = (
                f"Вы пришли в город {city.name}."
                if not fare
                else f"Вы пришли в город {city.name}. Дорога стоила {fare} золота."
            )
            return arrived.at(ScreenId.CITY).with_notice(note)

    # Закрытый город всё-таки может сюда попасть - набранным или нажатым со старой
    # клавиатуры, - поэтому он получает настоящее объяснение, а не общий отказ.
    for city in content.cities:
        if city.name == command.argument:
            return state.with_notice(
                f"Город {city.name} откроется на уровне {city.unlock_level}. "
                f"Ваш уровень: {character.level}."
            )
    return state.with_notice("Нажмите город из списка.")


def _handle_city(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку города.")
    city = known_city(content, state.city_id, character.city_id)
    if labels.ROAD.matches(command.argument):
        return replace(state, world_page=PageState()).at(ScreenId.WORLD)
    if labels.NPCS.matches(command.argument):
        # Кнопки может уже не быть: жителя убрали правкой между двумя нажатиями.
        if not content.npcs_in(city.id):
            return state.with_notice(f"В городе {city.name} сейчас никого нет.")
        return state.at(ScreenId.NPCS)
    service = SERVICES.get(command.argument)
    if service is None:
        return state.with_notice("Нажмите кнопку города.")

    declared, target = service
    if declared not in city.services:
        # Все города сейчас предлагают всё; сюда попадают только со старой
        # клавиатуры. Настоящего экрана за этим нет - есть объяснение (правило 12).
        return state.with_notice(f"В городе {city.name} нет такой службы. Ниже — то, что есть.")
    fresh = replace(state, list_page=PageState(), board_page=PageState(), mentor_page=PageState())
    return fresh.at(target)


def _handle_character(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из списка.")
    if labels.SKILLS.matches(command.argument):
        return replace(state, skill_page=PageState()).at(ScreenId.SKILLS)
    if labels.STATS.matches(command.argument):
        return mark_task(state.at(ScreenId.STATS), character, TutorialTask.STATS)

    for slot, slot_name in item_screens.SLOT_NAMES.items():
        if not screens.unequip_label(slot_name).matches(command.argument):
            continue
        item_id = character.equipment.item_in(slot)
        if item_id is None:
            return state.with_notice(f"В слоте «{slot_name}» ничего нет.")
        stripped = replace(character, equipment=character.equipment.unequip(slot))
        # Вещь могла исчезнуть из содержимого, пока лежала надетой: слот всё
        # равно освобождается, просто в сумку кладётся молча.
        if not content.has_item(item_id):
            return state.storing(PendingWrite(character=stripped)).with_notice(
                f"Слот «{slot_name}» освобождён: этой вещи в игре больше нет."
            )
        write = PendingWrite(character=stripped, items=((item_id, 1),))
        return state.storing(write).with_notice(
            f"{content.item(item_id).name} снят и убран в сумку."
        )
    return state.with_notice("Нажмите кнопку из списка.")


def _handle_stats(character: Character, state: PlayState, command: Command) -> PlayState:
    """Очко на нажатие. Экран, на котором отвечают, говорит, что это очко купило."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите характеристику, чтобы вложить очко.")
    for code, stat_name in STAT_NAMES.items():
        if not screens.spend_label(stat_name).matches(command.argument):
            continue
        if character.unspent_stat_points < 1:
            return state.with_notice("Свободных очков характеристик нет, их даёт новый уровень.")
        stronger = replace(
            character,
            allocated=character.allocated.with_change(StatCode(code), 1),
            unspent_stat_points=character.unspent_stat_points - 1,
        )
        return state.storing(PendingWrite(character=stronger)).with_notice(
            f"{stat_name} повышена. Осталось очков: {stronger.unspent_stat_points}."
        )
    return state.with_notice("Нажмите характеристику, чтобы вложить очко.")


# --- goods ------------------------------------------------------------


def _handle_shop(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    shown = _visible_stock(content, state, goods)
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите товар из списка.")
    if labels.SELL.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.SELL)

    item = shop_screens.item_from_button(content, command.argument, shown)
    if item is None:
        return state.with_notice("Нажмите товар из списка.")
    # Нажатие открывает карточку, а не кошелёк: сначала игрок узнаёт, что вещь
    # даёт и чем она лучше надетого, и только потом платит (``screens/items.py``).
    return replace(state, item_id=item.id).at(ScreenId.SHOP_ITEM)


def _visible_stock(content: GameContent, state: PlayState, goods: Goods) -> tuple[Item, ...]:
    """Товар, прошедший раздел и поиск, - тот же список, что нарисован."""
    return tuple(
        item for item in goods.stock if shop_screens.matches_filters(item, state.list_page, content)
    )


def _handle_shop_item(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    """Карточка товара: одна кнопка, и она стоит денег."""
    if not content.has_item(state.item_id):
        return go_back(state).with_notice("Этого товара больше нет на прилавке.")
    item = content.item(state.item_id)
    if command.intent is not Intent.SELECT or not item_screens.BUY.matches(command.argument):
        return state.with_notice("Нажмите «Купить» или «Назад».")

    price = goods.prices.get(item.id, item.price)
    if price > goods.gold:
        return state.with_notice(
            f"{item.name} стоит {price} золота, у вас {goods.gold}. Не хватает."
        )
    write = PendingWrite(character=character.with_gold(-price), items=((item.id, 1),)).because(
        economy_log.SHOP
    )
    bought = (
        go_back(replace(state, item_id=""))
        .storing(write)
        .with_notice(f"{item.name} куплен за {price} золота.")
    )
    return mark_task(bought, character, TutorialTask.TRADE)


def _handle_sell(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    shown = _visible_bag(content, state, goods)
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вещь из списка.")

    item = shop_screens.sold_from_button(content, command.argument, shown)
    if item is None:
        return state.with_notice("Нажмите вещь из списка.")
    price = economy.sell_price(content, item)
    write = PendingWrite(character=character.with_gold(price), items=((item.id, -1),)).because(
        economy_log.SHOP
    )
    sold = state.storing(write).with_notice(f"{item.name} продан за {price} золота.")
    return mark_task(sold, character, TutorialTask.TRADE)


def _handle_inventory(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    shown = _visible_bag(content, state, goods)
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите предмет из списка.")

    item = shop_screens.owned_from_button(content, command.argument, shown)
    if item is None:
        return state.with_notice("Нажмите предмет из списка.")
    # Нажатие открывает карточку вещи, а не действует ею: сначала игрок узнаёт,
    # что она даёт, и только потом надевает или пьёт (``screens/items.py``).
    return replace(state, item_id=item.id).at(ScreenId.ITEM)


def _visible_bag(content: GameContent, state: PlayState, goods: Goods) -> tuple[OwnedItem, ...]:
    """Сумка, прошедшая раздел и поиск, - тот же список, что нарисован."""
    return tuple(
        held
        for held in goods.owned
        if content.has_item(held.item_id)
        and shop_screens.matches_filters(content.item(held.item_id), state.list_page, content)
    )


def _handle_item(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    """Карточка вещи из сумки: надеть, выпить - и всё это по кнопке с именем."""
    if not content.has_item(state.item_id):
        return go_back(state).with_notice("Этой вещи у вас больше нет.")
    item = content.item(state.item_id)
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите действие или «Назад».")

    # Сначала шаг назад, потом запись: ``go_back`` чистит ``pending``, и запись,
    # сделанная до него, до хендлера бы не доехала.
    if item_screens.EQUIP.matches(command.argument) and item.is_equipment:
        return _equip(content, character, go_back(replace(state, item_id="")), item)
    if item_screens.USE.matches(command.argument) and item.kind.value == "consumable":
        healed_character, healed = adventure.use_consumable(content, character, item.id)
        if healed <= 0:
            return state.with_notice(f"{item.name} сейчас ничего не даст.")
        write = PendingWrite(character=healed_character, items=((item.id, -1),))
        return (
            go_back(replace(state, item_id=""))
            .storing(write)
            .with_notice(f"{item.name} выпито, восстановлено {healed} здоровья.")
        )
    return state.with_notice("Нажмите действие или «Назад».")


def _equip(content: GameContent, character: Character, state: PlayState, item: Item) -> PlayState:
    """Надеть вещь. То, что она заменяет, возвращается в сумку, а не в никуда.

    Надеть можно что угодно: чужая вещь не запрещена, она дорога. Чего она стоит
    точностью и инициативой, сказано на карточке до нажатия и повторяется вслед
    «надет» (ADR 0064).
    """
    previous = character.equipment.item_in(item.slot)
    dressed = replace(character, equipment=character.equipment.equip(item.slot, item.id))
    write = PendingWrite(character=dressed, items=((item.id, -1),))
    if previous is not None and content.has_item(previous):
        write = write.with_items((previous, 1))
        said = f"{item.name} надет, {content.item(previous).name} убран в сумку."
    else:
        said = f"{item.name} надет."
    if warning := gear.equip_warning(content, character, item):
        said = f"{said} {warning}"
    return state.storing(write).with_notice(said)


def _handle_settings(
    state: PlayState, command: Command, settings: AccessibilitySettings
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите переключатель из списка.")
    if settings_screens.REPEAT_SCREEN.matches(command.argument):
        return state.with_notice("Настройки доступности.")

    updated, said = settings_screens.toggled(settings, command.argument)
    if not said:
        return state.with_notice("Нажмите переключатель из списка.")
    # Сохраняет хендлер; ветка только решает, что должно измениться.
    return state.storing(PendingWrite(settings=updated)).with_notice(said)


# --- skills -----------------------------------------------------------


def _handle_skills(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    pool = skill_screens.matching_skills(
        content, character, skill_rules.teachable(content, character), state.skill_page
    )
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")
    if labels.SKILL_SLOTS.matches(command.argument):
        return state.at(ScreenId.SKILL_SLOTS)

    for skill in pool:
        if not command.argument.startswith(f"{skill.name} —"):
            continue
        if skill_rules.needs_edge(content, character, skill):
            return replace(state, edge_skill=skill.code).at(ScreenId.SKILL_EDGE)
        learned = skill_rules.learn(content, character, skill)
        if learned is None:
            return state.with_notice(skill_screens.refusal(content, character, skill))
        rank = learned.loadout.rank_of(skill.code)
        said = f"{skill.name}: ранг {rank}. Осталось очков: {learned.unspent_skill_points}."
        if skill_rules.needs_edge(content, learned, skill):
            return (
                replace(state, edge_skill=skill.code)
                .storing(PendingWrite(character=learned))
                .at(ScreenId.SKILL_EDGE)
                .with_notice(said)
            )
        if skill.is_active and skill.code not in learned.loadout.equipped_actives():
            said += " Положите его в слот, иначе в бою его не будет."
        return state.storing(PendingWrite(character=learned)).with_notice(said)
    return state.with_notice("Нажмите умение из списка.")


def _handle_slots(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите слот из списка.")
    for slot in range(content.rules.active_slots):
        if skill_screens.slot_label(content, character, slot).matches(command.argument):
            return replace(state, pick_slot=slot, list_page=PageState()).at(ScreenId.SKILL_PICK)
    return state.with_notice("Нажмите слот из списка.")


def _handle_pick(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    available = skill_rules.equippable(content, character)
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")

    if skill_screens.CLEAR_SLOT.matches(command.argument):
        emptied = skill_rules.put_in_slot(content, character, state.pick_slot, None)
        if emptied is None:
            return state.with_notice("Этот слот освободить нельзя.")
        return (
            state.storing(PendingWrite(character=emptied))
            .at(ScreenId.SKILL_SLOTS)
            .with_notice(f"Слот {state.pick_slot + 1} пуст.")
        )

    for skill in available:
        if not command.argument.startswith(f"{skill.name} —"):
            continue
        filled = skill_rules.put_in_slot(content, character, state.pick_slot, skill.code)
        if filled is None:
            return state.with_notice("Это умение сюда не встаёт.")
        placed = (
            state.storing(PendingWrite(character=filled))
            .at(ScreenId.SKILL_SLOTS)
            .with_notice(f"{skill.name} занял слот {state.pick_slot + 1}.")
        )
        return mark_task(placed, character, TutorialTask.SKILL_SLOT)
    return state.with_notice("Нажмите умение из списка.")


def _handle_edge(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if not content.has_skill(state.edge_skill):
        return go_back(replace(state, edge_skill="")).with_notice("Этого умения в игре больше нет.")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Выберите одну из двух граней.")
    skill = content.skill(state.edge_skill)
    for edge in skill.edges:
        if not skill_screens.edge_label(edge.name).matches(command.argument):
            continue
        chosen = skill_rules.choose_edge(character, skill, edge.code)
        if chosen is None:
            return state.with_notice("Грань этого умения уже выбрана.")
        # Говорится прямо, что press на умение снова покупает ранг: выбор грани
        # ранга не поднимает, и без этой фразы экран читается как заевший.
        said = f"{skill.name}: грань «{edge.name}» выбрана."
        if character.loadout.rank_of(skill.code) < content.rules.max_rank:
            said += " Ранг снова растёт за очко умений."
        return state.storing(PendingWrite(character=chosen)).at(ScreenId.SKILLS).with_notice(said)
    return state.with_notice("Выберите одну из двух граней.")


# --- городские службы -------------------------------------------------


def _handle_tavern(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку таверны.")
    if labels.QUEST_BOARD.matches(command.argument):
        return replace(state, board_page=PageState()).at(ScreenId.QUEST_BOARD)
    if labels.HAND_IN.matches(command.argument):
        return _hand_in(content, character, state)

    paid = labels.REST_PAID.matches(command.argument)
    if not paid and not labels.REST_FREE.matches(command.argument):
        return state.with_notice("Нажмите кнопку таверны.")

    result = adventure.rest(content, character, paid=paid)
    if result.refused == "whole":
        return state.with_notice("Вы и так целы. Незачем платить за койку.")
    if result.refused == "poor":
        return state.with_notice(
            f"Комната стоит {result.cost} золота, у вас {character.gold}. Во дворе есть солома."
        )
    if paid:
        said = f"Ночь в комнате: {result.cost} золота, здоровье полное."
    else:
        said = f"Ночь на соломе: восстановлено {result.healed} здоровья, спина не в счёт."
    rested = PendingWrite(character=result.character).because(economy_log.SERVICE)
    return state.storing(rested).with_notice(said)


def _handle_forge(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Кузница: вернуть прочность одной вещи или всему надетому разом (ADR 0057).

    Платят за сточенное, а не за визит, поэтому «починить всё» - это сумма, а не
    скидка. Не хватило золота - отказ до записи: кузнец не берёт половину работы.
    """
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку кузницы.")
    # Кузница делает с вещью три работы, и две из них - о сумке, а не о надетом
    # (ADR 0060). Обе идут своим списком, поэтому здесь только поворот.
    if labels.SALVAGE.matches(command.argument):
        return state.at(ScreenId.SALVAGE)
    if labels.REFORGE.matches(command.argument):
        return state.at(ScreenId.REFORGE)
    entries = repair_rules.bill(content, character)
    if not entries:
        return state.with_notice("Чинить нечего: всё надетое целое.")

    if labels.REPAIR_ALL.matches(command.argument):
        items = tuple(item for item, _ in entries)
        price = repair_rules.total(entries)
        said = f"Починено вещей: {len(items)}."
    else:
        chosen = next(
            (
                (item, price)
                for item, price in entries
                if city_screens.repair_label(item).matches(command.argument)
            ),
            None,
        )
        if chosen is None:
            return state.with_notice("Нажмите кнопку кузницы.")
        items, price = (chosen[0],), chosen[1]
        said = f"Починено: {chosen[0].name}."

    if character.gold < price:
        return state.with_notice(
            f"Работа стоит {price} золота, у вас {character.gold}. "
            "Кузнец берёт вперёд и половину работы не делает."
        )
    fixed = repair_rules.repaired(character.with_gold(-price), items)
    write = PendingWrite(character=fixed).because(economy_log.SERVICE)
    return state.storing(write).with_notice(f"{said} Уплачено {price} золота.")


def _handle_salvage(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
) -> PlayState:
    """Разобрать вещь из сумки на сырьё (ADR 0060).

    Надетое сюда не попадает: в списке только сумка, а сверх того разбор ещё раз
    спрашивает, не на игроке ли вещь.
    """
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вещь из списка.")
    item = city_screens.gear_from_button(content, command.argument, goods.owned)
    if item is None:
        return state.with_notice("Нажмите вещь из списка.")
    refused = salvage_rules.can_salvage(content, character, item)
    if refused:
        return state.with_notice(refused)

    made = salvage_rules.yield_of(
        content, item, modifiers=mods.collect_modifiers(content, character)
    )
    write = PendingWrite().with_items((item.id, -1), *made)
    got = ", ".join(f"{content.item(item_id).name} {count}" for item_id, count in made)
    return state.storing(write).with_notice(f"{item.name} разобран. Вышло: {got}.")


def _handle_reforge(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    goods: Goods,
    *,
    clock: Clock,
    world_seed: str,
) -> PlayState:
    """Перековать вещь: тот же вид, та же редкость, другой ведущий аффикс (ADR 0059).

    Плата вперёд, как и за починку. Оттиск всегда меняется: заплатить и получить
    ровно то же, что принёс, - это пошлина, а не работа.
    """
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вещь из списка.")
    item = city_screens.gear_from_button(content, command.argument, goods.owned)
    if item is None:
        return state.with_notice("Нажмите вещь из списка.")
    if not salvage_rules.can_reforge(content, item):
        return state.with_notice("Перековывать нечего: у этой вещи нет прибавок.")
    if character.equipment.item_in(item.slot) == item.id:
        return state.with_notice("Эта вещь на вас надета. Снимите её, потом перековывайте.")

    price = salvage_rules.reforge_price(content, item)
    if character.gold < price:
        return state.with_notice(
            f"Работа стоит {price} золота, у вас {character.gold}. Кузнец берёт вперёд."
        )
    seed = derive(world_seed, "reforge", character.id, item.id, clock.now)
    made_id = salvage_rules.reforged(content, item.id, source=rng(seed))
    write = (
        PendingWrite(character=character.with_gold(-price))
        .with_items((item.id, -1), (made_id, 1))
        .because(economy_log.SERVICE)
    )
    made = content.item(made_id)
    return state.storing(write).with_notice(
        f"{item.name} перекован. Теперь это {made.name}. Уплачено {price} золота."
    )


def _hand_in(content: GameContent, character: Character, state: PlayState) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    due = quest_rules.ready_to_hand_in(content, character, city.id)
    if not due:
        return state.with_notice("Сдавать нечего: ни одно задание не досчитано.")
    payout = quest_rules.hand_in(content, character, due[0].quest)
    if payout is None:  # pragma: no cover - ready_to_hand_in это уже проверил
        return state.with_notice("Сдавать нечего.")

    write = PendingWrite(character=payout.character).because(economy_log.QUEST)
    said = (
        f"Задание «{payout.quest.name}» закрыто. "
        f"Плата: {payout.gold} золота и {payout.experience} опыта."
    )
    if payout.item_id and content.has_item(payout.item_id):
        write = write.with_items((payout.item_id, 1))
        said += f" Сверху дали: {content.item(payout.item_id).name}."
    # Об уровне здесь не говорят: он приходит своим сообщением, сразу за этим
    # экраном (``handlers/play``, ``screens/play.level_up_report``).
    return mark_task(state.storing(write).with_notice(said), character, TutorialTask.HAND_IN)


def _handle_board(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    offered = quest_rules.available(content, character, city.id)
    working = tuple(
        step for step in quest_rules.taken(content, character) if step.quest.city_id == city.id
    )
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите задание из списка.")
    for quest in offered:
        if quest_screens.quest_button(quest).matches(command.argument):
            return replace(state, quest_id=quest.id).at(ScreenId.QUEST_OFFER)
    # Взятое задание с доски не пропадает, и нажатие на него открывает тот же
    # разговор - только уже со счётом и с правом отказаться.
    for step in working:
        if quest_screens.taken_button(step.quest, step.progress).matches(command.argument):
            return replace(state, quest_id=step.quest.id).at(ScreenId.QUEST_OFFER)
    return state.with_notice("Нажмите задание из списка.")


def _handle_offer(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if not content.has_quest(state.quest_id):
        return go_back(replace(state, quest_id="")).with_notice(
            "Этого задания больше нет. Посмотрите доску заново."
        )
    if command.intent is not Intent.SELECT:
        return state.with_notice("Согласитесь, спросите или уйдите.")
    quest = content.quest(state.quest_id)
    if labels.QUEST_ASK.matches(command.argument):
        return state.with_notice(
            f"Платит {quest.giver}, из своего кармана, после счёта. "
            f"Сбор берут с торговли, а не с задания."
        )
    if labels.QUEST_LEAVE.matches(command.argument):
        # Отказ никогда не закрывает задание насовсем (Narrative.md, раздел 4).
        return go_back(state).with_notice("Вы ушли. Задание останется на доске.")
    if labels.QUEST_ABANDON.matches(command.argument):
        if not character.quests.is_taken(quest.id):
            return state.with_notice("Это задание у вас не взято.")
        given_back = quest_rules.abandon(character, quest)
        return (
            go_back(replace(state, quest_id=""))
            .storing(PendingWrite(character=given_back))
            .with_notice(
                f"Задание «{quest.name}» возвращено. Счёт потерян, само задание осталось на доске."
            )
        )
    if not labels.QUEST_ACCEPT.matches(command.argument):
        return state.with_notice("Согласитесь, спросите или уйдите.")

    accepted = quest_rules.take(content, character, quest)
    if accepted == character:
        return state.with_notice("Это задание уже у вас или уже закрыто.")
    taken = (
        go_back(replace(state, quest_id=""))
        .storing(PendingWrite(character=accepted))
        .with_notice(f"Задание «{quest.name}» взято. Счёт идёт с этой минуты.")
    )
    return mark_task(taken, character, TutorialTask.QUEST)


def _handle_mentor(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    known = skill_rules.forgettable(content, character)
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")

    price = economy.mentor_price(character.level)
    for skill in known:
        rank = character.loadout.rank_of(skill.code)
        if not city_screens.forget_label(skill.name, rank).matches(command.argument):
            continue
        if character.gold < price:
            return state.with_notice(f"Наставник берёт {price} золота, у вас {character.gold}.")
        refund = skill_rules.spent_on(content, character, skill.code)
        forgotten = skill_rules.forget(content, character, skill)
        if forgotten is None:  # pragma: no cover - в списке лежат только разбираемые умения
            return state.with_notice("Это умение сейчас не разобрать.")
        paid = forgotten.with_gold(-price)
        return state.storing(PendingWrite(character=paid).because(economy_log.SERVICE)).with_notice(
            f"{skill.name} забыто. Вернулось очков: {refund}. Заплачено {price} золота."
        )
    return state.with_notice("Нажмите умение из списка.")


def _handle_bank(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите сумму.")
    for step in city_screens.DEPOSIT_STEPS:
        if city_screens.deposit_label(step).matches(command.argument):
            if character.gold < step:
                return state.with_notice(f"На руках только {character.gold}.")
            stored = replace(character.with_gold(-step), bank_gold=character.bank_gold + step)
            return state.storing(PendingWrite(character=stored)).with_notice(
                f"В ячейке теперь {stored.bank_gold}. На руках {stored.gold}."
            )
        if city_screens.withdraw_label(step).matches(command.argument):
            if character.bank_gold < step:
                return state.with_notice(f"В ячейке только {character.bank_gold}.")
            taken = replace(character.with_gold(step), bank_gold=character.bank_gold - step)
            return state.storing(PendingWrite(character=taken)).with_notice(
                f"На руках теперь {taken.gold}. В ячейке {taken.bank_gold}."
            )
    return state.with_notice("Нажмите сумму.")


def _handle_dungeon(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
) -> PlayState:
    """Выбор подземелья из списка. Сложность спрашивают на следующем экране."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите подземелье или «Назад».")
    city = known_city(content, state.city_id, character.city_id)
    for one in open_dungeons(city, character):
        if labels.label(one.name).matches(command.argument):
            return replace(state, dungeon_pick=one.id).at(ScreenId.DUNGEON_PICK)
    return state.with_notice("Не узнал это подземелье.")


def _handle_dungeon_pick(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    clock: Clock,
) -> PlayState:
    """Выбор сложности и заход в выбранное подземелье (ADR 0041, ADR 0036)."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Выберите сложность или «Назад».")
    city = known_city(content, state.city_id, character.city_id)
    if not city.has_dungeon(state.dungeon_pick):
        return state.at(ScreenId.DUNGEON).with_notice("Это подземелье пропало. Выберите заново.")
    dungeon = city.dungeon(state.dungeon_pick)
    if not dungeon_open(city, character, dungeon):
        return state.at(ScreenId.DUNGEON).with_notice("Этот спуск ещё не открыт.")

    for difficulty in dungeon_screens.DIFFICULTY_ORDER:
        if dungeon_screens.difficulty_label(difficulty).matches(command.argument):
            descent = Descent(
                city_id=city.id,
                dungeon_id=dungeon.id,
                level=dungeon.level,
                layer=0,
                started_at=clock.now,
                difficulty=difficulty.value,
                room=dungeon_rules.RoomKind.SKIRMISH.value,
            )
            return replace(state, descent=descent, fight="dungeon").at(ScreenId.COMBAT)
    return state.with_notice("Выберите сложность или «Назад».")


# --- локации ----------------------------------------------------------


# --- отряд ------------------------------------------------------------
#
# Автомат об отряде не знает ничего: он лежит в общем хранилище, а автомат не
# читает и не пишет. Здесь только намерение - что игрок попросил сделать, - а
# делает это хендлер (``handlers/play.py``).

#: Намерение отряда и то слово, которым оно называется в состоянии.
_PARTY_ACTIONS: dict[Intent, str] = {
    Intent.PARTY_CREATE: "create",
    Intent.PARTY_DISBAND: "disband",
    Intent.PARTY_LEAVE: "leave",
    Intent.PARTY_ACCEPT: "accept",
    Intent.PARTY_DECLINE: "decline",
}


def _party_intent(state: PlayState, command: Command) -> PlayState | None:
    """Шаг отряда, откуда бы его ни сделали. ``None`` - это был не он."""
    if command.intent is Intent.PARTY:
        return state.at(ScreenId.PARTY)
    if command.intent is Intent.PARTY_INVITE:
        return state.at(ScreenId.PARTY_INVITE)
    action = _PARTY_ACTIONS.get(command.intent)
    if action is None:
        return None
    # Экран остаётся тем же: зов приходит туда, где игрок стоит, и соглашаться
    # он должен там же, не бросая ни боя, ни лавки.
    return replace(state, party_action=action)


def _handle_party_invite(state: PlayState, command: Command, text: str) -> PlayState:
    """Набранное на этом экране - имя того, кого зовут, и больше ничего."""
    name = text.strip()
    if command.intent is not Intent.UNKNOWN or not name:
        return state.with_notice("Напишите имя того, кого зовёте, одним сообщением.")
    return replace(state, invite_name=name)


#: Намерение гильдии и слово, которым оно называется в состоянии.
_GUILD_ACTIONS: dict[Intent, str] = {
    Intent.GUILD_DISBAND: "disband",
    Intent.GUILD_LEAVE: "leave",
    Intent.GUILD_ACCEPT: "accept",
    Intent.GUILD_DECLINE: "decline",
}

_GUILD_SCREENS: dict[Intent, ScreenId] = {
    Intent.GUILD: ScreenId.GUILD,
    Intent.GUILD_FOUND: ScreenId.GUILD_FOUND,
    Intent.GUILD_INVITE: ScreenId.GUILD_INVITE,
    Intent.GUILD_ROSTER: ScreenId.GUILD_ROSTER,
    Intent.GUILD_VAULT: ScreenId.GUILD_VAULT,
}


def _guild_intent(state: PlayState, command: Command) -> PlayState | None:
    """Шаг гильдии, откуда бы его ни сделали. ``None`` - это был не он."""
    screen = _GUILD_SCREENS.get(command.intent)
    if screen is not None:
        # Состав открывается с первой страницы: список, открытый на середине
        # чужого списка, - это чужая страница (``docs/accessibility.md``).
        opened = replace(state, list_page=PageState()) if screen is ScreenId.GUILD_ROSTER else state
        return opened.at(screen)
    action = _GUILD_ACTIONS.get(command.intent)
    if action is None:
        return None
    return replace(state, guild_action=action)


def _handle_guild_text(state: PlayState, command: Command, text: str, *, action: str) -> PlayState:
    """Набранное на экране «основать» / «позвать» - имя, и больше ничего."""
    name = text.strip()
    if command.intent is not Intent.UNKNOWN or not name:
        return state.with_notice("Напишите имя одним сообщением.")
    return replace(state, guild_action=action, guild_arg=name)


def _guild_pick(command: Command, prefix: str) -> str:
    """Имя из надписи вроде «Повысить: Мирна». Пусто - не та кнопка."""
    if command.intent is Intent.SELECT and command.argument.startswith(f"{prefix}: "):
        return command.argument.split(": ", 1)[1].strip()
    return ""


def _handle_guild_roster(state: PlayState, command: Command) -> PlayState:
    # Страницы состава листает общий разбор в ``advance``: экран объявляет их
    # число в ``metadata``, а ``LIST_PAGE_FIELD`` называет, где оно лежит.
    for prefix, action in (("Повысить", "promote"), ("Понизить", "demote"), ("Выгнать", "kick")):
        if name := _guild_pick(command, prefix):
            return replace(state, guild_action=action, guild_arg=name)
    return state.with_notice("Нажмите кнопку рядом с именем.")


def _handle_guild_vault(state: PlayState, command: Command) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите сумму.")
    for step in guild_screens.VAULT_STEPS:
        if labels.guild_deposit_label(step).matches(command.argument):
            return replace(state, guild_action="deposit", guild_arg=str(step))
        if labels.guild_withdraw_label(step).matches(command.argument):
            return replace(state, guild_action="withdraw", guild_arg=str(step))
    return state.with_notice("Нажмите сумму.")


# --- передача вещи (``handlers/play._transfer_step``) -----------------
#
# Автомат об отряде и гильдии ничего не читает, поэтому он только выбирает: кому,
# что и сколько. Двигает сумку другого игрока хендлер, читая ``transfer_amount``.


def _transfer_intent(state: PlayState, command: Command) -> PlayState | None:
    """Начало передачи вещи — из отряда или из гильдии. ``None`` — это был не он.

    Кнопка есть только на экране объединения, но набранная команда
    (``/отряд передать``) работает откуда угодно: пустой список получателей на
    том конце просто скажет, что передавать некому.
    """
    if command.intent is Intent.PARTY_TRANSFER:
        scope = "party"
    elif command.intent is Intent.GUILD_TRANSFER:
        scope = "guild"
    else:
        return None
    return replace(
        state, transfer_scope=scope, transfer_to="", transfer_item="", list_page=PageState()
    ).at(ScreenId.TRANSFER_TO)


def _transfer_home(state: PlayState) -> ScreenId:
    return ScreenId.GUILD if state.transfer_scope == "guild" else ScreenId.PARTY


def _transfer_now(state: PlayState, item_id: str, amount: int) -> PlayState:
    """Всё выбрано: вернуться на экран объединения и оставить хендлеру триггер."""
    return replace(state, transfer_item=item_id, transfer_amount=amount).at(_transfer_home(state))


def _handle_transfer_to(
    state: PlayState, command: Command, recipients: tuple[str, ...]
) -> PlayState:
    if not recipients:
        return go_back(state).with_notice("Передавать некому: рядом больше никого нет.")
    if command.intent is Intent.SELECT and command.argument in recipients:
        return replace(state, transfer_to=command.argument, list_page=PageState()).at(
            ScreenId.TRANSFER_ITEM
        )
    return state.with_notice("Нажмите, кому передать вещь.")


def _handle_transfer_item(
    content: GameContent, state: PlayState, command: Command, goods: Goods
) -> PlayState:
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вещь из сумки.")
    item = transfer_screens.item_from_button(content, command.argument, goods.owned)
    if item is None:
        return state.with_notice("Нажмите вещь из сумки.")
    held = _owned_count(goods, item.id)
    if held <= 0:
        return state.with_notice("Этой вещи у вас уже нет.")
    if held == 1:
        return _transfer_now(state, item.id, 1)
    return replace(state, transfer_item=item.id).at(ScreenId.TRANSFER_AMOUNT)


def _handle_transfer_amount(
    content: GameContent, state: PlayState, command: Command, text: str, goods: Goods
) -> PlayState:
    if not content.has_item(state.transfer_item):
        return go_back(replace(state, transfer_item="")).with_notice("Этой вещи в игре больше нет.")
    held = _owned_count(goods, state.transfer_item)
    if held <= 0:
        return go_back(replace(state, transfer_item="")).with_notice("Этой вещи у вас уже нет.")
    if command.intent is Intent.SELECT and labels.TRANSFER_ALL.matches(command.argument):
        return _transfer_now(state, state.transfer_item, held)
    wanted = text.strip()
    if command.intent is Intent.UNKNOWN and wanted.isdecimal() and 1 <= int(wanted) <= held:
        return _transfer_now(state, state.transfer_item, int(wanted))
    return state.with_notice(f"Наберите число от 1 до {held} или нажмите «Передать всё».")


def _handle_location_list(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите локацию из списка.")

    for location in city.locations:
        if command.argument.startswith(f"{location.slot}. {location.name}"):
            if character.level < location.level_min and not character.is_admin:
                return state.with_notice(
                    f"Локация {location.name} рассчитана на уровни с {location.level_min} "
                    f"по {location.level_max}. Ваш уровень: {character.level}."
                )
            # То, что осталось в узлах, общее для всех, кто в месте, поэтому хендлер
            # читает это из кэша до отрисовки экрана; ветка говорит лишь, в какую
            # локацию вошли и где стоит игрок.
            session = LocationSession(city_id=city.id, slot=location.slot, node=0)
            return replace(state, session=session).at(ScreenId.LOCATION)
    return state.with_notice("Не узнал эту локацию. Нажмите локацию из списка.")


def _handle_location(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    world_seed: str,
    neighbours: Sequence[Presence] = (),
    fights: Sequence[Engagement] = (),
    location_state: LocationState,
    now: int,
) -> PlayState:
    if not location_known(content, state.session):
        return (
            replace(state, session=LocationSession())
            .at(ScreenId.LOCATION_LIST)
            .with_notice(LOST_VISIT)
        )
    location = build_location(
        content, world_seed, state.session, epoch=node_rules.location_epoch(location_state)
    )
    node = location.node(state.session.node)

    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите переход или действие узла.")

    if screens.LEAVE_LOCATION.matches(command.argument) or (
        node.kind is NodeKind.EXIT
        and command.argument.startswith(screens.NODE_ACTIONS[NodeKind.EXIT])
    ):
        return (
            replace(state, session=LocationSession())
            .at(ScreenId.LOCATION_LIST)
            .with_notice(f"Вы покинули локацию {location.name}.")
        )

    if labels.ENTER_ROAMER.matches(command.argument):
        return _enter_roamer(state, location_state, node.index, now)

    for person in neighbours:
        if screens.invite_label(person.name).matches(command.argument):
            # Звать умеет только хендлер: отряд лежит в общем хранилище, а
            # автомат ничего не читает и не пишет (``domain/rules/party.py``).
            return replace(state, invite=person.character_id).with_notice(
                f"Зов отправлен: {person.name}."
            )
        if not screens.attack_label(person.name).matches(command.argument):
            continue
        refused = pvp_rules.refusal(
            character,
            defender_name=person.name,
            defender_level=person.level,
            location_allows=_location_allows_pvp(content, state.session),
        )
        if refused:
            return state.with_notice(refused)
        # Боем владеет хендлер: только он может прочитать из хранилища другого персонажа
        # и снять с него слепок.
        return replace(state, fight=f"pvp:{person.character_id}").at(ScreenId.COMBAT)

    for neighbour in (location.node(index) for index in node.links):
        if screens.node_button(neighbour).matches(command.argument):
            return replace(state, session=replace(state.session, node=neighbour.index))

    # У каждой стаи узла своя кнопка, и узнаётся она по слову действия и номеру
    # места в волне (ADR 0065): «Вступить в бой 2: Серый волк, 3 штуки».
    picked = _picked_foe(
        content,
        state,
        command,
        location=location,
        node=node,
        world_seed=world_seed,
        location_state=location_state,
        fights=fights,
        now=now,
    )
    if picked is not None:
        return picked

    # Кнопка узла называет то, к чему ведёт («Обыскать тайник»), и потому
    # узнаётся по слову действия, а не по строке целиком (ADR 0063).
    if command.argument.startswith(screens.NODE_ACTIONS[node.kind]):
        return _resolve_node_action(
            content, character, state, location, world_seed, location_state, now
        )

    return state.with_notice("Не узнал это действие. Нажмите кнопку узла.")


def _picked_foe(
    content: GameContent,
    state: PlayState,
    command: Command,
    *,
    location: GeneratedLocation,
    node: LocationNode,
    world_seed: str,
    location_state: LocationState,
    fights: Sequence[Engagement],
    now: int,
) -> PlayState | None:
    """Нажатие на одну названную стаю узла. ``None`` - нажали не по стае (ADR 0065).

    Кнопка стаи говорит, что с ней делать: свободную бьют, за занятую уже
    дерутся, и в тот бой вмешиваются. Клавиатура на руках у игрока может отстать
    от узла на минуту, поэтому нажатие «не по той» кнопке объясняет, а не молчит
    (правило доступности 12).
    """
    if not node.kind.is_combat:
        return None
    left = node_rules.standing_at(
        visit_seed(world_seed, state.session), location, location_state, node.index, now
    )
    foes = node_foes(
        content,
        world_seed=world_seed,
        session=state.session,
        location=location,
        index=node.index,
        standing={node.index: left},
        state=location_state,
        fights=fights,
    )
    alone = left.size <= 1
    for foe in foes:
        if screens.foe_label(node.kind, foe, alone).matches(command.argument):
            if foe.busy:
                return state.with_notice(
                    f"За эту стаю уже дерётся {foe.fighter}. В этот бой можно вмешаться."
                )
            return replace(state, fight=f"node:{foe.place}").at(ScreenId.COMBAT)
        if screens.join_label(foe, alone).matches(command.argument):
            if not foe.busy:
                return state.with_notice("Тот бой уже кончился. Эта стая стоит свободно.")
            return replace(state, fight=f"join:{foe.place}").at(ScreenId.COMBAT)
    return None


def _enter_roamer(
    state: PlayState, location_state: LocationState, node_index: int, now: int
) -> PlayState:
    """Спуститься в блуждающее подземелье (ADR 0037).

    Ветка только собирает заход; замок берёт хендлер - подземелье общее, а
    автомат ничего не читает и не пишет (``domain/rules/roamer.py``).
    """
    roamer = location_state.roamer
    if roamer is None or roamer.node != node_index:
        return state.with_notice("Здесь никакого подземелья нет.")
    if roamer.taken:
        return state.with_notice("В подземелье уже спустились. Дождитесь, пока выйдут.")
    descent = Descent(
        city_id=state.session.city_id,
        slot=state.session.slot,
        level=max(1, roamer.level),
        layer=0,
        started_at=now,
        difficulty=roamer.difficulty,
        room=dungeon_rules.RoomKind.SKIRMISH.value,
        roamer=True,
        stamp=roamer.stamp,
        group=roamer.group,
    )
    return replace(state, descent=descent, fight="dungeon").at(ScreenId.COMBAT)


def _empty_node_line(node_name: str) -> str:
    """Ответ на действие в вычищенном узле.

    Сколько ждать, скажет строкой ниже сам экран: повторять это дважды подряд
    значит заставить слушать одно и то же два раза.
    """
    return f"{node_name}: здесь уже всё разобрали. Идите к соседнему узлу или загляните позже."


def _resolve_node_action(
    content: GameContent,
    character: Character,
    state: PlayState,
    location: GeneratedLocation,
    world_seed: str,
    location_state: LocationState,
    now: int,
) -> PlayState:
    """Небоевые узлы разрешаются сразу; бой передаётся боевому экрану."""
    index = state.session.node
    node = location.node(index)

    if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        # Дверь не платит никому: посмотреть на неё - это ответ, а не награда.
        return state.with_notice(
            f"{node.name}: смотреть здесь не на что, кроме дороги в обе стороны."
        )

    left = node_rules.standing_at(
        visit_seed(world_seed, state.session), location, location_state, index, now
    )
    if left.empty:
        return state.with_notice(_empty_node_line(node.name))

    if node.kind.is_combat:
        # Бой собирает сам хендлер: ему принадлежат и сборка противника, и кэш, в
        # котором бой живёт. Место в волне называется здесь же: без него хендлер
        # не знает, за какую из стай узла дерутся (ADR 0065).
        return replace(state, fight=f"node:{left.free[0]}").at(ScreenId.COMBAT)

    biomes = _location_biomes(content, state.session)
    if node.kind is NodeKind.GATHER:
        # Отказ ходом не считается: без годного инструмента жила не трогается
        # вовсе, и волна в узле остаётся нетронутой (ADR 0056).
        lying = adventure.GATHER_SOURCES.get(node.name, "")
        refused = tool_rules.refusal(content, character, lying)
        if refused:
            return state.with_notice(refused)

    # Волна входит в сид, поэтому вторая горсть из той же жилы - не первая заново.
    seed = derive(visit_seed(world_seed, state.session), "search", index, left.wave, left.taken)
    result = adventure.resolve_search(
        content,
        character,
        node,
        seed,
        tool=tool_rules.tool_of(content, character),
        biomes=biomes,
    )
    write = PendingWrite(character=result.character, node_take=index, node_kind=node.kind.value)
    if result.item_id:
        write = write.with_items((result.item_id, max(1, result.count)))

    said = search_line(content, node.name, result)
    # Сколько осталось, экран скажет строкой ниже своими словами - здесь это
    # было бы второй раз подряд об одном и том же.
    if left.left <= 1:
        said = f"{said} Узел вычищен."
    return state.storing(write).with_notice(said)


def _location_biomes(content: GameContent, session: LocationSession) -> frozenset[str]:
    """Земля этого места: что в ней вообще лежит, решает сбор (``crafts.yields_here``)."""
    if not content.has_city(session.city_id):
        return frozenset()
    city = content.city(session.city_id)
    if not city.has_location(session.slot):
        return frozenset()
    return frozenset({city.location(session.slot).biome})


def search_line(content: GameContent, node_name: str, result: adventure.SearchResult) -> str:
    """Одна фраза об отработанном узле, а следом - что он дал."""
    parts = [f"{node_name}: сделано."]
    if result.gold:
        parts.append(f"Найдено золота: {result.gold}.")
    if result.item_id and content.has_item(result.item_id):
        taken = content.item(result.item_id).name
        many = f"Взято: {taken}, {result.count} штук."
        parts.append(many if result.count > 1 else f"Взято: {taken}.")
    if result.craft_experience:
        parts.append(f"Работы записано: {result.craft_experience}.")
    if result.tool_broken:
        parts.append("Инструмент сточился и рассыпался. Новый берут в лавке.")
    elif result.tool_left:
        parts.append(f"Сборов у инструмента осталось: {result.tool_left}.")
    if result.healed:
        parts.append(f"Восстановлено здоровья: {result.healed}.")
    parts.append(f"Опыт: {result.experience}.")
    for step in result.quest_steps:
        parts.append(f"Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}.")
    return " ".join(parts)
