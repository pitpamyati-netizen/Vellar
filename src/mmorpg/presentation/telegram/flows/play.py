"""Navigation between menu, world, city and locations, as a pure state machine.

Same shape as the creation flow: ``advance(state, message) -> state``, no I/O and
no clock. The current world cycle arrives as an argument, which is what keeps
location generation reproducible in tests (``docs/procgen.md``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.entities.location import GeneratedLocation, NodeKind
from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.domain.procgen.location import cleared_mask, generate_location, is_cleared
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Command, Intent, resolve
from mmorpg.presentation.telegram.screens import play as screens
from mmorpg.presentation.telegram.screens import settings as settings_screens
from mmorpg.presentation.telegram.screens import shop as shop_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import PageState, total_pages
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import NavigationStack, back_target

# Sections that exist as screens but have no content yet. Each one is a real
# screen with a working "Назад" - never silence.
STUBS: dict[str, str] = {
    labels.DUNGEONS.text: "Данжи",
    labels.TAVERN.text: "Таверна",
    labels.MENTOR.text: "Наставник",
    labels.BANK.text: "Банк",
    labels.SKILLS.text: "Умения",
}

DEFAULT_SETTINGS = AccessibilitySettings()


@dataclass(frozen=True, slots=True)
class Goods:
    """What the player owns and what the current city sells.

    Passed in from the handler: the flow itself never touches a repository.
    """

    gold: int = 0
    owned: tuple[OwnedItem, ...] = ()
    stock: tuple[Item, ...] = ()
    prices: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocationSession:
    """A visit to one location.

    The cycle index is captured on entry and kept until the player leaves, so the
    map never changes under their feet mid-visit (docs/adr/0003).
    """

    city_id: str = ""
    slot: int = 0
    cycle: int = 0
    node: int = 0
    cleared: int = 0

    @property
    def active(self) -> bool:
        return bool(self.city_id and self.slot)


@dataclass(frozen=True, slots=True)
class PlayState:
    screen: ScreenId = ScreenId.MAIN_MENU
    stack: NavigationStack = field(default_factory=lambda: NavigationStack((ScreenId.MAIN_MENU,)))
    world_page: PageState = field(default_factory=PageState)
    location_page: PageState = field(default_factory=PageState)
    city_id: str = ""
    session: LocationSession = field(default_factory=LocationSession)
    stub_title: str = ""
    notice: str = ""
    list_page: PageState = field(default_factory=PageState)
    # Set when the player pressed "buy": the handler performs the purchase, since
    # writing to the database is not the flow's job. Same for a settings switch.
    pending_purchase: str = ""
    pending_settings: AccessibilitySettings | None = None

    def at(self, screen: ScreenId) -> PlayState:
        return replace(self, screen=screen, stack=self.stack.push(screen), notice="")

    def with_notice(self, notice: str) -> PlayState:
        return replace(self, notice=notice)

    def serialise(self) -> str:
        return json.dumps(
            {
                "screen": self.screen.value,
                "stack": self.stack.serialise(),
                "world_page": self.world_page.page,
                "location_page": self.location_page.page,
                "city": self.city_id,
                "session": [
                    self.session.city_id,
                    self.session.slot,
                    self.session.cycle,
                    self.session.node,
                    self.session.cleared,
                ],
                "stub": self.stub_title,
            },
            ensure_ascii=False,
        )

    @classmethod
    def deserialise(cls, raw: str) -> PlayState:
        data = json.loads(raw)
        city_id, slot, cycle, node, cleared = data.get("session", ["", 0, 0, 0, 0])
        return cls(
            screen=ScreenId(data["screen"]),
            stack=NavigationStack.deserialise(data.get("stack", "")),
            world_page=PageState(page=int(data.get("world_page", 1))),
            location_page=PageState(page=int(data.get("location_page", 1))),
            city_id=data.get("city", ""),
            session=LocationSession(
                city_id=city_id,
                slot=int(slot),
                cycle=int(cycle),
                node=int(node),
                cleared=int(cleared),
            ),
            stub_title=data.get("stub", ""),
        )


def begin(character: Character) -> PlayState:
    return PlayState(city_id=character.city_id)


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
        cycle=session.cycle,
        name=location.name,
        biome=location.biome,
        level_min=location.level_min,
        level_max=location.level_max,
    )


def render(
    content: GameContent,
    character: Character,
    state: PlayState,
    *,
    world_seed: str,
    goods: Goods | None = None,
    settings: AccessibilitySettings | None = None,
) -> Screen:
    shelf = goods or Goods(gold=character.gold)
    match state.screen:
        case ScreenId.SETTINGS:
            return settings_screens.settings_screen(settings or DEFAULT_SETTINGS, state.notice)
        case ScreenId.INVENTORY:
            return shop_screens.inventory_screen(
                content, shelf.owned, state.list_page, gold=shelf.gold, notice=state.notice
            )
        case ScreenId.SHOP:
            city = content.city(state.city_id or character.city_id)
            return shop_screens.shop_screen(
                content,
                shelf.stock,
                dict(shelf.prices),
                state.list_page,
                gold=shelf.gold,
                city_name=city.name,
                notice=state.notice,
            )
        case ScreenId.WORLD:
            return screens.world_screen(content, character, state.world_page, state.notice)
        case ScreenId.CITY:
            city = content.city(state.city_id or character.city_id)
            return screens.city_screen(content, city, character, state.notice)
        case ScreenId.LOCATION_LIST:
            city = content.city(state.city_id or character.city_id)
            return screens.location_list_screen(
                content, city, character, state.location_page, state.notice
            )
        case ScreenId.LOCATION:
            location = build_location(content, world_seed, state.session)
            return screens.location_screen(
                location,
                location.node(state.session.node),
                cleared=state.session.cleared,
                notice=state.notice,
            )
        case ScreenId.CHARACTER:
            return screens.character_screen(
                content, character, derived_stats(content, character), state.notice
            )
        case ScreenId.STUB:
            return screens.stub_screen(state.stub_title, state.notice)
        case _:
            return screens.main_menu_screen(
                content, character, derived_stats(content, character), state.notice
            )


def advance(
    content: GameContent,
    character: Character,
    state: PlayState,
    text: str,
    *,
    cycle: int,
    world_seed: str,
    goods: Goods | None = None,
    settings: AccessibilitySettings | None = None,
) -> PlayState:
    """Apply one message. Always answers; never raises on unexpected input."""
    screen = render(
        content, character, state, world_seed=world_seed, goods=goods, settings=settings
    )
    command = resolve(text, screen)

    if command.intent is Intent.LOOK:
        return replace(state, notice="")
    if command.intent is Intent.MAIN_MENU:
        return replace(
            state,
            screen=ScreenId.MAIN_MENU,
            stack=NavigationStack((ScreenId.MAIN_MENU,)),
            notice="",
        )
    if command.intent is Intent.BACK:
        return _go_back(state)

    state = replace(state, pending_purchase="", pending_settings=None)

    match state.screen:
        case ScreenId.SETTINGS:
            return _handle_settings(state, command, settings or DEFAULT_SETTINGS)
        case ScreenId.INVENTORY | ScreenId.SHOP:
            return _handle_goods(content, state, command, goods or Goods(gold=character.gold))
        case ScreenId.MAIN_MENU:
            return _handle_main_menu(state, command)
        case ScreenId.WORLD:
            return _handle_world(content, character, state, command)
        case ScreenId.CITY:
            return _handle_city(state, command)
        case ScreenId.LOCATION_LIST:
            return _handle_location_list(content, character, state, command, cycle=cycle)
        case ScreenId.LOCATION:
            return _handle_location(content, state, command, world_seed=world_seed)
        case _:
            return state.with_notice("Нажмите «Назад» или «Главное меню».")


def _go_back(state: PlayState) -> PlayState:
    stack, previous = state.stack.pop()
    if previous is None:
        target = back_target(state.screen) or ScreenId.MAIN_MENU
        return replace(state, screen=target, stack=NavigationStack((target,)), notice="")
    leaving_location = state.screen is ScreenId.LOCATION
    return replace(
        state,
        screen=previous,
        stack=stack,
        notice="",
        session=LocationSession() if leaving_location else state.session,
    )


def _stub_for(state: PlayState, command: Command) -> PlayState | None:
    title = STUBS.get(command.argument)
    if title is None:
        return None
    return replace(state, stub_title=title).at(ScreenId.STUB)


def _handle_goods(
    content: GameContent, state: PlayState, command: Command, goods: Goods
) -> PlayState:
    """Inventory and shop share their paging, filtering and selection behaviour."""
    entries = len(goods.stock if state.screen is ScreenId.SHOP else goods.owned)
    pages = total_pages(entries)

    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, list_page=state.list_page.moved(delta, pages), notice="")
    if command.intent is Intent.PAGE and command.number is not None:
        return replace(state, list_page=state.list_page.jumped(command.number, pages), notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите позицию из списка.")

    if labels.RESET_FILTERS.matches(command.argument):
        return replace(
            state, list_page=PageState(filters=state.list_page.filters.cleared()), notice=""
        )

    if state.screen is ScreenId.SHOP:
        item = shop_screens.item_from_button(content, command.argument, goods.stock)
        if item is None:
            return state.with_notice("Нажмите товар из списка.")
        price = goods.prices.get(item.id, item.price)
        if price > goods.gold:
            return state.with_notice(
                f"{item.name} стоит {price} золота, у вас {goods.gold}. Не хватает."
            )
        return replace(state, pending_purchase=item.id).with_notice(
            f"{item.name} куплен за {price} золота."
        )

    owned = shop_screens.owned_from_button(content, command.argument, goods.owned)
    if owned is None:
        return state.with_notice("Нажмите предмет из списка.")
    return state.with_notice(f"{owned.name}. {owned.text}")


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
    return replace(state, pending_settings=updated).with_notice(said)


def _handle_main_menu(state: PlayState, command: Command) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку из меню.")
    if labels.WORLD.matches(command.argument):
        return state.at(ScreenId.WORLD)
    if labels.CHARACTER.matches(command.argument):
        return state.at(ScreenId.CHARACTER)
    if labels.INVENTORY.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.INVENTORY)
    if labels.SETTINGS.matches(command.argument):
        return state.at(ScreenId.SETTINGS)
    stub = _stub_for(state, command)
    return stub if stub is not None else state.with_notice("Нажмите кнопку из меню.")


def _handle_world(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    available = content.cities_available_at(character.level)
    pages = total_pages(len(available))
    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, world_page=state.world_page.moved(delta, pages), notice="")
    if command.intent is Intent.PAGE and command.number is not None:
        return replace(state, world_page=state.world_page.jumped(command.number, pages), notice="")

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


def _handle_city(state: PlayState, command: Command) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите кнопку города.")
    if labels.LOCATIONS.matches(command.argument):
        return state.at(ScreenId.LOCATION_LIST)
    if labels.SHOP.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.SHOP)
    stub = _stub_for(state, command)
    return stub if stub is not None else state.with_notice("Нажмите кнопку города.")


def _handle_location_list(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    cycle: int,
) -> PlayState:
    city = content.city(state.city_id or character.city_id)
    pages = total_pages(len(city.locations))
    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, location_page=state.location_page.moved(delta, pages), notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите локацию из списка.")

    for location in city.locations:
        if command.argument.startswith(f"{location.slot}. {location.name}"):
            if character.level < location.level_min:
                return state.with_notice(
                    f"Локация {location.name} рассчитана на уровни с {location.level_min} "
                    f"по {location.level_max}. Ваш уровень: {character.level}."
                )
            session = LocationSession(
                city_id=city.id, slot=location.slot, cycle=cycle, node=0, cleared=0
            )
            return replace(state, session=session).at(ScreenId.LOCATION)
    return state.with_notice("Не узнал эту локацию. Нажмите локацию из списка.")


def _handle_location(
    content: GameContent, state: PlayState, command: Command, *, world_seed: str
) -> PlayState:
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

    for neighbour in (location.node(index) for index in node.links):
        if screens.node_button(neighbour).matches(command.argument):
            return replace(state, session=replace(state.session, node=neighbour.index))

    if command.argument == screens.NODE_ACTIONS[node.kind]:
        return _resolve_node_action(state, location, node.index)

    return state.with_notice("Не узнал это действие. Нажмите кнопку узла.")


def _resolve_node_action(state: PlayState, location: GeneratedLocation, index: int) -> PlayState:
    """Non-combat nodes resolve immediately; combat is handed to the fight screen."""
    node = location.node(index)
    if node.kind.is_combat:
        # Wired to the combat engine by the combat handler.
        return replace(state, screen=ScreenId.COMBAT, stack=state.stack.push(ScreenId.COMBAT))

    if is_cleared(state.session.cleared, index):
        return state.with_notice(f"Узел {index} уже пройден, здесь больше ничего нет.")

    cleared = state.session.cleared | cleared_mask([index])
    session = replace(state.session, cleared=cleared)
    return replace(state, session=session).with_notice(
        f"Узел {index}: {node.name} — сделано. Отметка сохранится до конца стражи."
    )
