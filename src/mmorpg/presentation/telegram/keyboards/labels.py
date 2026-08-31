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
# Уводит из города на большую дорогу: список городов, куда можно уйти за золото.
ROAD = label("Дорога", "🐎")
LOCATIONS = label("Локации", "🌲")
# Не «данжи»: в Велларе это подземелья, и экран за кнопкой зовёт их так же.
DUNGEONS = label("Подземелья", "⛓")
ARENA = label("Арена", "🥊")
CHAMBER = label("Управа", "🏛")
HOUSE = label("Двор дома", "🏰")
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
# Заход в подземелье: сперва выбирают подземелье из списка (ADR 0041), потом
# сложность (``domain/rules/dungeon.Difficulty``, ADR 0036). Кнопки сложности
# общие для всех подземелий — маршрут идёт по точному тексту.
DIFFICULTY_RECON = label("Разведка", "🕳")
DIFFICULTY_DELVE = label("Тёмный ход", "🕳")
DIFFICULTY_GRIM = label("Гиблый спуск", "🕳")

# Блуждающее подземелье в локации (ADR 0037): подземный ход, которого в сводке
# не было. Заход в него — тот же движок, что городской спуск.
ENTER_ROAMER = label("Спуститься в подземелье", "🕳")

# Развилка после комнаты: в какую из соседних идти дальше. Назад пути нет.
ROOM_SKIRMISH = label("Дальше — схватка", "⚔️")
ROOM_BEAST = label("Дальше — крупный зверь", "🐗")
ROOM_HOLLOW = label("Дальше — затишье", "🕯")
ROOM_LAIR = label("Логово хозяина", "💀")
ROOM_STAIRS = label("Ход наверх", "🚪")

# --- Новое имя (эндгейм, ``domain/rules/turning.py``) ---

TURNING = label("Просить новое имя", "🏵")
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
KEEPER_EDITS_BTN = label("Все правки", "📋")
KEEPER_OPEN_CARD = label("Открыть карточку", "📇")

# Точные правки: то же, что быстрые выдачи, но числом, а не шагом.
KEEPER_TUNE = label("Задать точно", "🎚")
KEEPER_SET_GOLD = label("Золото: изменить на число", "🪙")
KEEPER_SET_BANK = label("Ячейка: выставить число", "🏦")
KEEPER_SET_HEALTH = label("Здоровье: выставить число", "🩸")
KEEPER_SET_LEVEL = label("Уровень: поднять до числа", "🔼")
KEEPER_ADD_STAT_POINTS = label("Выдать очки характеристик", "🎯")
KEEPER_ADD_SKILL_POINTS = label("Выдать очки умений", "✨")
KEEPER_RENAME = label("Переименовать", "✍")

# Выдать вещь: собранное снаряжение по виду × ступени × редкости, или
# написанный расходник/сырьё числом.
KEEPER_GIVE_ITEM = label("Выдать вещь", "🎁")
KEEPER_GIVE_AT_PLAYER_LEVEL = label("Ступень по уровню игрока", "📏")

# Умения игрока: список, карточка одного умения, изучение и сброс дерева.
KEEPER_SKILLS = label("Умения", "✨")
KEEPER_SKILL_LEARN = label("Изучить умение", "📚")
KEEPER_SKILL_RESPEC = label("Сбросить дерево умений", "♻️")
KEEPER_RANK_UP = label("Ранг больше", "🔼")
KEEPER_RANK_DOWN = label("Ранг меньше", "🔽")
KEEPER_SKILL_EDGE_BTN = label("Сменить грань", "🌿")
KEEPER_SKILL_EDGE_CLEAR = label("Снять грань", "🍂")
KEEPER_SKILL_SLOT_BTN = label("Положить в слот", "🧩")
KEEPER_SKILL_SLOT_CLEAR = label("Убрать из слотов", "␡")
KEEPER_SKILL_FORGET = label("Забыть умение", "🧠")

# Характеристики игрока: вложенное в каждую, числом.
KEEPER_STATS_EDIT_BTN = label("Характеристики", "📊")

# Сумка и снаряжение игрока: надеть из сумки, снять со слота.
KEEPER_BAG_BTN = label("Сумка и снаряжение", "🎒")

# Задания игрока: журнал, отметка и счётчик.
KEEPER_QUESTS_BTN = label("Задания", "📜")
KEEPER_QUEST_DONE = label("Отметить закрытым", "✅")
KEEPER_QUEST_REOPEN = label("Убрать из журнала", "↩️")
KEEPER_QUEST_COUNT = label("Выставить счётчик", "🔢")

