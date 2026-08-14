"""Button labels.

A label is text plus an *optional* emoji. The text alone must always be
unambiguous, because emoji are off by default and a screen reader user may never
hear them (accessibility rule 6).

Routing is by exact button text, so these strings are part of the contract: a
renamed label is a routing change (``docs/adr/0002-reply-keyboards-only.md``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Label:
    """One button label."""

    text: str
    emoji: str = ""

    def render(self, *, emoji: bool = False) -> str:
        """The exact string shown on the button."""
        if emoji and self.emoji:
            return f"{self.emoji} {self.text}"
        return self.text

    def matches(self, pressed: str) -> bool:
        """Whether a pressed button text refers to this label.

        Accepts both renderings, because the player may press a button from an
        older keyboard rendered with the other emoji setting.
        """
        stripped = pressed.strip()
        return stripped in {self.text, f"{self.emoji} {self.text}".strip()}


def label(text: str, emoji: str = "") -> Label:
    return Label(text=text, emoji=emoji)


# --- the service row, identical on every screen (accessibility rule 8) ---

BACK = label("Назад", "◀️")
LOOK = label("Осмотреться", "🔁")
MAIN_MENU = label("Главное меню", "🏠")
SERVICE_ROW: tuple[Label, ...] = (BACK, LOOK, MAIN_MENU)

# --- navigation ---

WORLD = label("Мир", "🗺")
CITY = label("Город", "🏰")
LOCATIONS = label("Локации", "🌲")
DUNGEONS = label("Данжи", "⛓")
TAVERN = label("Таверна", "🍺")
MENTOR = label("Наставник", "📖")
BANK = label("Банк", "💰")
SHOP = label("Лавка", "🛒")
CHARACTER = label("Персонаж", "🧝")
INVENTORY = label("Инвентарь", "🎒")
SKILLS = label("Умения", "✨")
QUESTS = label("Подряды", "📜")
SETTINGS = label("Настройки", "⚙️")

# --- skills, contracts and city services ---

SKILL_SLOTS = label("Слоты умений", "🧩")
SKILL_LEARN = label("Изучить и улучшить", "📚")
QUEST_BOARD = label("Доска подрядов", "📌")
QUEST_ACCEPT = label("Согласиться", "🤝")
QUEST_ASK = label("Спросить, кто платит", "❔")
QUEST_LEAVE = label("Уйти", "🚪")
REST_PAID = label("Снять комнату", "🛏")
REST_FREE = label("Ночь на соломе", "🌾")
HAND_IN = label("Сдать подряд", "🧾")
SELL = label("Продать вещи", "💱")
DEPOSIT = label("Положить в банк", "📥")
WITHDRAW = label("Забрать из банка", "📤")
FORGET_SKILL = label("Забыть умение", "🧠")
DUNGEON_ENTER = label("Спуститься", "🕳")
DUNGEON_DEEPER = label("Идти глубже", "⬇️")
DUNGEON_LEAVE = label("Выйти на воздух", "🚪")

# --- creation ---

CREATE_CHARACTER = label("Создать персонажа", "✳️")
CONTINUE = label("Продолжить", "▶️")
CONFIRM = label("Подтвердить", "✅")
RACE_DETAILS = label("Подробно о расе", "❔")
CLASS_DETAILS = label("Подробно о классе", "❔")

# --- paginated lists (accessibility rule 8 and section 13 of the spec) ---

PREVIOUS_PAGE = label("Предыдущая страница", "⬅️")
NEXT_PAGE = label("Следующая страница", "➡️")
FILTERS = label("Фильтры", "🔎")
RESET_FILTERS = label("Сбросить фильтры", "🧹")
SEARCH = label("Поиск", "🔤")
APPLY = label("Применить", "✅")

# --- combat ---

ATTACK = label("Атака", "⚔️")
BAG = label("Сумка", "🎒")
FLEE = label("Бежать", "🏃")
