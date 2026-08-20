"""Navigation and every action outside a fight, as a pure state machine.

Same shape as the creation flow: ``advance(state, message) -> state``, no I/O and
no clock. What the world looks like arrives as values - the standing generation
of a location, the shop rotation, the moment gathering is judged at - which is
what keeps generation reproducible (``docs/procgen.md``).

The flow never writes anything. When a step should change stored data - gold, a
loadout, a contract, the bag - it puts the result into :class:`PendingWrite` and
the handler persists it. That is what keeps the whole game testable without a
database (``docs/architecture.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from mmorpg import economy_log
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent, Item, SkillKind
from mmorpg.domain.entities.location import (
    GeneratedLocation,
    LocationState,
    NodeKind,
    Presence,
)
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.domain.procgen.location import generate_location
from mmorpg.domain.procgen.seeds import derive, location_seed
from mmorpg.domain.rules import adventure, economy
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.domain.rules import pvp as pvp_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.domain.rules import tutorial as tutorial_rules
from mmorpg.domain.rules.progression import LevelUp
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.presentation.telegram.flows import keeper as keeper_flow
from mmorpg.presentation.telegram.flows.state import (
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
from mmorpg.presentation.telegram.screens import format as format_screens
from mmorpg.presentation.telegram.screens import items as item_screens
from mmorpg.presentation.telegram.screens import play as screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens import settings as settings_screens
from mmorpg.presentation.telegram.screens import shop as shop_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens import tutorial as tutorial_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.creation import STAT_NAMES
from mmorpg.presentation.telegram.screens.keeper import KeeperView
from mmorpg.presentation.telegram.screens.paginated import (
    SEARCH_PROMPT,
    PageState,
    filters_screen,
    total_pages,
)
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import NavigationStack

DEFAULT_SETTINGS = AccessibilitySettings()

# Экраны, состояние которых живёт не здесь: панель смотрителя и разговор с
# жителем города (``flows/keeper.py``).
KEEPER_SCREENS = keeper_flow.SCREENS

# Which city service each button opens, and the id the city declares it under.
SERVICES: dict[str, tuple[str, ScreenId]] = {
    labels.LOCATIONS.text: ("locations", ScreenId.LOCATION_LIST),
    labels.SHOP.text: ("shop", ScreenId.SHOP),
    labels.DUNGEONS.text: ("dungeons", ScreenId.DUNGEON),
    labels.ARENA.text: ("arena", ScreenId.ARENA),
    labels.CHAMBER.text: ("chamber", ScreenId.CHAMBER),
    labels.TAVERN.text: ("tavern", ScreenId.TAVERN),
    labels.MENTOR.text: ("mentor", ScreenId.MENTOR),
    labels.BANK.text: ("bank", ScreenId.BANK),
}

# Said when the screen claims a location the game can no longer rebuild.
LOST_VISIT = "Та вылазка уже закончилась. Выберите локацию заново."


def begin(character: Character) -> PlayState:
    return PlayState(city_id=character.city_id)


def known_city(content: GameContent, city_id: str, fallback_id: str) -> City:
    """The city this id names, or the one the character stands in.

    State comes back from storage, and storage outlives content: a city id written
    by an older release may name nothing at all today. That is not an error the
    player should ever meet - they get their own city instead of a crash
    (accessibility rule 12, same principle as ``NavigationStack.deserialise``).
    """
    for candidate in (city_id, fallback_id):
        if content.has_city(candidate):
            return content.city(candidate)
    return content.cities[0]


def _location_allows_pvp(content: GameContent, session: LocationSession) -> bool:
    """Whether this place is one of the free ones (``domain/rules/pvp.py``)."""
    if not location_known(content, session):
        return False
    return content.city(session.city_id).location(session.slot).pvp


def location_known(content: GameContent, session: LocationSession) -> bool:
    """Whether the visit in this session can still be rebuilt from content."""
    return (
        session.active
        and content.has_city(session.city_id)
        and content.city(session.city_id).has_location(session.slot)
    )


def build_location(
    content: GameContent, world_seed: str, session: LocationSession
) -> GeneratedLocation:
    """Rebuild the location the player is standing in. Nothing about it is stored."""
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
    )


def visit_seed(world_seed: str, session: LocationSession) -> bytes:
    """The seed of the place itself. The map never changes, so neither does it."""
    return location_seed(world_seed, session.city_id, session.slot)


def node_standing(
    content: GameContent,
    world_seed: str,
    session: LocationSession,
    state: LocationState,
    now: int,
) -> dict[int, node_rules.Standing]:
    """What is left in every node of the location being visited."""
    location = build_location(content, world_seed, session)
    return node_rules.standing(visit_seed(world_seed, session), location, state, now)


def node_fight_seed(world_seed: str, session: LocationSession, wave: int) -> bytes:
    """The seed of the fight standing at the current node in its current wave.

    The wave is part of it on purpose: the pack that walks in after the last one
    fell is a different pack, or the node would hand out the same three wolves for
    ever (``domain/rules/nodes.py``).
    """
    return derive(visit_seed(world_seed, session), "fight", session.node, wave)


def descent_fight_seed(world_seed: str, descent: Descent) -> bytes:
    return derive(world_seed, "descent", descent.city_id, descent.started_at, descent.depth)


def dungeon_level(content: GameContent, character: Character, city_id: str) -> int:
    """A descent is tuned to the player, inside the band the city allows."""
    city = content.city(city_id)
    return max(city.level_min, min(city.level_max, character.level + 1))


# --- rendering --------------------------------------------------------


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
    arena_table: Sequence[Character] = (),
    tally: Mapping[str, int] | None = None,
    keeper: KeeperView | None = None,
    location_state: LocationState | None = None,
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
            location = build_location(content, world_seed, state.session)
            return screens.location_screen(
                location,
                location.node(state.session.node),
                standing=node_standing(content, world_seed, state.session, here_now, clock.now),
                character_level=character.level,
                others=neighbours,
                pvp=_location_allows_pvp(content, state.session),
                notice=state.notice,
            )
        # The screen says "location" but the visit behind it is gone: state saved
        # by an older release, or a location content no longer has. The player is
        # put back on the list they walked in from, never left with a crash.
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
        case ScreenId.CHAMBER_PLEDGE:
            return chamber_screens.pledge_screen(content, character, state.list_page, state.notice)
        case ScreenId.SKILLS:
            return skill_screens.skills_screen(content, character, state.skill_page, state.notice)
        case ScreenId.SKILL_SLOTS:
            return skill_screens.slots_screen(content, character, state.notice)
        case ScreenId.SKILL_PICK:
            return skill_screens.pick_screen(
                content,
                character,
                _pick_kind(state),
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
                now=state.craft_moment,
                cooldown=clock.gather_cooldown,
                biomes=here.biomes,
                place=here.name,
                notice=state.notice,
            )
        # A craft that content no longer has is not an error: the player is put
        # back on the list they came from (accessibility rule 12).
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
        case ScreenId.MENTOR:
            return city_screens.mentor_screen(
                content, character, city, state.mentor_page, state.notice
            )
        case ScreenId.BANK:
            return city_screens.bank_screen(content, character, city, state.notice)
        case ScreenId.DUNGEON:
            return city_screens.dungeon_screen(
                content,
                character,
                city,
                level=dungeon_level(content, character, city.id),
                depth=state.descent.depth,
                total=turning_rules.descent_depth(character),
                notice=state.notice,
            )
        case ScreenId.STUB:
            return screens.stub_screen(state.stub_title, state.notice)
        case _:
            return screens.main_menu_screen(
                content, character, derived_stats(content, character), state.notice
            )


def list_sections(content: GameContent, screen: ScreenId) -> tuple[str, ...]:
    """По каким разделам режется список на этом экране. Пусто - не режется."""
    match screen:
        case ScreenId.INVENTORY | ScreenId.SHOP | ScreenId.SELL:
            return shop_screens.ITEM_SECTIONS
        case ScreenId.SKILLS:
            return skill_screens.SKILL_SECTIONS
        case _:
            return ()


def _search_and_filters(
    content: GameContent, state: PlayState, command: Command
) -> PlayState | None:
    """Поиск, разделы и сброс - одинаково на всех списках. ``None`` - не про них.

    Три кнопки под страницами раньше рисовались на каждом длинном списке и не
    делали ничего: «Поиск» не был намерением вообще, а «Фильтры» никто не
    разбирал. Разбор один и живёт здесь.
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


