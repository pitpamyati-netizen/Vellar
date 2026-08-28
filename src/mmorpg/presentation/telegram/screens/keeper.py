"""Панель смотрителя: служебная дверь, а не часть игры.

Рисуется только для тех, чей Telegram id стоит в ``ADMIN_IDS``, и говорит об этом
первой строкой: экран, который игрок слышать не должен, обязан назваться раньше,
чем что-то предложит.

Правил здесь два, и оба взяты у остальной игры, а не придуманы для служебного
экрана. Первое: каждое действие отвечает числом, которое получилось, — смотритель
сверяет игру, а не выслушивает заверения. Второе: доступность обычная. Служебный
ряд на месте, псевдографики нет, значение поля читается словом («не заполнено»,
«вольная земля: да»), а не пустотой, которую нечем озвучить.

Карточка сущности рисуется по описанию полей из ``domain/rules/overlay.py``, а не
по списку кнопок, набранному здесь: новое поле у задания должно появляться на
экране само, иначе панель начнёт отставать от того, что она правит.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import (
    GameContent,
    GearArchetype,
    Item,
    ItemKind,
    OwnerKind,
    Rarity,
    SkillKind,
)
from mmorpg.domain.entities.moderation import Ban, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import Quest
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.entities.trade import OfferKind, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import Census
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.keeper import GOLD_STEP, POINTS_STEP
from mmorpg.domain.rules.overlay import FieldKind, FieldSpec
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.creation import STAT_NAMES
from mmorpg.presentation.telegram.screens.format import amount, duration, gold
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

#: Сколько полей карточки на одной странице и сколько знаков значения видно в
#: строке. Пять и восемьдесят вместо восьми и тридцати четырёх: строку карточки
#: слушают, и лучше услышать пять полных строк, чем восемь оборванных. Полное
#: значение всегда есть на экране поля (``field_screen``).
CARD_FIELDS = 5
CARD_VALUE = 80

#: Сколько причин отказа показывать на карточке. Три - это уже понятно, что
#: запись недописана; остальные считаются числом.
PROBLEMS_SHOWN = 3

#: Порядок разновидностей на экране содержимого: сначала люди, потом работа, потом
#: земля. Тот же порядок, в котором о мире рассказывают.
KINDS: tuple[OverlayKind, ...] = (
    OverlayKind.NPC,
    OverlayKind.QUEST,
    OverlayKind.LOCATION,
    OverlayKind.ENEMY,
    OverlayKind.CITY,
)


@dataclass(frozen=True, slots=True)
class KeeperView:
    """Всё, что панель показывает, но не считает сама.

    Читает это хендлер - и только когда открыт экран смотрителя: игроку эти
    запросы не стоят ничего, потому что не выполняются.
    """

    records: tuple[OverlayRecord, ...] = ()
    players: tuple[Character, ...] = ()
    target: Character | None = None
    census: Census | None = None
    found: str = ""
    #: Пришло ли право того, кто смотрит, из настройки, а не из игры. Читается
    #: только на карточке игрока; всё остальное в панели одинаково для всех.
    granting: bool = False
    #: Держит ли право аккаунт открытого игрока. Читается по аккаунту, а не по
    #: флагу персонажа: персонажей у него может быть несколько.
    target_keeper: bool = False
    #: Пришло ли право открытого игрока из настройки. Такое из игры не снимается.
    target_locked: bool = False
    #: Что висит на аккаунте открытого игрока. Пустая — не заблокирован.
    target_ban: Ban = field(default_factory=Ban)
    #: Последние записи журнала смотрителя, свежие сначала.
    log: tuple[KeeperEntry, ...] = ()
    #: Последние сделки открытого игрока, свежие сначала.
    trades: tuple[TradeRecord, ...] = ()
    #: Момент, которым меряется остаток срока. Ноль — сроков на экране нет.
    now: int = 0


def kind_label(kind: OverlayKind) -> Label:
    return label(overlay_rules.TITLES[kind][1])


def kind_from_button(pressed: str) -> OverlayKind | None:
    return next((kind for kind in KINDS if kind_label(kind).matches(pressed)), None)


# --- панель ------------------------------------------------------------


def keeper_screen(
    content: GameContent,
    character: Character,
    stats: DerivedStats,
    view: KeeperView = KeeperView(),
    notice: str = "",
) -> Screen:
    """Четыре двери и четыре быстрых выдачи себе."""
    broken = sum(1 for record in view.records if overlay_rules.problems(content, record))
    standing = f"Правок в мире: {len(view.records)}."
    if broken:
        standing += f" Из них не работают: {broken}."
    lines = [
        notice or "Смотритель. Служебный экран, игроки его не видят.",
        f"{character.name}, уровень {character.level}, "
        f"{content.race(character.race_id).name}, "
        f"{content.character_class(character.class_id).name}.",
        f"Здоровье: {amount(character.health_or(stats.max_health), stats.max_health)}. "
        f"Золото: {gold(character.gold)}.",
        standing,
        f"Кнопки внизу дают вам: {GOLD_STEP} золота, один уровень, "
        f"полное здоровье, по {POINTS_STEP} очков каждого рода.",
        "Города открыты все, независимо от уровня.",
    ]
    return Screen(
        id=ScreenId.KEEPER,
        lines=tuple(lines),
        rows=(
            (labels.KEEPER_WORLD, labels.KEEPER_PLAYERS),
            (labels.KEEPER_STATS, labels.KEEPER_SERVICE),
            (labels.KEEPER_LOG,),
            (labels.KEEPER_GOLD, labels.KEEPER_LEVEL),
            (labels.KEEPER_HEAL, labels.KEEPER_POINTS),
            (labels.KEEPER_TUNE,),
        ),
    )


def content_screen(content: GameContent, view: KeeperView, notice: str = "") -> Screen:
    """Из чего состоит мир, и сколько чего в нём сейчас."""
    counts = {kind: len(overlay_rules.listing(content, kind)) for kind in KINDS}
    lines = [
        notice or "Мир и содержимое. Здесь правят то, из чего игра сделана.",
        "Правка ложится поверх файлов в content и снимается целиком: "
        "исходная строка никуда не девается.",
        *(f"{overlay_rules.TITLES[kind][1]}: {counts[kind]}." for kind in KINDS),
        f"Правок стоит: {len(view.records)}.",
    ]
    empty = overlay_rules.orphaned_biomes(content)
    if empty:
        lines.append(f"Некому водиться в местностях: {', '.join(empty)}. Там не с кем драться.")
    rows: list[tuple[Label, ...]] = [
        tuple(kind_label(kind) for kind in KINDS[index : index + 2])
        for index in range(0, len(KINDS), 2)
    ]
    rows.append((labels.KEEPER_RELOAD,))
    return Screen(id=ScreenId.KEEPER_CONTENT, lines=tuple(lines), rows=tuple(rows))


# --- список сущностей --------------------------------------------------


def numbered(index: int, text: str) -> str:
    """Строка списка с номером.

    Номер здесь не украшение: два жителя могут зваться одинаково, а маршрутизация
    идёт по тексту кнопки, и две одинаковые кнопки - это экран, который не
    соберётся (``screens/base.py``).
    """
    return f"{index}. {text}"


def list_screen(
    content: GameContent,
    kind: OverlayKind,
    state: PageState,
    view: KeeperView = KeeperView(),
    notice: str = "",
) -> Screen:
    entries = [
        ListEntry(key=entity_id, text=numbered(index, title))
        for index, (entity_id, title) in enumerate(overlay_rules.listing(content, kind), start=1)
    ]
    edited = sum(1 for record in view.records if record.kind is kind)
    single, many = overlay_rules.TITLES[kind]
    rows: tuple[tuple[Label, ...], ...] = (
        ((labels.KEEPER_ADD,),) if kind in overlay_rules.CREATABLE else ()
    )
    return paginated_screen(
        screen_id=ScreenId.KEEPER_LIST,
        title=many,
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"{many}. Нажмите запись, чтобы открыть карточку.",
            f"Правок в этом разделе: {edited}.",
        ),
        empty_text=f"Ни одной записи. {single} можно добавить кнопкой ниже.",
        extra_rows=rows,
        show_filters=False,
    )


def entity_from_button(content: GameContent, kind: OverlayKind, pressed: str) -> str:
    """Идентификатор сущности по нажатой строке списка. Пусто - не узнали."""
    for index, (entity_id, title) in enumerate(overlay_rules.listing(content, kind), start=1):
        if pressed.strip() == numbered(index, title):
            return entity_id
    return ""


# --- карточка сущности -------------------------------------------------


def field_button(spec: FieldSpec) -> Label:
    """Кнопка поля — его название. Что в нём стоит, сказано строкой над ней.

    Значение на кнопке пришлось бы резать до неузнаваемости: у задания условие
    занимает две строки, а кнопка — одну.
    """
    return label(spec.name)


def field_from_button(record: OverlayRecord, pressed: str) -> FieldSpec | None:
    for spec in overlay_rules.fields_for(record):
        if field_button(spec).matches(pressed):
            return spec
    return None


def entity_screen(
    content: GameContent,
    record: OverlayRecord,
    state: PageState,
    view: KeeperView = KeeperView(),
    notice: str = "",
) -> Screen:
    """Одна сущность: что в ней стоит, и что с ней можно сделать.

    Карточка — это список полей, поэтому она и рисуется списком: у задания их
    четырнадцать, и все четырнадцать в одном сообщении не поместились бы. Строка
    говорит, что стоит в поле, кнопка под ней его меняет.
    """
    single = overlay_rules.TITLES[record.kind][0]
    stored = overlay_rules.held(view.records, record.kind, record.entity_id)
    entries = [
        ListEntry(key=spec.key, text=spec.name, detail=_short(content, spec, record))
        for spec in overlay_rules.fields_for(record)
    ]

    named = overlay_rules.clipped(record.value("name")) or record.entity_id
    lead = [
        notice or f"{single}: {named}.",
        f"Ключ: {record.entity_id}. {_standing(record, stored)}",
    ]
    why = overlay_rules.problems(content, record)
    if why:
        lead.append("Пока не работает в игре:")
        lead.extend(why[:PROBLEMS_SHOWN])
        if len(why) > PROBLEMS_SHOWN:
            lead.append(f"И ещё причин: {len(why) - PROBLEMS_SHOWN}.")

    rows: list[tuple[Label, ...]] = [
        (labels.KEEPER_RETURN,) if record.removed else (labels.KEEPER_REMOVE,)
    ]
    if stored is not None:
        rows.append((labels.KEEPER_FORGET,))
    return paginated_screen(
        screen_id=ScreenId.KEEPER_ENTITY,
        title=single,
        entries=entries,
        state=state,
        page_size=CARD_FIELDS,
        lead_lines=tuple(lead),
        extra_rows=tuple(rows),
        show_filters=False,
    )


def _short(content: GameContent, spec: FieldSpec, record: OverlayRecord) -> str:
    """Значение поля так, как оно помещается в строку карточки.

    Обрезка тут есть и останется: карточка читается вслух целиком, и четырнадцать
    полей по шестьсот знаков — это не карточка. Но 34 знака, оставшиеся от времён,
    когда значение попадало на кнопку, обрывали условие задания на третьем слове,
    и смотритель считал, что панель съела набранное. Поля на странице теперь
    вдвое меньше, а видно каждого вдвое больше, и полное значение всегда на один
    шаг ниже — на экране самого поля.
    """
    value = overlay_rules.shown(content, spec, record)
    if len(value) > CARD_VALUE:
        value = f"{value[:CARD_VALUE].rstrip()}…"
    return value


def _standing(record: OverlayRecord, stored: OverlayRecord | None) -> str:
    if record.removed:
        return "Убрано из игры. Вернуть можно кнопкой ниже."
    if stored is None:
        return "Из содержимого, правок нет."
    if record.is_keepers:
        return "Заведено смотрителем."
    return "Из содержимого, с правкой смотрителя."


# --- одно поле ---------------------------------------------------------


def field_screen(
    content: GameContent,
    record: OverlayRecord,
    spec: FieldSpec,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Значение одного поля: выбрать из списка или набрать сообщением."""
    lead = [
        notice or f"{spec.name}. Сейчас: {overlay_rules.shown(content, spec, record)}.",
        _how_to_fill(spec),
    ]
    if spec.hint:
        lead.append(f"Например: {spec.hint}.")

    if spec.kind in {FieldKind.CHOICE, FieldKind.LIST}:
        return _choice_screen(content, record, spec, state, lead)

    rows: list[tuple[Label, ...]] = []
    if spec.kind is FieldKind.FLAG:
        rows.append((label("Да"), label("Нет")))
    if not spec.required:
        rows.append((labels.KEEPER_CLEAR,))
    return Screen(id=ScreenId.KEEPER_FIELD, lines=tuple(lead), rows=tuple(rows))


