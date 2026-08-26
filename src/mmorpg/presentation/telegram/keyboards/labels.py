"""Надписи на кнопках.

Надпись - это текст плюс *необязательный* значок. Один текст обязан быть
однозначным всегда, потому что значки выключены по умолчанию, и тот, кто слушает
экранный диктор, может не услышать их никогда (правило доступности 6).

Маршрутизация идёт по точному тексту кнопки, поэтому эти строки - часть
договора: переименованная надпись есть изменение маршрута
(``docs/adr/0002-reply-keyboards-only.md``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Label:
    """Одна надпись на кнопке."""

    text: str
    emoji: str = ""

    def render(self, *, emoji: bool = False) -> str:
        """Ровно та строка, что показана на кнопке."""
        if emoji and self.emoji:
            return f"{self.emoji} {self.text}"
        return self.text

    def matches(self, pressed: str) -> bool:
        """Относится ли нажатый текст кнопки к этой надписи.

        Принимаются обе записи, потому что игрок мог нажать кнопку старой клавиатуры,
        собранной при другой настройке значков.
        """
        stripped = pressed.strip()
        return stripped in {self.text, f"{self.emoji} {self.text}".strip()}


def label(text: str, emoji: str = "") -> Label:
    return Label(text=text, emoji=emoji)


# --- служебный ряд, одинаковый на каждом экране (правило доступности 8) ---

BACK = label("Назад", "◀️")
MAIN_MENU = label("Главное меню", "🏠")
SERVICE_ROW: tuple[Label, ...] = (BACK, MAIN_MENU)

# «Осмотреться» больше не кнопка: в Telegram ничто не уезжает безвозвратно, и последнее
# сообщение всегда можно прочитать заново, поэтому третья кнопка на каждом без
# исключения экране была кнопкой, которая стоила внимания и не покупала ничего. Надпись
# осталась: команда ``/осмотреться`` работает, как работает и сама кнопка, если игрок
# нажмёт её на старой клавиатуре.
LOOK = label("Осмотреться", "🔁")

# --- перемещение ---

WORLD = label("Мир", "🗺")
CITY = label("Город", "🏰")
LOCATIONS = label("Локации", "🌲")
# Не «данжи»: в Велларе это подземелья, и экран за кнопкой зовёт их так же.
DUNGEONS = label("Подземелья", "⛓")
ARENA = label("Арена", "🥊")
CHAMBER = label("Палата", "🏛")
TAVERN = label("Таверна", "🍺")
MENTOR = label("Наставник", "📖")
BANK = label("Банк", "💰")
SHOP = label("Лавка", "🛒")
CHARACTER = label("Персонаж", "🧝")
STATS = label("Характеристики", "📊")
INVENTORY = label("Инвентарь", "🎒")
SKILLS = label("Умения", "✨")
QUESTS = label("Задания", "📜")
SETTINGS = label("Настройки", "⚙️")
TUTORIAL = label("Обучение", "🧭")

# --- умения, задания и городские службы ---

SKILL_SLOTS = label("Слоты умений", "🧩")
SKILL_LEARN = label("Изучить и улучшить", "📚")
QUEST_BOARD = label("Доска заданий", "📌")
QUEST_ACCEPT = label("Согласиться", "🤝")
QUEST_ASK = label("Спросить, кто платит", "❔")
QUEST_LEAVE = label("Уйти", "🚪")
QUEST_ABANDON = label("Отказаться от задания", "🙅")
REST_PAID = label("Снять комнату", "🛏")
REST_FREE = label("Ночь на соломе", "🌾")
HAND_IN = label("Сдать задание", "🧾")
SELL = label("Продать вещи", "💱")
DEPOSIT = label("Положить в банк", "📥")
WITHDRAW = label("Забрать из банка", "📤")
FORGET_SKILL = label("Забыть умение", "🧠")
DUNGEON_ENTER = label("Спуститься", "🕳")
DUNGEON_DEEPER = label("Идти глубже", "⬇️")
DUNGEON_LEAVE = label("Выйти на воздух", "🚪")

# --- Перерождение (эндгейм, ``domain/rules/turning.py``) ---

TURNING = label("Совершить перерождение", "🏵")
TURNING_QUESTION = label("Голосование", "🧮")

# --- смотритель (только ADMIN_IDS; обычные игроки этого ряда не видят) ---

KEEPER = label("Смотритель", "🗝")
KEEPER_GOLD = label("Выдать золото", "🪙")
KEEPER_LEVEL = label("Поднять уровень", "🔼")
KEEPER_HEAL = label("Залечить раны", "🩹")
KEEPER_POINTS = label("Выдать очки", "🎯")

# Панель: четыре двери, а за ними всё, из чего сделана игра.
KEEPER_WORLD = label("Мир и содержимое", "🧱")
KEEPER_PLAYERS = label("Игроки", "👥")
KEEPER_STATS = label("Статистика", "📈")
KEEPER_SERVICE = label("Обслуживание", "🧹")

# Правка одной сущности: добавить, изменить, отменить.
KEEPER_ADD = label("Добавить", "➕")
KEEPER_REMOVE = label("Убрать из игры", "🚫")
KEEPER_RETURN = label("Вернуть в игру", "↩️")
KEEPER_FORGET = label("Снять правку", "🧽")
KEEPER_CLEAR = label("Очистить поле", "␡")
KEEPER_RELOAD = label("Перечитать правки", "🔄")

# Игроки.
KEEPER_FIND = label("Найти по имени", "🔤")
KEEPER_MOVE = label("Перевести в город", "🧭")
KEEPER_DELETE = label("Удалить персонажа", "🗑")
KEEPER_BAN = label("Заблокировать", "⛔")
KEEPER_UNBAN = label("Снять блокировку", "🔓")
KEEPER_REASON = label("Указать причину", "✍")
KEEPER_LOG = label("Журнал", "📜")
KEEPER_PROMOTE = label("Сделать смотрителем", "🗝")
KEEPER_DEMOTE = label("Убрать из смотрителей", "🚷")
KEEPER_TRADES = label("Сделки", "🤝")

# Обслуживание.
KEEPER_SWEEP_DRAFTS = label("Убрать брошенных", "🧺")
KEEPER_CHECK_BLOCKED = label("Проверить, кто заблокировал", "📮")
KEEPER_DROP_BLOCKED = label("Убрать заблокировавших", "🚮")

# --- жители города (только там, где кто-то действительно живёт) ---

NPCS = label("Жители", "🧑")

# --- crafts ---

CRAFTS = label("Ремёсла", "🛠")
GATHER = label("Собрать сырьё", "🌿")

# --- создание ---

CREATE_CHARACTER = label("Создать персонажа", "✳️")
CONTINUE = label("Продолжить", "▶️")
CONFIRM = label("Подтвердить", "✅")
RACE_DETAILS = label("Подробно о расе", "❔")
CLASS_DETAILS = label("Подробно о классе", "❔")

# --- списки со страницами (правило доступности 8 и раздел 13 спецификации) ---

PREVIOUS_PAGE = label("Предыдущая страница", "⬅️")
NEXT_PAGE = label("Следующая страница", "➡️")
FILTERS = label("Фильтры", "🔎")
RESET_FILTERS = label("Сбросить фильтры", "🧹")
SEARCH = label("Поиск", "🔤")
APPLY = label("Применить", "✅")

# --- combat ---

ATTACK = label("Атака", "⚔️")
# Закрыться: ход, который умеет всякий и которому не нужно умения. Что он даёт,
# метка называет числами (``screens/combat.defend_label``).
DEFEND = label("Защититься", "🛡")
BAG = label("Сумка", "🎒")
FLEE = label("Бежать", "🏃")
# Ожидание чужого хода - это экран, а не пустота: у него есть что сказать и есть
# что нажать. «Что там» перечитывает бой, «Сдаться» из него выходит - это
# единственная дверь из поединка, который бросили с той стороны (ADR 0021).
BATTLE_REFRESH = label("Что там в бою", "👀")
BATTLE_YIELD = label("Сдаться", "🏳")

# --- отряд (``domain/rules/party.py``) ---

PARTY = label("Отряд", "🤝")
PARTY_CREATE = label("Создать отряд", "🤝")
PARTY_DISBAND = label("Расформировать отряд", "🚪")
PARTY_INVITE = label("Пригласить в отряд", "✉️")
PARTY_ACCEPT = label("Пойти вместе", "🤝")
PARTY_DECLINE = label("Отказаться", "🙅")
PARTY_LEAVE = label("Покинуть отряд", "🚪")