def _pick_kind(state: PlayState) -> SkillKind:
    return SkillKind.PASSIVE if state.pick_kind == "passive" else SkillKind.ACTIVE


def _owned_count(goods: Goods, item_id: str) -> int:
    return next((held.quantity for held in goods.owned if held.item_id == item_id), 0)


def _sale_prices(content: GameContent, owned: tuple[OwnedItem, ...]) -> dict[str, int]:
    return {held.item_id: economy.sell_price(content, content.item(held.item_id)) for held in owned}


# --- stepping ---------------------------------------------------------


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
    keeper: KeeperView | None = None,
    location_state: LocationState | None = None,
) -> PlayState:
    """Apply one message. Always answers; never raises on unexpected input."""
    # A visit that can no longer be rebuilt is dropped before anything reads it,
    # so the state written back is a state that works. Healing in ``render`` alone
    # would fix the answer and leave the stored screen broken for ever.
    if state.screen is ScreenId.LOCATION and not location_known(content, state.session):
        # The dead screen leaves the stack too, so "Назад" walks on to the city
        # instead of back into the place that is gone.
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
        # The buttons that were on screen belong to a place that is gone, so this
        # press only explains and hands over a working keyboard (rule 12) - except
        # for the service row, which means what it says everywhere.
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
        keeper=view,
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

    state = replace(state, pending=PendingWrite(), fight="")
    shelf = goods or Goods(gold=character.gold)
    ticking = clock or Clock()

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
        case ScreenId.TURNING:
            return _handle_turning(content, character, state, command)
        case ScreenId.CHAMBER_PLEDGE:
            return _handle_pledge(content, character, state, command)
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
            return _handle_main_menu(character, state, command, clock=ticking)
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
        case ScreenId.DUNGEON:
            return _handle_dungeon(content, character, state, command, clock=ticking)
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
                location_state=location_state or LocationState(),
                now=ticking.now,
            )
        case _:
            return state.with_notice("Нажмите «Назад» или «Главное меню».")