def _how_to_fill(spec: FieldSpec) -> str:
    match spec.kind:
        case FieldKind.NUMBER:
            return "Наберите целое число сообщением."
        case FieldKind.RATE:
            return "Наберите долю сообщением: 1 — обычная, 1,5 — в полтора раза больше."
        case FieldKind.FLAG:
            return "Нажмите «Да» или «Нет»."
        case FieldKind.LIST:
            return "Нажимайте варианты: нажатое добавляется, нажатое второй раз убирается."
        case FieldKind.CHOICE:
            return "Нажмите вариант из списка."
        case _:
            return "Наберите значение сообщением. Оно заменит то, что стоит сейчас."


def option_button(index: int, name: str, *, chosen: bool) -> Label:
    """Вариант списка. Отмеченный говорит об этом словом, а не значком."""
    return label(numbered(index, f"{name}{', выбрано' if chosen else ''}"))


def _choice_screen(
    content: GameContent,
    record: OverlayRecord,
    spec: FieldSpec,
    state: PageState,
    lead: Sequence[str],
) -> Screen:
    picked = (
        set(record.listed(spec.key)) if spec.kind is FieldKind.LIST else {record.value(spec.key)}
    )
    entries = [
        ListEntry(
            key=value,
            text=option_button(
                index,
                overlay_rules.option_name(content, spec, value, record),
                chosen=value in picked,
            ).text,
        )
        for index, value in enumerate(overlay_rules.options(content, spec, record), start=1)
    ]
    extra: tuple[tuple[Label, ...], ...] = () if spec.required else ((labels.KEEPER_CLEAR,),)
    return paginated_screen(
        screen_id=ScreenId.KEEPER_FIELD,
        title=spec.name,
        entries=entries,
        state=state,
        lead_lines=tuple(lead),
        empty_text="Выбирать не из чего: сначала заполните поля выше.",
        extra_rows=extra,
        show_filters=False,
    )


