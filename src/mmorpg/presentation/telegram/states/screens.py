"""Состояния автомата и стопка для возврата назад.

Создание персонажа - прямая дорога с настоящим «назад» на каждом шаге, включая
первый (спецификация, раздел 12). Стопка пройденных шагов лежит в данных
автомата, а не выводится догадками: шаг назад снимает одну запись, и всякий уже
сделанный выбор остаётся на месте.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.fsm.state import State, StatesGroup

from mmorpg.presentation.telegram.screens.base import ScreenId


class Creation(StatesGroup):
    """Ветка создания персонажа."""

    name = State()
    race = State()
    race_details = State()
    character_class = State()
    class_details = State()
    traits = State()
    trait_filters = State()
    points = State()
    confirm = State()


class Play(StatesGroup):
    """Всё, что идёт после того, как персонаж появился."""

    main_menu = State()
    world = State()
    city = State()
    location_list = State()
    location = State()
    combat = State()
    combat_bag = State()
    inventory = State()
    item = State()
    shop = State()
    shop_item = State()
    sell = State()
    list_filters = State()
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
    chamber = State()
    chamber_pledge = State()
    turning = State()
    npcs = State()
    npc = State()
    party = State()
    party_invite = State()
    guild = State()
    guild_found = State()
    guild_invite = State()
    guild_roster = State()
    guild_vault = State()
    transfer_to = State()
    transfer_item = State()
    transfer_amount = State()
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
    keeper_ban = State()
    keeper_log = State()
    keeper_trades = State()
    keeper_tune = State()
    keeper_amount = State()
    keeper_give = State()
    keeper_give_gear = State()
    keeper_give_item = State()
    keeper_skills = State()
    keeper_skill = State()
    keeper_skill_learn = State()
    keeper_skill_edge = State()
    keeper_skill_slot = State()


# Какой экран какому состоянию принадлежит. По этому разборщик говорит игроку, где он на
# самом деле стоит, когда тот нажал кнопку старой клавиатуры.
STATE_FOR_SCREEN: dict[ScreenId, State] = {
    ScreenId.CREATE_NAME: Creation.name,
    ScreenId.CREATE_RACE: Creation.race,
    ScreenId.CREATE_RACE_DETAILS: Creation.race_details,
    ScreenId.CREATE_CLASS: Creation.character_class,
    ScreenId.CREATE_CLASS_DETAILS: Creation.class_details,
    ScreenId.CREATE_TRAITS: Creation.traits,
    ScreenId.CREATE_TRAIT_FILTERS: Creation.trait_filters,
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
    ScreenId.ITEM: Play.item,
    ScreenId.SHOP: Play.shop,
    ScreenId.SHOP_ITEM: Play.shop_item,
    ScreenId.SELL: Play.sell,
    ScreenId.LIST_FILTERS: Play.list_filters,
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
    ScreenId.CHAMBER: Play.chamber,
    ScreenId.CHAMBER_PLEDGE: Play.chamber_pledge,
    ScreenId.TURNING: Play.turning,
    ScreenId.NPCS: Play.npcs,
    ScreenId.NPC: Play.npc,
    ScreenId.PARTY: Play.party,
    ScreenId.PARTY_INVITE: Play.party_invite,
    ScreenId.GUILD: Play.guild,
    ScreenId.GUILD_FOUND: Play.guild_found,
    ScreenId.GUILD_INVITE: Play.guild_invite,
    ScreenId.GUILD_ROSTER: Play.guild_roster,
    ScreenId.GUILD_VAULT: Play.guild_vault,
    ScreenId.TRANSFER_TO: Play.transfer_to,
    ScreenId.TRANSFER_ITEM: Play.transfer_item,
    ScreenId.TRANSFER_AMOUNT: Play.transfer_amount,
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
    ScreenId.KEEPER_BAN: Play.keeper_ban,
    ScreenId.KEEPER_LOG: Play.keeper_log,
    ScreenId.KEEPER_TRADES: Play.keeper_trades,
    ScreenId.KEEPER_TUNE: Play.keeper_tune,
    ScreenId.KEEPER_AMOUNT: Play.keeper_amount,
    ScreenId.KEEPER_GIVE: Play.keeper_give,
    ScreenId.KEEPER_GIVE_GEAR: Play.keeper_give_gear,
    ScreenId.KEEPER_GIVE_ITEM: Play.keeper_give_item,
    ScreenId.KEEPER_SKILLS: Play.keeper_skills,
    ScreenId.KEEPER_SKILL: Play.keeper_skill,
    ScreenId.KEEPER_SKILL_LEARN: Play.keeper_skill_learn,
    ScreenId.KEEPER_SKILL_EDGE: Play.keeper_skill_edge,
    ScreenId.KEEPER_SKILL_SLOT: Play.keeper_skill_slot,
}

# Тот единственный шаг, куда ведёт «назад», для каждого экрана. Создание идёт назад по
# собственным шагам, игровые экраны откатываются к главному меню.
BACK_TARGET: dict[ScreenId, ScreenId | None] = {
    ScreenId.CREATE_NAME: None,  # первый шаг спрашивает подтверждение перед выходом из создания
    ScreenId.CREATE_RACE: ScreenId.CREATE_NAME,
    ScreenId.CREATE_RACE_DETAILS: ScreenId.CREATE_RACE,
    ScreenId.CREATE_CLASS: ScreenId.CREATE_RACE,
    ScreenId.CREATE_CLASS_DETAILS: ScreenId.CREATE_CLASS,
    ScreenId.CREATE_TRAITS: ScreenId.CREATE_CLASS,
    ScreenId.CREATE_TRAIT_FILTERS: ScreenId.CREATE_TRAITS,
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
    ScreenId.ITEM: ScreenId.INVENTORY,
    ScreenId.SHOP: ScreenId.CITY,
    ScreenId.SHOP_ITEM: ScreenId.SHOP,
    ScreenId.SELL: ScreenId.SHOP,
    ScreenId.LIST_FILTERS: ScreenId.INVENTORY,
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
    ScreenId.CHAMBER: ScreenId.CITY,
    ScreenId.CHAMBER_PLEDGE: ScreenId.CHAMBER,
    ScreenId.TURNING: ScreenId.CHAMBER,
    ScreenId.NPCS: ScreenId.CITY,
    ScreenId.NPC: ScreenId.NPCS,
    ScreenId.PARTY: ScreenId.MAIN_MENU,
    ScreenId.PARTY_INVITE: ScreenId.PARTY,
    ScreenId.GUILD: ScreenId.MAIN_MENU,
    ScreenId.GUILD_FOUND: ScreenId.GUILD,
    ScreenId.GUILD_INVITE: ScreenId.GUILD,
    ScreenId.GUILD_ROSTER: ScreenId.GUILD,
    ScreenId.GUILD_VAULT: ScreenId.GUILD,
    # Передача общая для отряда и гильдии; куда вести «Назад» на самом деле,
    # знает ``NavigationStack`` — здесь только нейтральный запасной путь.
    ScreenId.TRANSFER_TO: ScreenId.MAIN_MENU,
    ScreenId.TRANSFER_ITEM: ScreenId.TRANSFER_TO,
    ScreenId.TRANSFER_AMOUNT: ScreenId.TRANSFER_ITEM,
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
    ScreenId.KEEPER_BAN: ScreenId.KEEPER_PLAYER,
    ScreenId.KEEPER_LOG: ScreenId.KEEPER,
    ScreenId.KEEPER_TRADES: ScreenId.KEEPER_PLAYER,
    ScreenId.KEEPER_TUNE: ScreenId.KEEPER,
    ScreenId.KEEPER_AMOUNT: ScreenId.KEEPER_TUNE,
    ScreenId.KEEPER_GIVE: ScreenId.KEEPER_PLAYER,
    ScreenId.KEEPER_GIVE_GEAR: ScreenId.KEEPER_GIVE,
    ScreenId.KEEPER_GIVE_ITEM: ScreenId.KEEPER_GIVE,
    ScreenId.KEEPER_SKILLS: ScreenId.KEEPER_PLAYER,
    ScreenId.KEEPER_SKILL: ScreenId.KEEPER_SKILLS,
    ScreenId.KEEPER_SKILL_LEARN: ScreenId.KEEPER_SKILLS,
    ScreenId.KEEPER_SKILL_EDGE: ScreenId.KEEPER_SKILL,
    ScreenId.KEEPER_SKILL_SLOT: ScreenId.KEEPER_SKILL,
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
    """Экраны, через которые прошёл игрок, старые сверху."""

    screens: tuple[ScreenId, ...] = ()

    @property
    def current(self) -> ScreenId | None:
        return self.screens[-1] if self.screens else None

    def push(self, screen: ScreenId) -> NavigationStack:
        """Шаг вперёд - или разматывание до того места, куда вернулись.

        Экран, на котором игрок уже стоял, не кладётся сверху ещё раз: положив
        умение в слот, игрок попадает обратно на «Слоты умений», и «Назад»
        оттуда обязано вести в «Умения», а не в тот самый выбор умения, который
        только что кончился. Иначе шаг назад открывает экран «Слот 3, боевой.
        Выберите умение», и слот читается пустым, хотя умение в нём уже лежит.
        Заодно у прогулки перестаёт расти хвост: пять заходов в один и тот же
        слот - это по-прежнему один экран в стопке, а не пять.
        """
        if self.current is screen:
            return self
        if screen in self.screens:
            return NavigationStack(self.screens[: self.screens.index(screen) + 1])
        return NavigationStack((*self.screens, screen))

    def pop(self) -> tuple[NavigationStack, ScreenId | None]:
        """Шагнуть на экран назад, сохранив всякий уже сделанный выбор."""
        if len(self.screens) <= 1:
            return NavigationStack(()), None
        remaining = self.screens[:-1]
        return NavigationStack(remaining), remaining[-1]

    def serialise(self) -> str:
        return ",".join(screen.value for screen in self.screens)

    @classmethod
    def deserialise(cls, raw: str) -> NavigationStack:
        """Собрать стопку заново, выбросив экраны, которых у игры больше нет.

        Игрок может стоять на экране, который между двумя выпусками переименовали или
        убрали; его дорога назад тогда короче, но не падает ничто.
        """
        if not raw:
            return cls(())
        known = {screen.value for screen in ScreenId}
        return cls(tuple(ScreenId(part) for part in raw.split(",") if part in known))


def back_target(screen: ScreenId) -> ScreenId | None:
    """Объявленный предыдущий экран; берётся, когда стопка пуста."""
    return BACK_TARGET.get(screen)