# Игроки.
KEEPER_FIND = label("Найти по имени", "🔤")
KEEPER_PLAYER_FILTERS_BTN = label("Фильтры игроков", "🧮")
KEEPER_PF_LEVEL_MIN = label("Уровень от", "🔽")
KEEPER_PF_LEVEL_MAX = label("Уровень до", "🔼")
KEEPER_PF_CITY = label("Город", "🏙")
KEEPER_PF_GUILD = label("Гильдия", "🏛")
KEEPER_PF_BANNED = label("Только заблокированные", "⛔")
KEEPER_PF_FRESH = label("Заходил за сутки", "🕰")
KEEPER_PF_APPLY = label("Показать игроков", "✅")
KEEPER_PF_CLEAR = label("Сбросить фильтры игроков", "␡")
KEEPER_MOVE = label("Перевести в город", "🧭")
KEEPER_DELETE = label("Удалить персонажа", "🗑")
KEEPER_BAN = label("Заблокировать", "⛔")
KEEPER_UNBAN = label("Снять блокировку", "🔓")
KEEPER_MUTE = label("Замолчать в группе", "🔇")
KEEPER_UNMUTE = label("Вернуть слово в группе", "🔊")
KEEPER_WARN = label("Вынести предупреждение", "⚠️")
KEEPER_UNWARN = label("Снять предупреждение", "🧯")
KEEPER_REASON = label("Указать причину", "✍")
KEEPER_LOG = label("Журнал", "📜")
KEEPER_PLAYER_LOG = label("Журнал по игроку", "📜")
KEEPER_GOLD_FLOW_BTN = label("Движения золота", "📓")
KEEPER_PROMOTE = label("Сделать смотрителем", "🗝")
KEEPER_DEMOTE = label("Убрать из смотрителей", "🚷")
KEEPER_TRADES = label("Сделки", "🤝")

# Отряд и гильдия игрока.
KEEPER_PARTY_BTN = label("Отряд игрока", "🫂")
KEEPER_GUILD_BTN = label("Гильдия игрока", "🏛")
KEEPER_PARTY_DISBAND = label("Расформировать отряд игрока", "💥")
KEEPER_GUILD_DISBAND = label("Распустить гильдию игрока", "💥")
KEEPER_VAULT_SET = label("Казна: выставить число", "💰")


def keeper_group_kick_label(number: int) -> Label:
    return label(f"Вывести {number}")


def keeper_rank_up_label(number: int) -> Label:
    return label(f"Повысить {number}")


def keeper_rank_down_label(number: int) -> Label:
    return label(f"Понизить {number}")


# Обслуживание.
# Живые операции (ADR 0045).
KEEPER_OPS_BTN = label("Живые операции", "🛠")
KEEPER_OPS_MAINT_ON = label("Включить режим обслуживания", "🚧")
KEEPER_OPS_MAINT_OFF = label("Снять режим обслуживания", "🟢")
KEEPER_OPS_ANNOUNCE = label("Объявить в канал", "📣")
KEEPER_OPS_FREE_BATTLE = label("Снять замок боя", "⚔")
KEEPER_OPS_RESET_PLAYER = label("Сбросить экран игрока", "🔄")
KEEPER_OPS_RESET_LOC = label("Сбросить локацию", "🌲")

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
# Полный расклад темпа - намерения врагов, след, что дают три стойки - живёт на
# отдельном экране, а не абзацем на боевой панели: на слух абзац между делом это
# стена (ADR 0050).
BATTLE_BREAKDOWN = label("Разбор боя", "📖")

# --- отряд (``domain/rules/party.py``) ---

PARTY = label("Отряд", "🤝")
PARTY_CREATE = label("Создать отряд", "🤝")
PARTY_DISBAND = label("Расформировать отряд", "🚪")
PARTY_INVITE = label("Пригласить в отряд", "✉️")
PARTY_ACCEPT = label("Пойти вместе", "🤝")
PARTY_DECLINE = label("Отказаться", "🙅")
PARTY_LEAVE = label("Покинуть отряд", "🚪")
PARTY_TRANSFER = label("Передать соратнику", "🎁")


# --- гильдия (``domain/rules/guild.py``) ---

GUILD = label("Гильдия", "🏛")
GUILD_FOUND = label("Основать гильдию", "🏛")
GUILD_DISBAND = label("Распустить гильдию", "🚪")
GUILD_INVITE = label("Позвать в гильдию", "✉️")
GUILD_ACCEPT = label("Вступить в гильдию", "🏛")
GUILD_DECLINE = label("Отклонить", "🙅")
GUILD_LEAVE = label("Выйти из гильдии", "🚪")
GUILD_ROSTER = label("Состав гильдии", "📋")
GUILD_VAULT = label("Казна гильдии", "💰")
GUILD_TRANSFER = label("Передать соклановцу", "🎁")


# --- великие дома (``domain/rules/houses.py``) ---

HOUSE_JOIN = label("Вступить в дом", "🏰")
HOUSE_LEAVE = label("Уйти из дома", "🚪")


# --- передача вещей в отряде и гильдии (``handlers/play._transfer_step``) ---

TRANSFER_ALL = label("Передать всё", "🎁")


def guild_deposit_label(sum_: int) -> Label:
    return label(f"Внести {sum_}")


def guild_withdraw_label(sum_: int) -> Label:
    return label(f"Взять {sum_}")


def guild_promote_label(name: str) -> Label:
    return label(f"Повысить: {name}")


def guild_demote_label(name: str) -> Label:
    return label(f"Понизить: {name}")


def guild_kick_label(name: str) -> Label:
    return label(f"Выгнать: {name}")