def option_from_button(
    content: GameContent, record: OverlayRecord, spec: FieldSpec, pressed: str
) -> str | None:
    picked = (
        set(record.listed(spec.key)) if spec.kind is FieldKind.LIST else {record.value(spec.key)}
    )
    for index, value in enumerate(overlay_rules.options(content, spec, record), start=1):
        name = overlay_rules.option_name(content, spec, value, record)
        if option_button(index, name, chosen=value in picked).matches(pressed):
            return value
    return None


# --- игроки ------------------------------------------------------------


def player_entry(content: GameContent, index: int, player: Character) -> str:
    city = content.city(player.city_id).name if content.has_city(player.city_id) else player.city_id
    return numbered(index, f"{player.name} — уровень {player.level}, {city}")


def players_screen(
    content: GameContent, view: KeeperView, state: PageState, notice: str = ""
) -> Screen:
    entries = [
        ListEntry(key=str(player.id), text=player_entry(content, index, player))
        for index, player in enumerate(view.players, start=1)
    ]
    return paginated_screen(
        screen_id=ScreenId.KEEPER_PLAYERS,
        title="Игроки",
        entries=entries,
        state=state,
        lead_lines=(
            notice or "Последние заведённые персонажи. Кого нет в списке — ищите по имени.",
            "Имя набирается сообщением после нажатия «Найти по имени».",
        ),
        empty_text="Ни одного персонажа.",
        extra_rows=((labels.KEEPER_FIND,),),
        show_filters=False,
    )


def player_from_button(content: GameContent, view: KeeperView, pressed: str) -> Character | None:
    for index, player in enumerate(view.players, start=1):
        if pressed.strip() == player_entry(content, index, player):
            return player
    return None


