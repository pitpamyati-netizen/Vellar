"""FSM states and the back-navigation stack.

Character creation is a linear walk with a real "back" at every step, including
the first one (spec section 12). The stack of visited steps lives in FSM data, not
in heuristics: going back pops one entry and every choice already made is kept.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.fsm.state import State, StatesGroup

from mmorpg.presentation.telegram.screens.base import ScreenId


class Creation(StatesGroup):
    """The character creation flow."""

    name = State()
    race = State()
    race_details = State()
    character_class = State()
    class_details = State()
    traits = State()
    points = State()
    confirm = State()


class Play(StatesGroup):
    """Everything after a character exists."""

    main_menu = State()
    world = State()
    city = State()
    location_list = State()
    location = State()
    combat = State()
    combat_bag = State()
    inventory = State()
    shop = State()
    sell = State()
    character = State()
    stats = State()
    skills = State()
    skill_slots = State()
    skill_pick = State()
    skill_edge = State()
    crafts = State()
    craft = State()
    quests = State()
    quest_board = State()
    quest_offer = State()
    tavern = State()
    mentor = State()
    bank = State()
    dungeon = State()
    arena = State()
    npcs = State()
    npc = State()
    settings = State()
    tutorial = State()
    keeper = State()
    keeper_content = State()
    keeper_list = State()
    keeper_entity = State()
    keeper_field = State()
    keeper_players = State()
    keeper_player = State()
    keeper_stats = State()
    keeper_service = State()
    stub = State()


# Which screen belongs to which state. The resolver uses this to tell the player
# where they actually are when they press a button from an old keyboard.
STATE_FOR_SCREEN: dict[ScreenId, State] = {
    ScreenId.CREATE_NAME: Creation.name,
    ScreenId.CREATE_RACE: Creation.race,
    ScreenId.CREATE_RACE_DETAILS: Creation.race_details,
    ScreenId.CREATE_CLASS: Creation.character_class,
    ScreenId.CREATE_CLASS_DETAILS: Creation.class_details,
    ScreenId.CREATE_TRAITS: Creation.traits,
    ScreenId.CREATE_POINTS: Creation.points,
    ScreenId.CREATE_CONFIRM: Creation.confirm,
    ScreenId.MAIN_MENU: Play.main_menu,
    ScreenId.WORLD: Play.world,
    ScreenId.CITY: Play.city,
    ScreenId.LOCATION_LIST: Play.location_list,
    ScreenId.LOCATION: Play.location,
    ScreenId.COMBAT: Play.combat,
    ScreenId.COMBAT_BAG: Play.combat_bag,
    ScreenId.INVENTORY: Play.inventory,
    ScreenId.SHOP: Play.shop,
    ScreenId.SELL: Play.sell,
    ScreenId.CHARACTER: Play.character,
    ScreenId.STATS: Play.stats,
    ScreenId.SKILLS: Play.skills,
    ScreenId.SKILL_SLOTS: Play.skill_slots,
    ScreenId.SKILL_PICK: Play.skill_pick,
    ScreenId.SKILL_EDGE: Play.skill_edge,
    ScreenId.CRAFTS: Play.crafts,
    ScreenId.CRAFT: Play.craft,
    ScreenId.QUESTS: Play.quests,
    ScreenId.QUEST_BOARD: Play.quest_board,
    ScreenId.QUEST_OFFER: Play.quest_offer,
    ScreenId.TAVERN: Play.tavern,
    ScreenId.MENTOR: Play.mentor,
    ScreenId.BANK: Play.bank,
    ScreenId.DUNGEON: Play.dungeon,
    ScreenId.ARENA: Play.arena,
    ScreenId.NPCS: Play.npcs,
    ScreenId.NPC: Play.npc,
    ScreenId.SETTINGS: Play.settings,
    ScreenId.TUTORIAL: Play.tutorial,
    ScreenId.KEEPER: Play.keeper,
    ScreenId.KEEPER_CONTENT: Play.keeper_content,
    ScreenId.KEEPER_LIST: Play.keeper_list,
    ScreenId.KEEPER_ENTITY: Play.keeper_entity,
    ScreenId.KEEPER_FIELD: Play.keeper_field,
    ScreenId.KEEPER_PLAYERS: Play.keeper_players,
    ScreenId.KEEPER_PLAYER: Play.keeper_player,
    ScreenId.KEEPER_STATS: Play.keeper_stats,
    ScreenId.KEEPER_SERVICE: Play.keeper_service,
    ScreenId.STUB: Play.stub,
}

# The single step "back" leads to, per screen. Creation walks backwards through
# its own steps; play screens fall back towards the main menu.
BACK_TARGET: dict[ScreenId, ScreenId | None] = {
    ScreenId.CREATE_NAME: None,  # the first step confirms before leaving creation
    ScreenId.CREATE_RACE: ScreenId.CREATE_NAME,
    ScreenId.CREATE_RACE_DETAILS: ScreenId.CREATE_RACE,
    ScreenId.CREATE_CLASS: ScreenId.CREATE_RACE,
    ScreenId.CREATE_CLASS_DETAILS: ScreenId.CREATE_CLASS,
    ScreenId.CREATE_TRAITS: ScreenId.CREATE_CLASS,
    ScreenId.CREATE_POINTS: ScreenId.CREATE_TRAITS,
    ScreenId.CREATE_CONFIRM: ScreenId.CREATE_POINTS,
    ScreenId.MAIN_MENU: None,
    ScreenId.WORLD: ScreenId.MAIN_MENU,
    ScreenId.CITY: ScreenId.WORLD,
    ScreenId.LOCATION_LIST: ScreenId.CITY,
    ScreenId.LOCATION: ScreenId.LOCATION_LIST,
    ScreenId.COMBAT: ScreenId.LOCATION,
    ScreenId.COMBAT_BAG: ScreenId.COMBAT,
    ScreenId.INVENTORY: ScreenId.MAIN_MENU,
    ScreenId.SHOP: ScreenId.CITY,
    ScreenId.SELL: ScreenId.SHOP,
    ScreenId.CHARACTER: ScreenId.MAIN_MENU,
    ScreenId.STATS: ScreenId.CHARACTER,
    ScreenId.SKILLS: ScreenId.MAIN_MENU,
    ScreenId.SKILL_SLOTS: ScreenId.SKILLS,
    ScreenId.SKILL_PICK: ScreenId.SKILL_SLOTS,
    ScreenId.SKILL_EDGE: ScreenId.SKILLS,
    ScreenId.CRAFTS: ScreenId.MAIN_MENU,
    ScreenId.CRAFT: ScreenId.CRAFTS,
    ScreenId.QUESTS: ScreenId.MAIN_MENU,
    ScreenId.QUEST_BOARD: ScreenId.TAVERN,
    ScreenId.QUEST_OFFER: ScreenId.QUEST_BOARD,
    ScreenId.TAVERN: ScreenId.CITY,
    ScreenId.MENTOR: ScreenId.CITY,
    ScreenId.BANK: ScreenId.CITY,
    ScreenId.DUNGEON: ScreenId.CITY,
    ScreenId.ARENA: ScreenId.CITY,
    ScreenId.NPCS: ScreenId.CITY,
    ScreenId.NPC: ScreenId.NPCS,
    ScreenId.SETTINGS: ScreenId.MAIN_MENU,
    ScreenId.TUTORIAL: ScreenId.MAIN_MENU,
    ScreenId.KEEPER: ScreenId.MAIN_MENU,
    ScreenId.KEEPER_CONTENT: ScreenId.KEEPER,
    ScreenId.KEEPER_LIST: ScreenId.KEEPER_CONTENT,
    ScreenId.KEEPER_ENTITY: ScreenId.KEEPER_LIST,
    ScreenId.KEEPER_FIELD: ScreenId.KEEPER_ENTITY,
    ScreenId.KEEPER_PLAYERS: ScreenId.KEEPER,
    ScreenId.KEEPER_PLAYER: ScreenId.KEEPER_PLAYERS,
    ScreenId.KEEPER_STATS: ScreenId.KEEPER,
    ScreenId.KEEPER_SERVICE: ScreenId.KEEPER,
    ScreenId.STUB: ScreenId.CITY,
}

CREATION_ORDER: tuple[ScreenId, ...] = (
    ScreenId.CREATE_NAME,
    ScreenId.CREATE_RACE,
    ScreenId.CREATE_CLASS,
    ScreenId.CREATE_TRAITS,
    ScreenId.CREATE_POINTS,
    ScreenId.CREATE_CONFIRM,
)


@dataclass(frozen=True, slots=True)
class NavigationStack:
    """The screens the player walked through, oldest first."""

    screens: tuple[ScreenId, ...] = ()

    @property
    def current(self) -> ScreenId | None:
        return self.screens[-1] if self.screens else None

    def push(self, screen: ScreenId) -> NavigationStack:
        if self.current is screen:
            return self
        return NavigationStack((*self.screens, screen))

    def pop(self) -> tuple[NavigationStack, ScreenId | None]:
        """Step back one screen, keeping every choice already made."""
        if len(self.screens) <= 1:
            return NavigationStack(()), None
        remaining = self.screens[:-1]
        return NavigationStack(remaining), remaining[-1]

    def serialise(self) -> str:
        return ",".join(screen.value for screen in self.screens)

    @classmethod
    def deserialise(cls, raw: str) -> NavigationStack:
        """Rebuild the stack, dropping screens the game no longer has.

        A player can be standing on a screen that was renamed or removed between
        two releases; their walk back is then shorter, but nothing raises.
        """
        if not raw:
            return cls(())
        known = {screen.value for screen in ScreenId}
        return cls(tuple(ScreenId(part) for part in raw.split(",") if part in known))


def back_target(screen: ScreenId) -> ScreenId | None:
    """The declared previous screen, used when the stack is empty."""
    return BACK_TARGET.get(screen)
