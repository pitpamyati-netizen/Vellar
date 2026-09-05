"""Понятие экрана.

Экран - это *текст плюс раскладка клавиатуры*, и больше ничего. Он не знает
типов aiogram, и это то, что позволяет тестам доступности собрать каждый экран
игры и рассмотреть его без токена бота.

Что проверяется прямо здесь, в минуту сборки:

- служебный ряд ``Назад · Главное меню`` дописывается к каждому экрану (правило
  доступности 8);
- надписи кнопок внутри экрана не повторяются - ни со значками, ни без них,
  потому что маршрутизация идёт по точному тексту (правило 9);
- тело умещается в предел сообщения (правило 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mmorpg.presentation.telegram.keyboards.labels import SERVICE_ROW, Label
from mmorpg.presentation.telegram.screens.format import (
    HARD_LIMIT,
    MESSAGE_LIMIT,
    paginate_text,
)


class ScreenId(StrEnum):
    """Каждый экран игры. Берётся для маршрутизации и для карты состояний автомата."""

    START = "start"
    MAIN_MENU = "main_menu"
    WORLD = "world"
    CITY = "city"
    LOCATION_LIST = "location_list"
    LOCATION = "location"
    COMBAT = "combat"
    COMBAT_BAG = "combat_bag"
    INVENTORY = "inventory"
    ITEM = "item"
    SHOP = "shop"
    SHOP_ITEM = "shop_item"
    SELL = "sell"
    LIST_FILTERS = "list_filters"
    CHARACTER = "character"
    STATS = "stats"
    SKILLS = "skills"
    SKILL_SLOTS = "skill_slots"
    SKILL_PICK = "skill_pick"
    CRAFTS = "crafts"
    CRAFT = "craft"
    QUESTS = "quests"
    QUEST_BOARD = "quest_board"
    QUEST_OFFER = "quest_offer"
    TAVERN = "tavern"
    SUMMARY = "summary"
    MENTOR = "mentor"
    BANK = "bank"
    FORGE = "forge"
    SALVAGE = "salvage"
    REFORGE = "reforge"
    DUNGEON = "dungeon"
    DUNGEON_PICK = "dungeon_pick"
    ARENA = "arena"
    CHAMBER = "chamber"
    CHAMBER_REMORT = "chamber_remort"
    TURNING = "turning"
    HOUSE = "house"
    NPCS = "npcs"
    NPC = "npc"
    PARTY = "party"
    PARTY_INVITE = "party_invite"
    GUILD = "guild"
    GUILD_FOUND = "guild_found"
    GUILD_INVITE = "guild_invite"
    GUILD_ROSTER = "guild_roster"
    GUILD_VAULT = "guild_vault"
    TRANSFER_TO = "transfer_to"
    TRANSFER_ITEM = "transfer_item"
    TRANSFER_AMOUNT = "transfer_amount"
    SETTINGS = "settings"
    TUTORIAL = "tutorial"
    KEEPER = "keeper"
    KEEPER_CONTENT = "keeper_content"
    KEEPER_EDITS = "keeper_edits"
    KEEPER_EDIT = "keeper_edit"
    KEEPER_LIST = "keeper_list"
    KEEPER_ENTITY = "keeper_entity"
    KEEPER_FIELD = "keeper_field"
    KEEPER_PLAYERS = "keeper_players"
    KEEPER_PLAYER_FILTERS = "keeper_player_filters"
    KEEPER_PLAYER = "keeper_player"
    KEEPER_STATS = "keeper_stats"
    KEEPER_SERVICE = "keeper_service"
    KEEPER_OPS = "keeper_ops"
    KEEPER_BAN = "keeper_ban"
    KEEPER_MUTE = "keeper_mute"
    KEEPER_GOLD_FLOW = "keeper_gold_flow"
    KEEPER_LOG = "keeper_log"
    KEEPER_TRADES = "keeper_trades"
    KEEPER_TUNE = "keeper_tune"
    KEEPER_AMOUNT = "keeper_amount"
    KEEPER_GIVE = "keeper_give"
    KEEPER_GIVE_GEAR = "keeper_give_gear"
    KEEPER_GIVE_ITEM = "keeper_give_item"
    KEEPER_SKILLS = "keeper_skills"
    KEEPER_SKILL = "keeper_skill"
    KEEPER_SKILL_LEARN = "keeper_skill_learn"
    KEEPER_SKILL_SLOT = "keeper_skill_slot"
    KEEPER_STATS_EDIT = "keeper_stats_edit"
    KEEPER_QUESTS = "keeper_quests"
    KEEPER_QUEST = "keeper_quest"
    KEEPER_BAG = "keeper_bag"
    KEEPER_PARTY = "keeper_party"
    KEEPER_GUILD = "keeper_guild"
    CREATE_NAME = "create_name"
    CREATE_RACE = "create_race"
    CREATE_RACE_DETAILS = "create_race_details"
    CREATE_CLASS = "create_class"
    CREATE_CLASS_DETAILS = "create_class_details"
    CREATE_TRAITS = "create_traits"
    CREATE_TRAIT_FILTERS = "create_trait_filters"
    CREATE_POINTS = "create_points"
    CREATE_CONFIRM = "create_confirm"


@dataclass(frozen=True, slots=True)
class Screen:
    """Один нарисованный экран: что сказать и что можно нажать."""

    id: ScreenId
    lines: tuple[str, ...]
    rows: tuple[tuple[Label, ...], ...] = ()
    service_row: bool = True
    context: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for row in self.all_rows():
            for item in row:
                # Проверяются обе записи: игрок может нажать кнопку старой клавиатуры,
                # нарисованной при другой настройке значков, и этот текст обязан по-
                # прежнему указывать ровно на одно действие.
                renderings = {item.render(emoji=False), item.render(emoji=True)}
                clash = renderings & seen
                if clash:
                    msg = (
                        f"screen {self.id}: duplicate button label {sorted(clash)[0]!r};"
                        " routing is by exact text, so labels must be unique"
                    )
                    raise ValueError(msg)
                seen |= renderings

    def all_rows(self) -> tuple[tuple[Label, ...], ...]:
        """Ряды действий плюс служебный ряд."""
        return (*self.rows, SERVICE_ROW) if self.service_row else self.rows

    def text(self) -> str:
        return "\n".join(line for line in self.lines if line is not None)

    def pages(self) -> tuple[str, ...]:
        """Тело сообщения, разрезанное только если оно и правда не влезает."""
        return paginate_text(self.text(), MESSAGE_LIMIT)

    def body(self) -> str:
        """Что уходит игроку одним сообщением: тело целиком.

        Отправлялась ``pages()[0]`` - первая страница из девятисот знаков, - и
        всё, что не влезло, пропадало молча: на экране умений было восемь кнопок
        и пять описаний. Список режется на страницы там, где он собирается
        (``screens/paginated.py``); здесь остаётся только предел самого
        Telegram, до которого экраны не доходят.
        """
        return paginate_text(self.text(), HARD_LIMIT)[0]

    def button_texts(self, *, emoji: bool = False) -> tuple[tuple[str, ...], ...]:
        """Раскладка обычными строками, ровно такая, какой её покажет Telegram."""
        return tuple(tuple(item.render(emoji=emoji) for item in row) for row in self.all_rows())

    def labels(self) -> tuple[Label, ...]:
        return tuple(item for row in self.all_rows() for item in row)

    def find(self, pressed: str) -> Label | None:
        """Свести нажатую кнопку обратно к её надписи, в любой из двух записей."""
        for item in self.labels():
            if item.matches(pressed):
                return item
        return None

    def fits_message_limit(self) -> bool:
        return len(self.text()) <= MESSAGE_LIMIT