def player_screen(
    content: GameContent,
    player: Character,
    stats: DerivedStats,
    notice: str = "",
    view: KeeperView = KeeperView(),
) -> Screen:
    """Чужой персонаж и то же, что смотритель может сделать себе.

    Раздача самого права смотрителя дорисовывается только тому, чьё право пришло
    из настройки: остальные не видят ни строки об этом, ни кнопки, потому что
    экран для них выглядит ровно так же, как выглядел всегда.
    """
    city = content.city(player.city_id).name if content.has_city(player.city_id) else player.city_id
    lines = [
        notice or f"{player.name}, уровень {player.level}.",
        f"{content.race(player.race_id).name}, "
        f"{content.character_class(player.class_id).name}. Аккаунт: {player.user_id}.",
        f"Здоровье: {amount(player.health_or(stats.max_health), stats.max_health)}.",
        f"Золото: {gold(player.gold)}, в ячейке {gold(player.bank_gold)}. Город: {city}.",
        f"Нераспределено: очков характеристик {player.unspent_stat_points}, "
        f"очков умений {player.unspent_skill_points}.",
        f"Заданий закрыто: {len(player.quests.done)}. "
        f"Арена: {player.arena_wins} побед, {player.arena_losses} поражений.",
        ban_line(view.target_ban, view.now),
        "Уровень поднимается по одному и только вверх: очки уже вложены.",
    ]
    rows: list[tuple[Label, ...]] = [
        (labels.KEEPER_GOLD, labels.KEEPER_LEVEL),
        (labels.KEEPER_HEAL, labels.KEEPER_POINTS),
        (labels.KEEPER_TUNE, labels.KEEPER_GIVE_ITEM),
        (labels.KEEPER_SKILLS, labels.KEEPER_STATS_EDIT_BTN),
        (labels.KEEPER_QUESTS_BTN, labels.KEEPER_MOVE),
        (labels.KEEPER_TRADES,),
        (labels.KEEPER_UNBAN,) if _under_ban(view) else (labels.KEEPER_BAN,),
        (labels.KEEPER_DELETE,),
    ]
    if view.granting:
        lines.append(_right(view))
        if not view.target_locked:
            rows.insert(
                2, (labels.KEEPER_DEMOTE,) if view.target_keeper else (labels.KEEPER_PROMOTE,)
            )
    return Screen(id=ScreenId.KEEPER_PLAYER, lines=tuple(lines), rows=tuple(rows))


def _under_ban(view: KeeperView) -> bool:
    return moderation_rules.is_banned(view.target_ban, now=view.now)


def ban_line(ban: Ban, now: int) -> str:
    """Одна строка о блокировке. Её же читает и сам заблокированный."""
    if not moderation_rules.is_banned(ban, now=now):
        return "Блокировка: нет."
    left = (
        "навсегда"
        if ban.forever
        else f"осталось {duration(moderation_rules.remaining(ban, now=now))}"
    )
    because = f" Причина: {ban.reason}." if ban.reason else " Причина не названа."
    return f"Блокировка: есть, {left}.{because}"


def _right(view: KeeperView) -> str:
    if view.target_locked:
        return "Права смотрителя: есть, из настройки. Отсюда не снимаются."
    if view.target_keeper:
        return "Права смотрителя: есть."
    return "Права смотрителя: нет."


# --- точные правки ----------------------------------------------------

#: Точные правки персонажа: те же выдачи, что быстрые кнопки, но числом или
#: словом, а не фиксированным шагом. Ключ — то, чем правка лежит в состоянии
#: автомата (``state.keeper_field``); порядок — то, в котором рисуются кнопки.
TUNE_KEYS: tuple[str, ...] = (
    "gold",
    "bank",
    "health",
    "level",
    "stat_points",
    "skill_points",
    "name",
)

_TUNE_LABEL: dict[str, Label] = {
    "gold": labels.KEEPER_SET_GOLD,
    "bank": labels.KEEPER_SET_BANK,
    "health": labels.KEEPER_SET_HEALTH,
    "level": labels.KEEPER_SET_LEVEL,
    "stat_points": labels.KEEPER_ADD_STAT_POINTS,
    "skill_points": labels.KEEPER_ADD_SKILL_POINTS,
    "name": labels.KEEPER_RENAME,
}

#: Единственная правка, которую набирают словом, а не числом.
TUNE_TEXT: frozenset[str] = frozenset({"name"})


def tune_label(key: str) -> Label:
    return _TUNE_LABEL[key]


def tune_key_from_button(pressed: str) -> str | None:
    return next((key for key in TUNE_KEYS if _TUNE_LABEL[key].matches(pressed)), None)


def _tune_current(subject: Character, stats: DerivedStats, key: str) -> str:
    match key:
        case "gold":
            return gold(subject.gold)
        case "bank":
            return gold(subject.bank_gold)
        case "health":
            return amount(subject.health_or(stats.max_health), stats.max_health)
        case "level":
            return str(subject.level)
        case "stat_points":
            return f"нераспределено {subject.unspent_stat_points}"
        case "skill_points":
            return f"нераспределено {subject.unspent_skill_points}"
        case _:
            return subject.name


def _tune_how(key: str) -> str:
    match key:
        case "gold":
            return "Наберите число со знаком: 500 добавит, -500 спишет."
        case "bank" | "health":
            return "Наберите число: оно станет новым значением."
        case "level":
            return "Наберите уровень: поднимется до него, по одному. Понизить нельзя."
        case "stat_points" | "skill_points":
            return "Наберите число: столько очков придёт сверх нынешних. Отнять нельзя."
        case _:
            return "Наберите новое имя сообщением."


def tune_screen(subject: Character, stats: DerivedStats, *, own: bool, notice: str = "") -> Screen:
    """Меню точных правок персонажа: то же, что быстрые кнопки, но числом."""
    whose = "ваш персонаж" if own else subject.name
    lines = (
        notice or f"Задать точно: {whose}.",
        f"Золото {gold(subject.gold)}, в ячейке {gold(subject.bank_gold)}. "
        f"Здоровье {amount(subject.health_or(stats.max_health), stats.max_health)}. "
        f"Уровень {subject.level}.",
        f"Очков нераспределено: характеристик {subject.unspent_stat_points}, "
        f"умений {subject.unspent_skill_points}.",
        "Нажмите, что менять. Значение наберёте следующим сообщением.",
    )
    rows = tuple(
        tuple(_TUNE_LABEL[key] for key in TUNE_KEYS[index : index + 2])
        for index in range(0, len(TUNE_KEYS), 2)
    )
    return Screen(id=ScreenId.KEEPER_TUNE, lines=lines, rows=rows)


