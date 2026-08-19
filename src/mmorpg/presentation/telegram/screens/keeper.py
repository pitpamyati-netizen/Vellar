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
по списку кнопок, набранному здесь: новое поле у подряда должно появляться на
экране само, иначе панель начнёт отставать от того, что она правит.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.moderation import Ban, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.trade import OfferKind, TradeRecord, TradeStatus
from mmorpg.domain.ports.repositories import Census
from mmorpg.domain.rules import moderation as moderation_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules.keeper import GOLD_STEP, POINTS_STEP
from mmorpg.domain.rules.overlay import FieldKind, FieldSpec
from mmorpg.domain.rules.stats import DerivedStats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, duration, gold
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

#: Сколько знаков значения помещается в строку списка. Длинное значение целиком
#: читается на экране самого поля: там оно одно и помещается заведомо.
BUTTON_VALUE = 34

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

    Значение на кнопке пришлось бы резать до неузнаваемости: у подряда условие
    занимает две строки, а кнопка — одну.
    """
    return label(spec.name)


def field_from_button(record: OverlayRecord, pressed: str) -> FieldSpec | None:
    for spec in overlay_rules.FIELDS[record.kind]:
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

    Карточка — это список полей, поэтому она и рисуется списком: у подряда их
    четырнадцать, и все четырнадцать в одном сообщении не поместились бы. Строка
    говорит, что стоит в поле, кнопка под ней его меняет.
    """
    single = overlay_rules.TITLES[record.kind][0]
    stored = overlay_rules.held(view.records, record.kind, record.entity_id)
    entries = [
        ListEntry(key=spec.key, text=spec.name, detail=_short(content, spec, record))
        for spec in overlay_rules.FIELDS[record.kind]
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
        lead_lines=tuple(lead),
        extra_rows=tuple(rows),
        show_filters=False,
    )


def _short(content: GameContent, spec: FieldSpec, record: OverlayRecord) -> str:
    """Значение поля так, как оно помещается в строку списка."""
    value = overlay_rules.shown(content, spec, record)
    if len(value) > BUTTON_VALUE:
        value = f"{value[:BUTTON_VALUE].rstrip()}…"
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
                index, overlay_rules.option_name(content, spec, value), chosen=value in picked
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
        name = overlay_rules.option_name(content, spec, value)
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
        f"Подрядов закрыто: {len(player.quests.done)}. "
        f"Круг: {player.arena_wins} побед, {player.arena_losses} поражений.",
        ban_line(view.target_ban, view.now),
        "Уровень поднимается по одному и только вверх: очки уже вложены.",
    ]
    rows: list[tuple[Label, ...]] = [
        (labels.KEEPER_GOLD, labels.KEEPER_LEVEL),
        (labels.KEEPER_HEAL, labels.KEEPER_POINTS),
        (labels.KEEPER_MOVE, labels.KEEPER_TRADES),
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
        f"Подрядов закрыто всего: {census.quests_done}. Боёв в круге: {census.arena_fights}.",
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
        "Брошенный — первый уровень, ноль опыта, ни одного задания обучения, "
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
