"""The Screen abstraction.

A screen is *text plus a keyboard layout*, nothing else. It knows no aiogram
types, which is what lets the accessibility tests build every screen in the game
and inspect it without a bot token.

Invariants enforced right here, at construction time:

- the service row ``Назад · Осмотреться · Главное меню`` is appended to every
  screen (accessibility rule 8);
- button labels are unique within a screen, both with and without emoji, because
  routing is by exact text (rule 9);
- the body stays under the message limit (rule 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mmorpg.presentation.telegram.keyboards.labels import SERVICE_ROW, Label
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT, paginate_text


class ScreenId(StrEnum):
    """Every screen in the game. Used for routing and for the FSM state map."""

    START = "start"
    MAIN_MENU = "main_menu"
    WORLD = "world"
    CITY = "city"
    LOCATION_LIST = "location_list"
    LOCATION = "location"
    COMBAT = "combat"
    COMBAT_BAG = "combat_bag"
    INVENTORY = "inventory"
    SHOP = "shop"
    SELL = "sell"
    CHARACTER = "character"
    SKILLS = "skills"
    SKILL_SLOTS = "skill_slots"
    SKILL_PICK = "skill_pick"
    SKILL_EDGE = "skill_edge"
    QUESTS = "quests"
    QUEST_BOARD = "quest_board"
    QUEST_OFFER = "quest_offer"
    TAVERN = "tavern"
    MENTOR = "mentor"
    BANK = "bank"
    DUNGEON = "dungeon"
    SETTINGS = "settings"
    STUB = "stub"
    CREATE_NAME = "create_name"
    CREATE_RACE = "create_race"
    CREATE_RACE_DETAILS = "create_race_details"
    CREATE_CLASS = "create_class"
    CREATE_CLASS_DETAILS = "create_class_details"
    CREATE_TRAITS = "create_traits"
    CREATE_POINTS = "create_points"
    CREATE_CONFIRM = "create_confirm"


@dataclass(frozen=True, slots=True)
class Screen:
    """One rendered screen: what to say and what can be pressed."""

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
                # Both renderings are checked: a player can press a button from an
                # older keyboard drawn with the other emoji setting, and that text
                # must still identify exactly one action.
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
        """Action rows plus the service row."""
        return (*self.rows, SERVICE_ROW) if self.service_row else self.rows

    def text(self) -> str:
        return "\n".join(line for line in self.lines if line is not None)

    def pages(self) -> tuple[str, ...]:
        """The message body, split only if it genuinely cannot fit."""
        return paginate_text(self.text(), MESSAGE_LIMIT)

    def button_texts(self, *, emoji: bool = False) -> tuple[tuple[str, ...], ...]:
        """The layout as plain strings, exactly as Telegram will show it."""
        return tuple(tuple(item.render(emoji=emoji) for item in row) for row in self.all_rows())

    def labels(self) -> tuple[Label, ...]:
        return tuple(item for row in self.all_rows() for item in row)

    def find(self, pressed: str) -> Label | None:
        """Match a pressed button back to its label, in either rendering."""
        for item in self.labels():
            if item.matches(pressed):
                return item
        return None

    def fits_message_limit(self) -> bool:
        return len(self.text()) <= MESSAGE_LIMIT