def mark_task(state: PlayState, character: Character, task: TutorialTask) -> PlayState:
    """Tick off an introduction task on whatever step happened to finish it.

    The step may already be storing a changed character - a purchase, a contract -
    so the mark goes on that one; otherwise on the character as loaded. A task
    already done changes nothing and says nothing.
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
    """One button, and it costs money: the stake is taken before the fight."""
    if command.intent is not Intent.SELECT or not arena_screens.ARENA_FIGHT.matches(
        command.argument
    ):
        return state.with_notice("Нажмите «Выйти на арену» или «Назад».")
    refused = arena_rules.refusal(character)
    if refused:
        return state.with_notice(refused)
    # The handler picks the opponent and takes the stake: it is the one that can
    # read another character out of storage.
    return replace(state, fight="arena").at(ScreenId.COMBAT)


def _handle_chamber(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Палата: две двери — заклад и голосование."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из списка или «Назад».")
    if labels.TURNING.matches(command.argument):
        refused = turning_rules.refusal(character)
        if refused:
            return state.with_notice(refused)
        return replace(state, list_page=PageState()).at(ScreenId.CHAMBER_PLEDGE)
    if labels.TURNING_QUESTION.matches(command.argument):
        return state.at(ScreenId.TURNING)
    return state.with_notice("Нажмите кнопку из списка или «Назад».")


def _handle_turning(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Голосование: по кнопке на ответ, и голос весит столько, сколько Печатей."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите ответ или «Назад».")
    turning = content.open_turning()
    if turning is None:
        return state.with_notice("Палата сейчас ни о чём не спрашивает.")
    for option in turning.options:
        if not chamber_screens.answer_label(option.name).matches(command.argument):
            continue
        voted = turning_rules.answer(character, turning, option.id)
        if voted is None:
            if not turning_rules.may_answer(character):
                return state.with_notice("Голос дают за перерождение: сперва Печать, потом ответ.")
            return state.with_notice(f"Ваш голос уже отдан за: {option.name}.")
        weight = turning_rules.voice(voted)
        return state.storing(PendingWrite(character=voted)).with_notice(
            f"Голос отдан за: {option.name}. Он весит {weight} "
            f"{format_screens.plural(weight, 'Печать', 'Печати', 'Печатей')}."
        )
    return state.with_notice("Нажмите ответ или «Назад».")


def _handle_pledge(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """Заклад. Нажатие здесь совершает перерождение: экран предупреждал об этом."""
    entries = chamber_screens.pledge_entries(content, character)
    moved = page_move(command, state.list_page, total_pages(len(entries)))
    if moved is not None:
        return replace(state, list_page=moved)
    if command.intent is not Intent.SELECT or not entries:
        return state.with_notice("Нажмите, что отдать, или «Назад».")

    key = chamber_screens.entry_for(content, character, command.argument)
    if not key:
        return state.with_notice("Нажмите, что отдать, или «Назад».")
    kind, _, entity_id = key.partition(":")
    if kind == turning_rules.ITEM_PLEDGE:
        sealed = turning_rules.pledge_item(content, character, entity_id)
    else:
        sealed = turning_rules.pledge_edge(content, character, entity_id)
    if sealed is None:
        return state.with_notice("Палата это уже не примет. Выберите другое.")
    return (
        state.storing(PendingWrite(character=sealed.character))
        .at(ScreenId.CHAMBER)
        .with_notice(chamber_screens.sealed_line(sealed))
    )


def _handle_tutorial(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    """One button: open the screen the current task is done on."""
    if command.intent is not Intent.SELECT or not tutorial_screens.DO_TASK.matches(
        command.argument
    ):
        return state.with_notice("Нажмите «Перейти к шагу» или «Назад».")
    task = tutorial_rules.next_task(character)
    if task is None:
        return state.with_notice("Все шаги сделаны.")

    card = tutorial_screens.card_for(task)
    city = known_city(content, state.city_id, character.city_id)
    # Every task but the first two happens somewhere in a city, so the walk there
    # is made for the player rather than described to them.
    needed = {
        ScreenId.QUEST_BOARD: "tavern",
        ScreenId.TAVERN: "tavern",
        ScreenId.SHOP: "shop",
        ScreenId.LOCATION_LIST: "locations",
    }.get(card.screen)
    if needed is not None and needed not in city.services:
        return state.with_notice(
            f"В городе {city.name} этого нет. Задание можно сделать в другом городе."
        )
    fresh = replace(
        state,
        city_id=city.id,
        list_page=PageState(),
        board_page=PageState(),
        location_page=PageState(),
    )
    opened = fresh.at(card.screen).with_notice(f"Задание: {card.title}. {card.text}")
    if card.screen is ScreenId.STATS:
        # Reading them *is* the task, and the screen is now open.
        return mark_task(opened, character, TutorialTask.STATS)
    return opened


# --- menu, world, city ------------------------------------------------


def _handle_main_menu(
    character: Character, state: PlayState, command: Command, *, clock: Clock
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из меню.")
    if labels.WORLD.matches(command.argument):
        return state.at(ScreenId.WORLD)
    if labels.CHARACTER.matches(command.argument):
        return state.at(ScreenId.CHARACTER)
    if labels.INVENTORY.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.INVENTORY)
    if labels.SKILLS.matches(command.argument):
        return replace(state, skill_page=PageState()).at(ScreenId.SKILLS)
    if labels.QUESTS.matches(command.argument):
        return state.at(ScreenId.QUESTS)
    if labels.CRAFTS.matches(command.argument):
        return replace(state, craft_moment=clock.now).at(ScreenId.CRAFTS)
    if labels.SETTINGS.matches(command.argument):
        return state.at(ScreenId.SETTINGS)
    if labels.TUTORIAL.matches(command.argument):
        return state.at(ScreenId.TUTORIAL)
    # Pressed from an older keyboard by somebody who is no longer a keeper, the
    # button is simply not a button any more.
    if labels.KEEPER.matches(command.argument) and character.is_admin:
        return state.at(ScreenId.KEEPER)
    return state.with_notice("Нажмите кнопку из меню.")


# --- crafts -----------------------------------------------------------


def _bag(goods: Goods) -> dict[str, int]:
    """The bag as the craft rules want it: item id to how many are in it."""
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
            chosen = replace(state, craft_id=craft.id, craft_moment=clock.now)
            return chosen.at(ScreenId.CRAFT)
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
    # ``render`` уже умеет отступить на список ремёсел, а разбор нажатия - ещё
    # нет: без этой строки нажатие на экране исчезнувшего ремесла падало
    # (``Claude.md``, правило 8: сохранённому состоянию не верят).
    if not content.has_craft(state.craft_id):
        return go_back(state).with_notice("Такого ремесла в игре больше нет.")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку работы или «Назад».")
    craft = content.craft(state.craft_id)

    if labels.GATHER.matches(command.argument):
        # The place is part of the seed as well as of the rules: the same watch of
        # work in two cities is two different pieces of work.
        here = known_city(content, state.city_id, character.city_id)
        seed = derive(world_seed, "gather", character.id, craft.id, here.id, clock.now)
        worked, gathered = craft_rules.gather(
            content,
            character,
            craft,
            now=clock.now,
            cooldown=clock.gather_cooldown,
            seed=seed,
            biomes=here.biomes,
        )
        line = craft_screens.gathered_line(content, gathered)
        if not gathered.ok:
            return state.with_notice(line)
        write = PendingWrite(character=worked).with_items((gathered.item_id, gathered.count))
        return replace(state, craft_moment=clock.now).storing(write).with_notice(line)

    owned = _bag(goods)
    for recipe in content.recipes_of(craft.id):
        if not command.argument.startswith(content.item(recipe.output_id).name):
            continue
        # The work already done is part of the seed, so two batches in a row are
        # not the same batch twice.
        experience = character.crafts.progress(craft.id).experience
        seed = derive(world_seed, "craft", character.id, recipe.id, clock.now, experience)
        worked, made = craft_rules.make(content, character, recipe, owned, seed=seed)
        line = craft_screens.made_line(content, made)
        if not made.ok:
            return state.with_notice(line)
        # Somebody in a city may be waiting for exactly this thing: a contract for
        # made goods counts here, where the work happens.
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
    # The keyboard the keeper is answering lists every city, so the step that
    # reads it must agree with it (screens.world_screen).
    available = (
        content.cities if character.is_admin else content.cities_available_at(character.level)
    )
    moved = page_move(command, state.world_page, total_pages(len(available)))
    if moved is not None:
        return replace(state, world_page=moved, notice="")

    if command.intent is Intent.SELECT:
        for city in available:
            if city.name == command.argument:
                return replace(state, city_id=city.id).at(ScreenId.CITY)

    # A locked city can still arrive here - typed, or pressed from an older
    # keyboard - so it gets a real explanation rather than a generic refusal.
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
        return replace(state, stub_title=command.argument).at(ScreenId.STUB)
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
    """One point, one press. The screen it answers on says what the point bought."""
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
    moved = page_move(command, state.list_page, total_pages(len(shown)))
    if moved is not None:
        return replace(state, list_page=moved, notice="")
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
    moved = page_move(command, state.list_page, total_pages(len(shown)))
    if moved is not None:
        return replace(state, list_page=moved, notice="")
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
    moved = page_move(command, state.list_page, total_pages(len(shown)))
    if moved is not None:
        return replace(state, list_page=moved, notice="")
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите предмет из списка.")

    item = shop_screens.owned_from_button(content, command.argument, shown)
    if item is None:
        return state.with_notice("Нажмите предмет из списка.")
    # Нажатие открывает карточку вещи, а не действует ею. Раньше оно сразу
    # надевало или выпивало, а сырьё в ответ читало описание - и про волчью
    # шкуру игра не могла сказать ничего (``screens/items.py``).
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
            return state.with_notice(f"{item.name} сейчас ничего не даст. {item.text}")
        write = PendingWrite(character=healed_character, items=((item.id, -1),))
        return (
            go_back(replace(state, item_id=""))
            .storing(write)
            .with_notice(f"{item.name} выпито, восстановлено {healed} здоровья.")
        )
    return state.with_notice("Нажмите действие или «Назад».")


def _equip(content: GameContent, character: Character, state: PlayState, item: Item) -> PlayState:
    """Put a thing on. What it replaces goes back into the bag, never nowhere."""
    previous = character.equipment.item_in(item.slot)
    dressed = replace(character, equipment=character.equipment.equip(item.slot, item.id))
    write = PendingWrite(character=dressed, items=((item.id, -1),))
    if previous is not None and content.has_item(previous):
        write = write.with_items((previous, 1))
        said = f"{item.name} надет, {content.item(previous).name} убран в сумку."
    else:
        said = f"{item.name} надет."
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
    # The handler persists it; the flow only decides what should change.
    return state.storing(PendingWrite(settings=updated)).with_notice(said)


# --- skills -----------------------------------------------------------


def _handle_skills(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    pool = skill_screens.matching_skills(
        content, character, skill_rules.teachable(content, character), state.skill_page
    )
    moved = page_move(command, state.skill_page, total_pages(len(pool)))
    if moved is not None:
        return replace(state, skill_page=moved, notice="")
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
            if character.unspent_skill_points < 1:
                return state.with_notice(
                    "Очков умений нет. Их дают за уровень и возвращает наставник."
                )
            return state.with_notice(f"{skill.name} уже на высшем ранге.")
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
    rules = content.rules
    for kind, count in (
        (SkillKind.ACTIVE, rules.active_slots),
        (SkillKind.PASSIVE, rules.passive_slots),
    ):
        for slot in range(count):
            if skill_screens.slot_label(content, character, kind, slot).matches(command.argument):
                return replace(
                    state,
                    pick_kind=kind.value,
                    pick_slot=slot,
                    list_page=PageState(),
                ).at(ScreenId.SKILL_PICK)
    return state.with_notice("Нажмите слот из списка.")


def _handle_pick(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    kind = _pick_kind(state)
    available = skill_rules.equippable(content, character, kind)
    moved = page_move(command, state.list_page, total_pages(len(available)))
    if moved is not None:
        return replace(state, list_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")

    if skill_screens.CLEAR_SLOT.matches(command.argument):
        emptied = skill_rules.put_in_slot(content, character, kind, state.pick_slot, None)
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
        filled = skill_rules.put_in_slot(content, character, kind, state.pick_slot, skill.code)
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


# --- city services ----------------------------------------------------


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


def _hand_in(content: GameContent, character: Character, state: PlayState) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    due = quest_rules.ready_to_hand_in(content, character, city.id)
    if not due:
        return state.with_notice("Сдавать нечего: ни одно задание не досчитано.")
    payout = quest_rules.hand_in(content, character, due[0].quest)
    if payout is None:  # pragma: no cover - ready_to_hand_in already checked it
        return state.with_notice("Сдавать нечего.")

    write = PendingWrite(character=payout.character).because(economy_log.QUEST)
    said = (
        f"Задание «{payout.quest.name}» закрыто. "
        f"Плата: {payout.gold} золота и {payout.experience} опыта."
    )
    if payout.item_id and content.has_item(payout.item_id):
        write = write.with_items((payout.item_id, 1))
        said += f" Сверху дали: {content.item(payout.item_id).name}."
    if payout.level_up.levels_gained:
        said += f" {level_up_line(payout.level_up)}"
    return mark_task(state.storing(write).with_notice(said), character, TutorialTask.HAND_IN)


def _handle_board(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    offered = quest_rules.available(content, character, city.id)
    working = tuple(
        step for step in quest_rules.taken(content, character) if step.quest.city_id == city.id
    )
    moved = page_move(command, state.board_page, total_pages(len(offered) + len(working)))
    if moved is not None:
        return replace(state, board_page=moved, notice="")
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
            f"Палата берёт своё с торговли, а не с задания."
        )
    if labels.QUEST_LEAVE.matches(command.argument):
        # Refusing never closes a contract for good (Narrative.md, section 4).
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
    known = sorted(code for code in skill_rules.known_codes(character) if content.has_skill(code))
    moved = page_move(command, state.mentor_page, total_pages(len(known)))
    if moved is not None:
        return replace(state, mentor_page=moved, notice="")
    listed = _search_and_filters(content, state, command)
    if listed is not None:
        return listed
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")

    price = economy.mentor_price(character.level)
    for code in known:
        skill = content.skill(code)
        rank = character.loadout.rank_of(code)
        if not city_screens.forget_label(skill.name, rank).matches(command.argument):
            continue
        if character.gold < price:
            return state.with_notice(f"Наставник берёт {price} золота, у вас {character.gold}.")
        forgotten = skill_rules.forget(content, character, skill)
        if forgotten is None:  # pragma: no cover - the list only holds known skills
            return state.with_notice("Это умение вы не изучали.")
        paid = forgotten.with_gold(-price)
        return state.storing(PendingWrite(character=paid).because(economy_log.SERVICE)).with_notice(
            f"{skill.name} забыто. Вернулось очков: {rank}. Заплачено {price} золота."
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
    *,
    clock: Clock,
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите «Спуститься» или «Назад».")
    if not labels.DUNGEON_ENTER.matches(command.argument):
        return state.with_notice("Нажмите «Спуститься» или «Назад».")

    city = known_city(content, state.city_id, character.city_id)
    descent = Descent(
        city_id=city.id,
        level=dungeon_level(content, character, city.id),
        depth=1,
        started_at=clock.now,
    )
    return replace(state, descent=descent, fight="dungeon").at(ScreenId.COMBAT)


# --- locations --------------------------------------------------------


def _handle_location_list(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
) -> PlayState:
    city = known_city(content, state.city_id, character.city_id)
    moved = page_move(command, state.location_page, total_pages(len(city.locations)))
    if moved is not None:
        return replace(state, location_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите локацию из списка.")

    for location in city.locations:
        if command.argument.startswith(f"{location.slot}. {location.name}"):
            if character.level < location.level_min and not character.is_admin:
                return state.with_notice(
                    f"Локация {location.name} рассчитана на уровни с {location.level_min} "
                    f"по {location.level_max}. Ваш уровень: {character.level}."
                )
            # What is left in the nodes is shared by everybody in the place, so
            # the handler reads it from the cache before the screen is drawn; the
            # flow only says which location was entered and where the player is.
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
    location_state: LocationState,
    now: int,
) -> PlayState:
    if not location_known(content, state.session):
        return (
            replace(state, session=LocationSession())
            .at(ScreenId.LOCATION_LIST)
            .with_notice(LOST_VISIT)
        )
    location = build_location(content, world_seed, state.session)
    node = location.node(state.session.node)

    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите переход или действие узла.")

    if screens.LEAVE_LOCATION.matches(command.argument) or (
        node.kind is NodeKind.EXIT and command.argument == screens.NODE_ACTIONS[NodeKind.EXIT]
    ):
        return (
            replace(state, session=LocationSession())
            .at(ScreenId.LOCATION_LIST)
            .with_notice(f"Вы покинули локацию {location.name}.")
        )

    for person in neighbours:
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
        # The handler owns the fight: it is the one that can read the other
        # character out of storage and take the snapshot.
        return replace(state, fight=f"pvp:{person.character_id}").at(ScreenId.COMBAT)

    for neighbour in (location.node(index) for index in node.links):
        if screens.node_button(neighbour).matches(command.argument):
            return replace(state, session=replace(state.session, node=neighbour.index))

    if command.argument == screens.NODE_ACTIONS[node.kind]:
        return _resolve_node_action(
            content, character, state, location, world_seed, location_state, now
        )

    return state.with_notice("Не узнал это действие. Нажмите кнопку узла.")


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
    """Non-combat nodes resolve immediately; combat is handed to the fight screen."""
    index = state.session.node
    node = location.node(index)

    if node.kind in {NodeKind.ENTRANCE, NodeKind.EXIT}:
        # A door pays nobody: looking at it is an answer, not a reward.
        return state.with_notice(
            f"{node.name}: смотреть здесь не на что, кроме дороги в обе стороны."
        )

    left = node_rules.standing_at(
        visit_seed(world_seed, state.session), location, location_state, index, now
    )
    if left.empty:
        return state.with_notice(_empty_node_line(node.name))

    if node.kind.is_combat:
        # The handler builds the fight itself: it owns the enemy generation and
        # the cache the fight lives in.
        return replace(state, fight="node").at(ScreenId.COMBAT)

    # The wave is part of the seed, so the second handful out of the same vein is
    # not the first one all over again.
    seed = derive(visit_seed(world_seed, state.session), "search", index, left.wave, left.taken)
    result = adventure.resolve_search(content, character, node, seed)
    write = PendingWrite(character=result.character, node_take=index)
    if result.item_id:
        write = write.with_items((result.item_id, 1))

    said = search_line(content, node.name, result)
    # Сколько осталось, экран скажет строкой ниже своими словами - здесь это
    # было бы второй раз подряд об одном и том же.
    if left.left <= 1:
        said = f"{said} Узел вычищен."
    return state.storing(write).with_notice(said)


def search_line(content: GameContent, node_name: str, result: adventure.SearchResult) -> str:
    """One sentence about a node worked through, then what it gave."""
    parts = [f"{node_name}: сделано."]
    if result.gold:
        parts.append(f"Найдено золота: {result.gold}.")
    if result.item_id and content.has_item(result.item_id):
        parts.append(f"Взято: {content.item(result.item_id).name}.")
    if result.healed:
        parts.append(f"Восстановлено здоровья: {result.healed}.")
    parts.append(f"Опыт: {result.experience}.")
    for step in result.quest_steps:
        parts.append(f"Задание «{step.quest.name}»: {step.progress} из {step.quest.target_count}.")
    if result.level_up is not None and result.level_up.levels_gained:
        parts.append(level_up_line(result.level_up))
    return " ".join(parts)


def level_up_line(level_up: LevelUp) -> str:
    """The one sentence a level is announced with, wherever it happened."""
    return (
        f"Уровень {level_up.new_level}. Очков характеристик: {level_up.stat_points}, "
        f"очков умений: {level_up.skill_points}."
    )