def amount_screen(
    key: str, subject: Character, stats: DerivedStats, *, own: bool, notice: str = ""
) -> Screen:
    """Одно значение точной правки: набрать число или имя сообщением."""
    whose = "ваш персонаж" if own else subject.name
    lines = (
        notice or f"{_TUNE_LABEL[key].text}: {whose}.",
        f"Сейчас: {_tune_current(subject, stats, key)}.",
        _tune_how(key),
    )
    return Screen(id=ScreenId.KEEPER_AMOUNT, lines=lines)


# --- выдать вещь -----------------------------------------------------

_KIND_WORD: dict[ItemKind, str] = {
    ItemKind.CONSUMABLE: "расходник",
    ItemKind.MATERIAL: "сырьё",
}


def written_items(content: GameContent) -> tuple[Item, ...]:
    """То, что в ``content`` пишут руками: расходники и сырьё."""
    return tuple(item for item in content.items if item.kind is not ItemKind.EQUIPMENT)


def _slot_word(content: GameContent, slot_id: str) -> str:
    return content.slot(slot_id).name if content.has_slot(slot_id) else slot_id


def give_entries(content: GameContent) -> tuple[tuple[str, str], ...]:
    """Что можно выдать: сперва виды снаряжения, потом написанные вещи.

    Ключ — ``gear:<вид>`` или ``item:<вещь>``: снаряжение собирается дальше из
    ступени и редкости, написанная вещь даётся числом сразу.
    """
    gear = tuple(
        (f"gear:{archetype.id}", f"{archetype.noun} — {_slot_word(content, archetype.slot)}")
        for archetype in content.gear_archetypes
    )
    written = tuple(
        (f"item:{item.id}", f"{item.name} — {_KIND_WORD.get(item.kind, 'вещь')}")
        for item in written_items(content)
    )
    return (*gear, *written)


def give_screen(
    content: GameContent, player: Character, state: PageState, notice: str = ""
) -> Screen:
    """Список того, что смотритель может выдать в сумку игрока."""
    entries = [
        ListEntry(key=key, text=numbered(index, text))
        for index, (key, text) in enumerate(give_entries(content), start=1)
    ]
    return paginated_screen(
        screen_id=ScreenId.KEEPER_GIVE,
        title="Выдать вещь",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Выдать вещь: {player.name}. Нажмите вид снаряжения или написанную вещь.",
            "Снаряжение соберётся из вида, ступени и редкости; расходник и сырьё дают числом.",
        ),
        show_filters=False,
    )


def give_from_button(content: GameContent, pressed: str) -> tuple[str, str] | None:
    for index, (key, text) in enumerate(give_entries(content), start=1):
        if pressed.strip() == numbered(index, text):
            kind, _, ident = key.partition(":")
            return kind, ident
    return None


def rarity_label(rarity: Rarity) -> Label:
    return label(rarity.name)


def rarity_from_button(content: GameContent, pressed: str) -> Rarity | None:
    return next(
        (rarity for rarity in content.rarities if rarity_label(rarity).matches(pressed)), None
    )


def give_gear_screen(
    content: GameContent,
    player: Character,
    archetype: GearArchetype,
    level: int,
    notice: str = "",
) -> Screen:
    """Ступень и редкость для собираемой вещи. Ступень — по уровню игрока или числом."""
    tier = item_procgen.tier_at(content, level)
    tier_level = tier.level if tier is not None else level
    named = f" ({tier.named(archetype.gender)})" if tier is not None else ""
    lines = (
        notice or f"Выдать: {archetype.noun}. Кому: {player.name}.",
        f"Ступень: уровень {tier_level}{named}. Наберите номер уровня, чтобы сменить.",
        "Нажмите редкость — вещь соберётся и ляжет в сумку.",
    )
    rarities = content.rarities
    rows: list[tuple[Label, ...]] = [
        tuple(rarity_label(rarity) for rarity in rarities[index : index + 2])
        for index in range(0, len(rarities), 2)
    ]
    rows.append((labels.KEEPER_GIVE_AT_PLAYER_LEVEL,))
    return Screen(id=ScreenId.KEEPER_GIVE_GEAR, lines=lines, rows=tuple(rows))


def give_item_screen(item: Item, player: Character, notice: str = "") -> Screen:
    """Сколько написанной вещи выдать. Отрицательное число — убрать из сумки."""
    lines = (
        notice or f"Выдать: {item.name}. Кому: {player.name}.",
        "Наберите количество сообщением. Со знаком минус — убрать столько из сумки.",
    )
    return Screen(id=ScreenId.KEEPER_GIVE_ITEM, lines=lines)


# --- умения игрока --------------------------------------------------

_SKILL_KIND_WORD: dict[SkillKind, str] = {
    SkillKind.ACTIVE: "боевое",
    SkillKind.PASSIVE: "пассивное",
}


def skill_line(content: GameContent, player: Character, code: str) -> str:
    """Одна строка об умении игрока: ранг, грань, слот."""
    if not content.has_skill(code):
        return f"{code} — умения такого в игре больше нет"
    skill = content.skill(code)
    rank = player.loadout.rank_of(code)
    parts = [f"{skill.name} — ранг {rank} из {content.rules.max_rank}"]
    edge = player.loadout.edge_of(code)
    if edge:
        named = next((one.name for one in skill.edges if one.code == edge), edge)
        parts.append(f"грань {named}")
    slot = next((index + 1 for index, held in enumerate(player.loadout.actives) if held == code), 0)
    if slot:
        parts.append(f"слот {slot}")
    if code == player.loadout.racial:
        parts.append("расовое")
    return ", ".join(parts)


