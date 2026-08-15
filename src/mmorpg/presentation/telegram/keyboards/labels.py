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
MAIN_MENU = label("Главное меню", "🏠")
SERVICE_ROW: tuple[Label, ...] = (BACK, MAIN_MENU)

# "Осмотреться" is no longer a button: in Telegram nothing scrolls away, and the
# last message is always there to be read again, so a third button on every single
# screen was a button that cost attention and bought nothing. The label stays -
# the command ``/осмотреться`` still works, and so does the button itself if a
# player presses one left over on an older keyboard.
LOOK = label("Осмотреться", "🔁")

# --- navigation ---

WORLD = label("Мир", "🗺")
CITY = label("Город", "🏰")
LOCATIONS = label("Локации", "🌲")
DUNGEONS = label("Данжи", "⛓")
ARENA = label("Долговой круг", "🥊")
TAVERN = label("Таверна", "🍺")
MENTOR = label("Наставник", "📖")
BANK = label("Банк", "💰")
SHOP = label("Лавка", "🛒")
CHARACTER = label("Персонаж", "🧝")
STATS = label("Характеристики", "📊")
INVENTORY = label("Инвентарь", "🎒")
SKILLS = label("Умения", "✨")
QUESTS = label("Подряды", "📜")
SETTINGS = label("Настройки", "⚙️")
TUTORIAL = label("Обучение", "🧭")

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

# --- keeper (ADMIN_IDS only; ordinary players never see this row) ---

KEEPER = label("Смотритель", "🗝")
KEEPER_GOLD = label("Выдать золото", "🪙")
KEEPER_LEVEL = label("Поднять уровень", "🔼")
KEEPER_HEAL = label("Залечить раны", "🩹")
KEEPER_POINTS = label("Выдать очки", "🎯")

# The panel: four doors, and behind them everything the game is made of.
KEEPER_WORLD = label("Мир и содержимое", "🧱")
KEEPER_PLAYERS = label("Игроки", "👥")
KEEPER_STATS = label("Статистика", "📈")
KEEPER_SERVICE = label("Обслуживание", "🧹")

# Editing one entity: add, change, take back.
KEEPER_ADD = label("Добавить", "➕")
KEEPER_REMOVE = label("Убрать из игры", "🚫")
KEEPER_RETURN = label("Вернуть в игру", "↩️")
KEEPER_FORGET = label("Снять правку", "🧽")
KEEPER_CLEAR = label("Очистить поле", "␡")
KEEPER_RELOAD = label("Перечитать правки", "🔄")

# Players.
KEEPER_FIND = label("Найти по имени", "🔤")
KEEPER_MOVE = label("Перевести в город", "🧭")
KEEPER_DELETE = label("Удалить персонажа", "🗑")

# Maintenance.
KEEPER_SWEEP_DRAFTS = label("Убрать брошенных", "🧺")
KEEPER_CHECK_BLOCKED = label("Проверить, кто заблокировал", "📮")
KEEPER_DROP_BLOCKED = label("Убрать заблокировавших", "🚮")

# --- residents of a city (only where somebody actually lives) ---

NPCS = label("Жители", "🧑")

# --- crafts ---

CRAFTS = label("Ремёсла", "🛠")
GATHER = label("Собрать сырьё", "🌿")

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
