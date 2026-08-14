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
from mmorpg.domain.entities.content import City, GameContent, Item
from mmorpg.domain.entities.location import GeneratedLocation, NodeKind
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.domain.procgen.dungeons import Dungeon, dungeon_floor, roll_dungeons
from mmorpg.domain.procgen.location import cleared_mask, generate_location, is_cleared
from mmorpg.domain.rules.bank import (
    Transfer,
    TransferKind,
    VaultRefusal,
    apply_transfer,
    plan_deposit,
    plan_withdrawal,
)
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.domain.rules.tavern import Rumour, roll_rumours
from mmorpg.domain.rules.training import train_stat
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Command, Intent, resolve
from mmorpg.presentation.telegram.screens import play as screens
from mmorpg.presentation.telegram.screens import services as service_screens
from mmorpg.presentation.telegram.screens import settings as settings_screens
from mmorpg.presentation.telegram.screens import shop as shop_screens
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import gold as gold_words
from mmorpg.presentation.telegram.screens.paginated import PageState, total_pages
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import NavigationStack, back_target

# Which city screen button opens which section.
CITY_SECTIONS: dict[str, ScreenId] = {
    labels.LOCATIONS.text: ScreenId.LOCATION_LIST,
    labels.SHOP.text: ScreenId.SHOP,
    labels.DUNGEONS.text: ScreenId.DUNGEONS,
    labels.TAVERN.text: ScreenId.TAVERN,
    labels.MENTOR.text: ScreenId.MENTOR,
    labels.BANK.text: ScreenId.BANK,
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
    notice: str = ""
    list_page: PageState = field(default_factory=PageState)
    # Set when the player pressed "buy": the handler performs the purchase, since
    # writing to the database is not the flow's job. Same for a settings switch,
    # for a point placed at the mentor and for gold moved at the vault.
    pending_purchase: str = ""
    pending_settings: AccessibilitySettings | None = None
    pending_stat: str = ""
    pending_transfer: Transfer | None = None

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
            },
            ensure_ascii=False,
        )

    @classmethod
    def deserialise(cls, raw: str) -> PlayState:
        data = json.loads(raw)
        city_id, slot, cycle, node, cleared = data.get("session", ["", 0, 0, 0, 0])
        # A screen that no longer exists - a section rebuilt between releases -
        # puts the player in the main menu rather than raising at them.
        stored = data.get("screen", "")
        known = {item.value for item in ScreenId}
        screen = ScreenId(stored) if stored in known else ScreenId.MAIN_MENU
        return cls(
            screen=screen,
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


def city_of(content: GameContent, character: Character, state: PlayState) -> City:
    return content.city(state.city_id or character.city_id)


def rumours_of(
    content: GameContent, character: Character, state: PlayState, *, world_seed: str, cycle: int
) -> tuple[Rumour, ...]:
    """The watch summary the tavern is repeating right now."""
    return roll_rumours(
        world_seed=world_seed,
        city=city_of(content, character, state),
        cycle=cycle,
        level=character.level,
    )


def dungeons_of(
    content: GameContent, character: Character, state: PlayState, *, world_seed: str
) -> tuple[Dungeon, ...]:
    """The runs under the city. They do not rotate with the watch."""
    city = city_of(content, character, state)
    return roll_dungeons(
        world_seed=world_seed,
        city_id=city.id,
        level_min=city.level_min,
        level_max=city.level_max,
    )


def render(
    content: GameContent,
    character: Character,
    state: PlayState,
    *,
    world_seed: str,
    cycle: int = 0,
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
        case ScreenId.SKILLS:
            return skill_screens.skills_screen(content, character, state.list_page, state.notice)
        case ScreenId.TAVERN:
            return service_screens.tavern_screen(
                city_of(content, character, state),
                rumours_of(content, character, state, world_seed=world_seed, cycle=cycle),
                state.notice,
            )
        case ScreenId.MENTOR:
            return service_screens.mentor_screen(
                content, city_of(content, character, state), character, state.notice
            )
        case ScreenId.BANK:
            return service_screens.bank_screen(
                city_of(content, character, state), character, state.notice
            )
        case ScreenId.DUNGEONS:
            return service_screens.dungeons_screen(
                city_of(content, character, state),
                dungeons_of(content, character, state, world_seed=world_seed),
                character,
                state.notice,
            )
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
        content,
        character,
        state,
        world_seed=world_seed,
        cycle=cycle,
        goods=goods,
        settings=settings,
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

    state = replace(
        state, pending_purchase="", pending_settings=None, pending_stat="", pending_transfer=None
    )

    match state.screen:
        case ScreenId.SETTINGS:
            return _handle_settings(state, command, settings or DEFAULT_SETTINGS)
        case ScreenId.INVENTORY | ScreenId.SHOP:
            return _handle_goods(content, state, command, goods or Goods(gold=character.gold))
        case ScreenId.MAIN_MENU:
            return _handle_main_menu(state, command)
        case ScreenId.CHARACTER:
            return _handle_character(state, command)
        case ScreenId.SKILLS:
            return _handle_skills(content, character, state, command)
        case ScreenId.WORLD:
            return _handle_world(content, character, state, command)
        case ScreenId.CITY:
            return _handle_city(state, command)
        case ScreenId.TAVERN:
            return _handle_tavern(
                content, character, state, command, world_seed=world_seed, cycle=cycle
            )
        case ScreenId.MENTOR:
            return _handle_mentor(content, character, state, command)
        case ScreenId.BANK:
            return _handle_bank(character, state, command)
        case ScreenId.DUNGEONS:
            return _handle_dungeons(content, character, state, command, world_seed=world_seed)
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


def _handle_character(state: PlayState, command: Command) -> PlayState:
    if command.intent is Intent.SELECT and labels.SKILLS.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.SKILLS)
    return state.with_notice("Нажмите «Умения», «Назад» или «Главное меню».")


def _handle_skills(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    pages = total_pages(len(skill_screens.known_skills(content, character)))
    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return replace(state, list_page=state.list_page.moved(delta, pages), notice="")
    if command.intent is Intent.PAGE and command.number is not None:
        return replace(state, list_page=state.list_page.jumped(command.number, pages), notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите умение из списка.")

    skill = skill_screens.skill_from_button(content, character, command.argument)
    if skill is None:
        return state.with_notice("Нажмите умение из списка.")
    return state.with_notice(skill_screens.describe_skill(content, character, skill))


def _handle_tavern(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    world_seed: str,
    cycle: int,
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите строку сводки, чтобы расспросить хозяина.")

    rumours = rumours_of(content, character, state, world_seed=world_seed, cycle=cycle)
    rumour = service_screens.rumour_from_button(command.argument, rumours)
    if rumour is None:
        return state.with_notice("Нажмите строку сводки, чтобы расспросить хозяина.")
    return state.with_notice(service_screens.describe_rumour(rumour))


def _handle_mentor(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите характеристику из списка.")

    code = service_screens.stat_from_button(command.argument)
    if code is None:
        return state.with_notice("Нажмите характеристику из списка.")

    trained = train_stat(character, code)
    if trained is None:
        return state.with_notice("Свободных очков нет: они приходят с уровнем.")
    # The flow decides, the handler writes: the sentence below describes exactly
    # what the handler will apply, because both go through ``train_stat``.
    return replace(state, pending_stat=StatCode(code).value).with_notice(
        service_screens.trained_line(content, trained, code)
    )


def _handle_bank(character: Character, state: PlayState, command: Command) -> PlayState:
    parsed = service_screens.parse_transfer(command.argument, character)
    if parsed is None:
        return state.with_notice("Нажмите сумму или напишите «положить 100», «снять 100».")

    kind, amount = parsed
    plan = (
        plan_deposit(character, amount)
        if kind is TransferKind.DEPOSIT
        else plan_withdrawal(character, amount)
    )
    if isinstance(plan, VaultRefusal):
        return state.with_notice(service_screens.VAULT_REFUSALS[plan])

    after = apply_transfer(character, plan)
    if plan.kind is TransferKind.DEPOSIT:
        said = f"Принято {gold_words(plan.amount)}, пошлина {gold_words(plan.fee)}."
    else:
        said = f"Выдано {gold_words(plan.amount)}."
    return replace(state, pending_transfer=plan).with_notice(
        f"{said} В хранилище {gold_words(after.bank_gold)}, на руках {gold_words(after.gold)}."
    )


def _handle_dungeons(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    world_seed: str,
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите ход из списка, чтобы осмотреть вход.")

    dungeons = dungeons_of(content, character, state, world_seed=world_seed)
    dungeon = service_screens.dungeon_from_button(command.argument, dungeons)
    if dungeon is None:
        return state.with_notice("Нажмите ход из списка, чтобы осмотреть вход.")

    floor = dungeon_floor(world_seed=world_seed, dungeon=dungeon, floor=1)
    return state.with_notice(service_screens.describe_entrance(dungeon, floor))


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
    if labels.SKILLS.matches(command.argument):
        return replace(state, list_page=PageState()).at(ScreenId.SKILLS)
    return state.with_notice("Нажмите кнопку из меню.")


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

    target = CITY_SECTIONS.get(command.argument)
    if target is None:
        return state.with_notice("Нажмите кнопку города.")
    # The shop is a list and opens on its first page, like every other list.
    if target is ScreenId.SHOP:
        return replace(state, list_page=PageState()).at(target)
    return state.at(target)


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
    return state.with_notice("Не узнал локацию. Нажмите локацию из списка.")


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

    return state.with_notice("Не узнал действие. Нажмите кнопку узла.")


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