def player_skills_screen(
    content: GameContent, player: Character, state: PageState, notice: str = ""
) -> Screen:
    """Все умения игрока: нажать — карточка; плюс изучить и сбросить дерево."""
    codes = sorted(player.loadout.ranks)
    entries = [
        ListEntry(key=code, text=numbered(index, skill_line(content, player, code)))
        for index, code in enumerate(codes, start=1)
    ]
    return paginated_screen(
        screen_id=ScreenId.KEEPER_SKILLS,
        title="Умения",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Умения: {player.name}. Нажмите умение, чтобы его править.",
            f"Нераспределено очков умений: {player.unspent_skill_points}.",
        ),
        empty_text="Ни одного изученного умения.",
        extra_rows=((labels.KEEPER_SKILL_LEARN,), (labels.KEEPER_SKILL_RESPEC,)),
        show_filters=False,
    )


def player_skill_from_button(content: GameContent, player: Character, pressed: str) -> str:
    for index, code in enumerate(sorted(player.loadout.ranks), start=1):
        if pressed.strip() == numbered(index, skill_line(content, player, code)):
            return code
    return ""


def teach_pool(content: GameContent, player: Character) -> tuple[tuple[str, str], ...]:
    known = set(player.loadout.ranks)
    return tuple(
        (skill.code, f"{skill.name} — {_SKILL_KIND_WORD.get(skill.kind, 'умение')}")
        for skill in skill_rules.teachable(content, player)
        if skill.code not in known and skill.owner_kind is OwnerKind.CLASS
    )


def skill_learn_screen(
    content: GameContent, player: Character, state: PageState, notice: str = ""
) -> Screen:
    pool = teach_pool(content, player)
    entries = [
        ListEntry(key=code, text=numbered(index, text))
        for index, (code, text) in enumerate(pool, start=1)
    ]
    return paginated_screen(
        screen_id=ScreenId.KEEPER_SKILL_LEARN,
        title="Изучить умение",
        entries=entries,
        state=state,
        lead_lines=(notice or f"Изучить умение: {player.name}. Даётся первым рангом, без очков.",),
        empty_text="Все умения класса этому игроку уже открыты.",
        show_filters=False,
    )


def skill_learn_from_button(content: GameContent, player: Character, pressed: str) -> str:
    for index, (code, text) in enumerate(teach_pool(content, player), start=1):
        if pressed.strip() == numbered(index, text):
            return code
    return ""


def keeper_skill_screen(
    content: GameContent, player: Character, code: str, notice: str = ""
) -> Screen:
    """Одно умение игрока: ранг, грань, слот, забыть."""
    skill = content.skill(code)
    rows: list[tuple[Label, ...]] = [(labels.KEEPER_RANK_UP, labels.KEEPER_RANK_DOWN)]
    if skill.edges:
        rows.append((labels.KEEPER_SKILL_EDGE_BTN, labels.KEEPER_SKILL_EDGE_CLEAR))
    if skill.kind is SkillKind.ACTIVE and skill.owner_kind is OwnerKind.CLASS:
        rows.append((labels.KEEPER_SKILL_SLOT_BTN, labels.KEEPER_SKILL_SLOT_CLEAR))
    if code != player.loadout.racial:
        rows.append((labels.KEEPER_SKILL_FORGET,))
    branch = skill_rules.branch_of(skill)
    where = (
        f"Ветвь: {skill_rules.BRANCH_NAMES[branch]}."
        if branch is not None
        else "Вне классового дерева."
    )
    lines = (
        notice or f"Умение: {skill.name}. Кому: {player.name}.",
        skill_line(content, player, code) + ".",
        where,
    )
    return Screen(id=ScreenId.KEEPER_SKILL, lines=lines, rows=tuple(rows))


def skill_edge_screen(
    content: GameContent, player: Character, code: str, notice: str = ""
) -> Screen:
    skill = content.skill(code)
    lines = (
        notice or f"Грань умения {skill.name}. Кому: {player.name}.",
        *(f"{one.name}: {one.text}" for one in skill.edges),
    )
    rows = tuple((label(one.name),) for one in skill.edges)
    return Screen(id=ScreenId.KEEPER_SKILL_EDGE, lines=lines, rows=rows)


def skill_edge_from_button(content: GameContent, code: str, pressed: str) -> str:
    for one in content.skill(code).edges:
        if label(one.name).matches(pressed):
            return one.code
    return ""


def skill_slot_screen(
    content: GameContent, player: Character, code: str, notice: str = ""
) -> Screen:
    slots = content.rules.active_slots
    lines = (
        notice or f"В какой слот: {content.skill(code).name}. Кому: {player.name}.",
        f"Слотов {slots}. Умение лежит разом в одном.",
    )
    rows = tuple(
        tuple(label(f"Слот {number}") for number in range(start, min(start + 3, slots + 1)))
        for start in range(1, slots + 1, 3)
    )
    return Screen(id=ScreenId.KEEPER_SKILL_SLOT, lines=lines, rows=rows)


def skill_slot_from_button(content: GameContent, pressed: str) -> int:
    for number in range(1, content.rules.active_slots + 1):
        if label(f"Слот {number}").matches(pressed):
            return number
    return 0


# --- характеристики игрока ------------------------------------------


def stat_name(code: StatCode) -> str:
    return STAT_NAMES[code]


def stats_edit_screen(player: Character, *, chosen: str = "", notice: str = "") -> Screen:
    """Вложенное в каждую характеристику. Нажать характеристику, затем набрать число."""
    order = list(StatCode)
    picked = (
        f"Выбрано: {stat_name(StatCode(chosen))}. Наберите новое значение."
        if chosen in {code.value for code in StatCode}
        else "Нажмите характеристику, затем наберите новое значение."
    )
    lines = (
        notice or f"Характеристики: {player.name}.",
        ", ".join(f"{stat_name(code)} {player.allocated[code]}" for code in order),
        f"Нераспределено очков характеристик: {player.unspent_stat_points}.",
        picked,
    )
    rows = tuple(
        tuple(label(stat_name(code)) for code in order[index : index + 2])
        for index in range(0, len(order), 2)
    )
    return Screen(id=ScreenId.KEEPER_STATS_EDIT, lines=lines, rows=rows)


def stat_from_button(pressed: str) -> str:
    for code in StatCode:
        if label(stat_name(code)).matches(pressed):
            return code.value
    return ""


# --- задания игрока -------------------------------------------------


def _quest_state_word(player: Character, quest: Quest) -> str:
    log = player.quests
    if log.is_done(quest.id):
        return "закрыто"
    if log.is_taken(quest.id):
        return f"взято {log.progress(quest.id)} из {quest.target_count}"
    return "не в журнале"


def _quest_line(content: GameContent, player: Character, quest: Quest) -> str:
    city = content.city(quest.city_id).name if content.has_city(quest.city_id) else quest.city_id
    return f"{quest.name} — {city}, {_quest_state_word(player, quest)}"


def player_quests_screen(
    content: GameContent, player: Character, state: PageState, notice: str = ""
) -> Screen:
    entries = [
        ListEntry(key=quest.id, text=numbered(index, _quest_line(content, player, quest)))
        for index, quest in enumerate(content.quests, start=1)
    ]
    return paginated_screen(
        screen_id=ScreenId.KEEPER_QUESTS,
        title="Задания",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Задания: {player.name}. Нажмите задание, чтобы поправить его в журнале.",
            f"Закрыто у игрока: {len(player.quests.done)}. Взято: {len(player.quests.taken)}.",
        ),
        empty_text="Заданий в игре нет.",
        show_filters=False,
    )


def player_quest_from_button(content: GameContent, pressed: str, player: Character) -> str:
    for index, quest in enumerate(content.quests, start=1):
        if pressed.strip() == numbered(index, _quest_line(content, player, quest)):
            return quest.id
    return ""


def keeper_quest_screen(
    content: GameContent,
    player: Character,
    quest_id: str,
    *,
    counting: bool = False,
    notice: str = "",
) -> Screen:
    quest = content.quest(quest_id)
    log = player.quests
    rows: list[tuple[Label, ...]] = []
    if not log.is_done(quest_id):
        rows.append((labels.KEEPER_QUEST_DONE,))
    if log.is_done(quest_id) or log.is_taken(quest_id):
        rows.append((labels.KEEPER_QUEST_REOPEN,))
    rows.append((labels.KEEPER_QUEST_COUNT,))
    lines = (
        notice or f"Задание: {quest.name}. Кому: {player.name}.",
        f"Считать: {quest.target_count} по счёту. У игрока: {_quest_state_word(player, quest)}.",
        "Наберите новое число счётчика." if counting else "Нажмите, что сделать с заданием.",
    )
    return Screen(id=ScreenId.KEEPER_QUEST, lines=lines, rows=tuple(rows))


# --- блокировка --------------------------------------------------------


def sentence_button(sentence: moderation_rules.Sentence) -> Label:
    return label(sentence.name)


def sentence_from_button(pressed: str) -> moderation_rules.Sentence | None:
    return moderation_rules.sentence_named(pressed)


def ban_screen(player: Character, view: KeeperView, reason: str = "", notice: str = "") -> Screen:
    """Срок и причина. Ничего не отбирается: блокировка — пауза, а не штраф.

    Причина набирается до срока, потому что читает её не смотритель, а тот, кого
    заблокировали: сказать «за что» — часть наказания, а не украшение.
    """
    lines = [
        notice or f"Блокировка: {player.name}.",
        "Заблокированный не играет, пока срок не выйдет. "
        "Персонаж, вещи и золото остаются на месте.",
        ban_line(view.target_ban, view.now),
        f"Причина для следующей блокировки: {reason or 'не названа'}.",
        "Нажмите срок — блокировка ляжет сразу.",
    ]
    rows: list[tuple[Label, ...]] = [
        tuple(
            sentence_button(sentence) for sentence in moderation_rules.SENTENCES[index : index + 2]
        )
        for index in range(0, len(moderation_rules.SENTENCES), 2)
    ]
    rows.append((labels.KEEPER_REASON,))
    if _under_ban(view):
        rows.append((labels.KEEPER_UNBAN,))
    return Screen(id=ScreenId.KEEPER_BAN, lines=tuple(lines), rows=tuple(rows))


# --- сделки ------------------------------------------------------------

#: Сколько сделок показывает список. Больше не нужно: откатывают свежее, а
#: давнее уже разошлось по рукам и вернуть его нечем.
TRADES_SHOWN = 12

STATUSES: dict[TradeStatus, str] = {
    TradeStatus.PENDING: "стоит",
    TradeStatus.ACCEPTED: "расчёт прошёл",
    TradeStatus.DECLINED: "отказ",
    TradeStatus.EXPIRED: "истекло",
    TradeStatus.REVERTED: "откачено",
}


def trade_entry(index: int, record: TradeRecord) -> str:
    """Строка сделки: кто, что, кому и чем кончилось.

    Направление называется словами, а не стрелкой: стрелку экранный диктор
    читает как «больше» или молчит о ней вовсе (``docs/accessibility.md``).
    """
    offer = record.offer
    what = offer.item_name or offer.item_id
    count = f" {offer.quantity} шт" if offer.quantity > 1 else ""
    who = (
        f"{offer.author.name} продал {offer.target.name}"
        if offer.kind is OfferKind.SELL
        else f"{offer.author.name} купил у {offer.target.name}"
    )
    return numbered(
        index, f"{who}: {what}{count} за {gold(offer.price)} — {STATUSES[record.status]}"
    )


def trade_from_button(view: KeeperView, pressed: str) -> TradeRecord | None:
    for index, record in enumerate(view.trades, start=1):
        if pressed.strip() == trade_entry(index, record):
            return record
    return None


def trades_screen(player: Character, view: KeeperView, notice: str = "") -> Screen:
    """Сделки игрока и откат расчёта.

    Откат — единственное, что панель делает не с персонажем, а между двумя, и
    поэтому он сказан числом до нажатия: вещь возвращается тому, кто её отдал, а
    плательщику приходит ровно то, что получил продавец. Пошлина не
    возвращается — её в игре уже нет.
    """
    shown = view.trades[:TRADES_SHOWN]
    undoable = sum(1 for record in shown if record.is_settled)
    lines = [
        notice or f"Сделки: {player.name}. Свежие сначала.",
        f"Показано: {len(shown)}. Из них можно откатить: {undoable}.",
        "Откат возвращает вещь тому, кто её отдал, а золото — тому, кто платил. "
        "Пошлина не возвращается: она ушла из игры при расчёте.",
        "Нажмите строку, чтобы откатить. Спрошу второй раз.",
    ]
    if not shown:
        lines.append("Сделок нет: этот игрок ни с кем не рассчитывался.")
    rows = tuple(
        (label(trade_entry(index, record)),) for index, record in enumerate(shown, start=1)
    )
    return Screen(id=ScreenId.KEEPER_TRADES, lines=tuple(lines), rows=rows)


# --- журнал ------------------------------------------------------------

#: Сколько записей журнала помещается в одно сообщение. Больше не нужно: панель
#: показывает последнее, а не хранит историю за всё время.
LOG_SHOWN = 8


def log_entry(entry: KeeperEntry, now: int) -> str:
    """Строка журнала: кто, что, с кем и как давно."""
    said = moderation_rules.ACTIONS[entry.action]
    who = entry.keeper_name or str(entry.keeper_id)
    ago = f"{duration(max(0, now - entry.at))} назад" if entry.at and now else "когда-то"
    about = f" {entry.target}," if entry.target else ""
    detail = f" {entry.detail}." if entry.detail else ""
    return f"{who} {said}{about} {ago}.{detail}"


def log_screen(view: KeeperView, notice: str = "") -> Screen:
    """Что смотрители делали. Только чтение, и ни одной кнопки.

    Журнал существует потому, что смотрителей больше одного (право раздаётся из
    панели), а панель раздаёт золото, уровни и блокировки. Работа, которую нельзя
    посмотреть, — это работа, за которую некому отвечать.

    Кнопок нет нарочно: нажимать здесь нечего, а кнопка, которая ничего не
    делает, для слушающего экран — обещание, которого никто не сдержит.
    """
    shown = view.log[:LOG_SHOWN]
    lines = [
        notice or "Журнал смотрителя. Свежие записи сначала.",
        f"Показано записей: {len(shown)}. Записи не правятся и не стираются.",
    ]
    if not shown:
        lines.append("Записей нет: пока никто ничего не делал.")
    lines.extend(log_entry(entry, view.now) for entry in shown)
    return Screen(id=ScreenId.KEEPER_LOG, lines=tuple(lines))


# --- статистика и обслуживание -----------------------------------------


def stats_screen(census: Census, notice: str = "") -> Screen:
    """Игра числами. Ни одного процента и ни одной полоски - только счёт."""
    lines = [
        notice or "Статистика игры.",
        f"Персонажей: {census.characters}. Аккаунтов: {census.accounts}.",
        f"Заходили за сутки: {census.fresh_day}. За неделю: {census.fresh_week}.",
        f"Уровень: высший {census.top_level}, средний {census.average_level}.",
        f"Золото: на руках {census.gold_on_hand}, в ячейках {census.gold_in_bank}.",
        f"Заданий закрыто всего: {census.quests_done}. Боёв на арене: {census.arena_fights}.",
        f"Брошенных персонажей: {census.abandoned}. Заблокировали бота: {census.blocked}.",
        f"Заблокировано смотрителем: {census.banned}.",
    ]
    if census.leaders:
        lines.append("Впереди всех:")
        lines.extend(f"{name}, уровень {level}." for name, level in census.leaders)
    return Screen(id=ScreenId.KEEPER_STATS, lines=tuple(lines))


def service_screen(view: KeeperView, notice: str = "") -> Screen:
    """Уборка. Каждая кнопка стирает, и каждая говорит, сколько стёрла."""
    census = view.census or Census()
    lines = (
        notice or "Обслуживание. Всё, что здесь, убирает записи насовсем.",
        f"Брошенных персонажей: {census.abandoned}. "
        "Брошенный — первый уровень, ноль опыта, ни одного шага обучения, "
        "и неделю никто не заходил.",
        f"Заблокировали бота: {census.blocked}. "
        "Узнать это можно только спросив Telegram, и спрашивается по сорок за нажатие.",
        "Проверка идёт порциями: нажмите ещё раз, если осталось.",
    )
    return Screen(
        id=ScreenId.KEEPER_SERVICE,
        lines=lines,
        rows=(
            (labels.KEEPER_SWEEP_DRAFTS,),
            (labels.KEEPER_CHECK_BLOCKED,),
            (labels.KEEPER_DROP_BLOCKED,),
        ),
    )
