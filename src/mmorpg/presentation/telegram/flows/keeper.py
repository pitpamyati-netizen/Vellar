"""Панель смотрителя и жители городов как часть того же автомата.

Здесь та же чистая функция, что и везде: ``advance(state, message) -> state``, без
ввода-вывода и без часов. Всё, что панель решила изменить — правку содержимого,
чужого персонажа, уборку, — она кладёт в :class:`PendingWrite`, а делает это
хендлер (``Claude.md``, правило 5). Разница только в том, что здесь цена ошибки
другая, поэтому удаление персонажа спрашивает дважды, а правка, которая не
работает, записывается вместе с причиной, по которой не работает.

Экраны жителей живут в этом же модуле, хотя игрок про смотрителя ничего не знает.
Причина простая: жители появляются только правкой, и разговор с ними — прямое
продолжение того, что панель завела. Держать это в двух файлах значило бы читать
одну вещь в двух местах.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.application.dto.creation import validate_name
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent
from mmorpg.domain.entities.moderation import KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.rules import keeper as keeper_rules
from mmorpg.domain.rules import overlay as overlay_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.overlay import FieldKind, FieldSpec
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.flows.state import (
    PLAYER_MODE,
    TYPING_NAME,
    TYPING_REASON,
    TYPING_VALUE,
    PendingWrite,
    PlayState,
    go_back,
    page_move,
)
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.routing import Command, Intent
from mmorpg.presentation.telegram.screens import city as city_screens
from mmorpg.presentation.telegram.screens import keeper as keeper_screens
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens import quests as quest_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.keeper import KeeperView
from mmorpg.presentation.telegram.screens.paginated import PageState, total_pages

#: Экраны, которые рисует и обрабатывает этот модуль.
PANEL: frozenset[ScreenId] = frozenset(
    {
        ScreenId.KEEPER,
        ScreenId.KEEPER_CONTENT,
        ScreenId.KEEPER_LIST,
        ScreenId.KEEPER_ENTITY,
        ScreenId.KEEPER_FIELD,
        ScreenId.KEEPER_PLAYERS,
        ScreenId.KEEPER_PLAYER,
        ScreenId.KEEPER_STATS,
        ScreenId.KEEPER_SERVICE,
        ScreenId.KEEPER_BAN,
        ScreenId.KEEPER_LOG,
        ScreenId.KEEPER_TRADES,
        ScreenId.KEEPER_TUNE,
        ScreenId.KEEPER_AMOUNT,
        ScreenId.KEEPER_GIVE,
        ScreenId.KEEPER_GIVE_GEAR,
        ScreenId.KEEPER_GIVE_ITEM,
    }
)
SCREENS: frozenset[ScreenId] = PANEL | {ScreenId.NPCS, ScreenId.NPC}

#: Имена уборок для хендлера (``application/services/keeper_panel.py``).
SWEEP_DRAFTS = "drafts"
SWEEP_CHECK = "check"
SWEEP_BLOCKED = "blocked"

#: Вооружённое удаление: первое нажатие только предупреждает.
ARMED = "delete"

#: Взведённый откат сделки: то же второе нажатие, но помнит, какой именно
#: расчёт откатывают. Строка вида ``rollback:12``.
ROLLBACK = "rollback"

#: Разновидности правок строками - тем, чем они лежат в состоянии автомата.
KINDS: frozenset[str] = frozenset(kind.value for kind in OverlayKind)


def _note(action: KeeperAction, target: str, detail: str = "") -> KeeperEntry:
    """Заготовка строки журнала. Момент и имя смотрителя проставит хендлер."""
    return KeeperEntry(action=action, target=target, detail=detail)


LOST_RIGHT = "Этот экран больше не ваш."
PRESS_A_BUTTON = "Нажмите кнопку панели."


def _city(content: GameContent, state: PlayState, character: Character) -> City:
    for candidate in (state.city_id, character.city_id):
        if content.has_city(candidate):
            return content.city(candidate)
    return content.cities[0]


def _spec(state: PlayState) -> FieldSpec | None:
    """Описание поля, которое сейчас правят. ``None`` — такого поля больше нет."""
    if state.keeper_kind == PLAYER_MODE:
        return overlay_rules.spec_of(OverlayKind.NPC, "city")
    if state.keeper_kind not in KINDS:
        return None
    return overlay_rules.spec_of(OverlayKind(state.keeper_kind), state.keeper_field)


def _record(content: GameContent, state: PlayState, view: KeeperView) -> OverlayRecord:
    kind = OverlayKind(state.keeper_kind)
    return overlay_rules.effective(content, view.records, kind, state.keeper_entity)


def _tune_subject(
    character: Character, state: PlayState, view: KeeperView
) -> tuple[Character | None, bool]:
    """Кого правит точная правка: чужого персонажа с карточки или своего.

    Различает их ``keeper_target``: на своей панели он ноль, на карточке игрока —
    его идентификатор. Второе значение — ``own``: своя правка в журнал не пишется.
    """
    if state.keeper_target:
        return view.target, False
    return character, True


def _to_int(text: str) -> int | None:
    raw = text.strip().lstrip("+")
    try:
        return int(raw)
    except ValueError:
        return None


def _give_level(state: PlayState, view: KeeperView) -> int:
    """Уровень собираемой вещи: набранный смотрителем или уровень игрока."""
    typed_level = _to_int(state.keeper_field)
    if typed_level is not None and typed_level >= 1:
        return typed_level
    return view.target.level if view.target is not None else 1


# --- рисование ---------------------------------------------------------


def render(
    content: GameContent, character: Character, state: PlayState, view: KeeperView
) -> Screen:
    """Один экран панели или разговора с жителем."""
    if state.screen in {ScreenId.NPCS, ScreenId.NPC}:
        return _render_residents(content, character, state)
    if not character.is_admin:
        # Право живёт в ADMIN_IDS и может исчезнуть между двумя нажатиями.
        return play_screens.main_menu_screen(
            content, character, derived_stats(content, character), LOST_RIGHT
        )
    return _render_panel(content, character, state, view)


def _render_residents(content: GameContent, character: Character, state: PlayState) -> Screen:
    city = _city(content, state, character)
    if state.screen is ScreenId.NPC and content.has_npc(state.npc_id):
        return city_screens.npc_screen(content, character, content.npc(state.npc_id), state.notice)
    # Жителя убрали правкой, пока игрок стоял у него: экран не падает, а
    # возвращает к списку (``Claude.md``, правило 8).
    return city_screens.npcs_screen(content, city, state.notice)


def _render_panel(
    content: GameContent, character: Character, state: PlayState, view: KeeperView
) -> Screen:
    stats = derived_stats(content, character)
    match state.screen:
        case ScreenId.KEEPER_CONTENT:
            return keeper_screens.content_screen(content, view, state.notice)
        case ScreenId.KEEPER_LIST if state.keeper_kind in KINDS:
            kind = OverlayKind(state.keeper_kind)
            return keeper_screens.list_screen(content, kind, state.keeper_page, view, state.notice)
        case ScreenId.KEEPER_ENTITY if state.keeper_kind in KINDS:
            return keeper_screens.entity_screen(
                content, _record(content, state, view), state.list_page, view, state.notice
            )
        case ScreenId.KEEPER_FIELD:
            return _render_field(content, state, view)
        case ScreenId.KEEPER_PLAYERS:
            return keeper_screens.players_screen(content, view, state.keeper_page, state.notice)
        case ScreenId.KEEPER_PLAYER if view.target is not None:
            return keeper_screens.player_screen(
                content, view.target, derived_stats(content, view.target), state.notice, view
            )
        case ScreenId.KEEPER_STATS if view.census is not None:
            return keeper_screens.stats_screen(view.census, state.notice)
        case ScreenId.KEEPER_SERVICE:
            return keeper_screens.service_screen(view, state.notice)
        case ScreenId.KEEPER_BAN if view.target is not None:
            return keeper_screens.ban_screen(view.target, view, state.keeper_reason, state.notice)
        case ScreenId.KEEPER_LOG:
            return keeper_screens.log_screen(view, state.notice)
        case ScreenId.KEEPER_TRADES if view.target is not None:
            return keeper_screens.trades_screen(view.target, view, state.notice)
        case ScreenId.KEEPER_TUNE:
            subject, own = _tune_subject(character, state, view)
            if subject is None:
                return keeper_screens.keeper_screen(
                    content, character, stats, view, "Того персонажа больше нет."
                )
            return keeper_screens.tune_screen(
                subject, derived_stats(content, subject), own=own, notice=state.notice
            )
        case ScreenId.KEEPER_AMOUNT:
            subject, own = _tune_subject(character, state, view)
            if subject is None or state.keeper_field not in keeper_screens.TUNE_KEYS:
                return keeper_screens.keeper_screen(
                    content, character, stats, view, "Той правки больше нет."
                )
            return keeper_screens.amount_screen(
                state.keeper_field,
                subject,
                derived_stats(content, subject),
                own=own,
                notice=state.notice,
            )
        case ScreenId.KEEPER_GIVE if view.target is not None:
            return keeper_screens.give_screen(content, view.target, state.keeper_page, state.notice)
        case ScreenId.KEEPER_GIVE_GEAR if view.target is not None and content.has_gear_archetype(
            state.keeper_entity
        ):
            return keeper_screens.give_gear_screen(
                content,
                view.target,
                content.gear_archetype(state.keeper_entity),
                _give_level(state, view),
                state.notice,
            )
        case ScreenId.KEEPER_GIVE_ITEM if view.target is not None and content.has_item(
            state.keeper_entity
        ):
            return keeper_screens.give_item_screen(
                content.item(state.keeper_entity), view.target, state.notice
            )
        case ScreenId.KEEPER:
            return keeper_screens.keeper_screen(content, character, stats, view, state.notice)
        case _:
            # Раздел, чья сущность или игрок исчезли между двумя нажатиями.
            return keeper_screens.keeper_screen(
                content, character, stats, view, state.notice or "Той записи больше нет."
            )


def _render_field(content: GameContent, state: PlayState, view: KeeperView) -> Screen:
    spec = _spec(state)
    if spec is None:
        return keeper_screens.content_screen(content, view, "Такого поля больше нет.")
    if state.keeper_kind == PLAYER_MODE:
        where = view.target.city_id if view.target is not None else ""
        moving = OverlayRecord(kind=OverlayKind.NPC, entity_id="", fields={"city": where})
        return keeper_screens.field_screen(
            content, moving, spec, state.keeper_page, state.notice or "В какой город перевести."
        )
    return keeper_screens.field_screen(
        content, _record(content, state, view), spec, state.keeper_page, state.notice
    )


# --- набранный текст ---------------------------------------------------


def awaits_text(state: PlayState, command: Command) -> bool:
    """Ждёт ли этот экран набранного значения, а не нажатой кнопки."""
    if command.intent is not Intent.UNKNOWN or not state.keeper_typing:
        return False
    return (
        (state.screen is ScreenId.KEEPER_FIELD and state.keeper_typing == TYPING_VALUE)
        or (state.screen is ScreenId.KEEPER_AMOUNT and state.keeper_typing == TYPING_VALUE)
        or (state.screen is ScreenId.KEEPER_GIVE_GEAR and state.keeper_typing == TYPING_VALUE)
        or (state.screen is ScreenId.KEEPER_GIVE_ITEM and state.keeper_typing == TYPING_VALUE)
        or (state.screen is ScreenId.KEEPER_PLAYERS and state.keeper_typing == TYPING_NAME)
        or (state.screen is ScreenId.KEEPER_BAN and state.keeper_typing == TYPING_REASON)
    )


def typed(
    content: GameContent, character: Character, state: PlayState, text: str, view: KeeperView
) -> PlayState:
    """Разобрать набранное. Команду за значение не принимают."""
    if not character.is_admin:
        return state.at(ScreenId.MAIN_MENU).with_notice(LOST_RIGHT)
    if text.startswith("/"):
        return state.with_notice("Это команда, а не значение. Наберите значение без косой черты.")
    if state.screen is ScreenId.KEEPER_PLAYERS:
        return _found(state, view, text)
    if state.screen is ScreenId.KEEPER_AMOUNT:
        return _tuned(content, character, state, view, text)
    if state.screen is ScreenId.KEEPER_GIVE_GEAR:
        value = _to_int(text)
        if value is None or value < 1:
            return state.with_notice("Нужен номер уровня — целое число от единицы.")
        return replace(state, keeper_field=str(value)).with_notice(f"Ступень по уровню {value}.")
    if state.screen is ScreenId.KEEPER_GIVE_ITEM:
        return _typed_give_item(content, state, view, text)
    if state.screen is ScreenId.KEEPER_BAN:
        return replace(state, keeper_typing="", keeper_reason=text.strip()).with_notice(
            f"Причина записана: {text.strip()}. Теперь нажмите срок."
        )
    return _value(content, state, view, text)


def _found(state: PlayState, view: KeeperView, name: str) -> PlayState:
    """Игрок, найденный по имени. Ищет хендлер, здесь только ответ."""
    if view.target is None:
        return replace(state, keeper_typing="").with_notice(f"Персонажа «{name}» в игре нет.")
    return replace(state, keeper_target=view.target.id, keeper_typing="").at(ScreenId.KEEPER_PLAYER)


def _value(content: GameContent, state: PlayState, view: KeeperView, text: str) -> PlayState:
    spec = _spec(state)
    if spec is None:
        return go_back(state).with_notice("Такого поля больше нет.")
    record = _record(content, state, view)
    edited = record.with_field(spec.key, text)
    said = f"{spec.name}: {text}."
    return _stored(state, edited, said)


def _stored(state: PlayState, record: OverlayRecord, said: str) -> PlayState:
    """Записать правку и вернуться на карточку сущности."""
    walked = replace(state, keeper_typing="", keeper_page=PageState())
    if walked.screen is ScreenId.KEEPER_FIELD:
        walked = go_back(walked)
    note = _note(KeeperAction.EDIT, record.entity_id, said)
    return walked.storing(PendingWrite(edit=record, note=note)).with_notice(said)


# --- шаг ---------------------------------------------------------------


def advance(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    view: KeeperView,
) -> PlayState:
    if state.screen in {ScreenId.NPCS, ScreenId.NPC}:
        return _step_residents(content, character, state, command)
    if not character.is_admin:
        return state.at(ScreenId.MAIN_MENU).with_notice(LOST_RIGHT)

    match state.screen:
        case ScreenId.KEEPER:
            return _step_panel(content, character, state, command)
        case ScreenId.KEEPER_CONTENT:
            return _step_content(state, command)
        case ScreenId.KEEPER_LIST:
            return _step_list(content, character, state, command, view)
        case ScreenId.KEEPER_ENTITY:
            return _step_entity(content, state, command, view)
        case ScreenId.KEEPER_FIELD:
            return _step_field(content, state, command, view)
        case ScreenId.KEEPER_PLAYERS:
            return _step_players(content, state, command, view)
        case ScreenId.KEEPER_PLAYER:
            return _step_player(content, state, command, view)
        case ScreenId.KEEPER_SERVICE:
            return _step_service(state, command)
        case ScreenId.KEEPER_BAN:
            return _step_ban(state, command, view)
        case ScreenId.KEEPER_TRADES:
            return _step_trades(state, command, view)
        case ScreenId.KEEPER_TUNE:
            return _step_tune(state, command)
        case ScreenId.KEEPER_AMOUNT:
            return _step_amount(state, command)
        case ScreenId.KEEPER_GIVE:
            return _step_give(content, state, command, view)
        case ScreenId.KEEPER_GIVE_GEAR:
            return _step_give_gear(content, state, command, view)
        case ScreenId.KEEPER_GIVE_ITEM:
            return _step_give_item(state, command)
        case _:
            return state.with_notice("Здесь только чтение. Нажмите «Назад».")


def _step_residents(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите человека из списка или «Назад».")
    city = _city(content, state, character)
    if state.screen is ScreenId.NPCS:
        for npc in content.npcs_in(city.id):
            if city_screens.npc_label(npc).matches(command.argument):
                return replace(state, npc_id=npc.id).at(ScreenId.NPC)
        return state.with_notice("Не узнал, о ком речь. Нажмите человека из списка.")

    if not content.has_npc(state.npc_id):
        return state.at(ScreenId.NPCS).with_notice("Этого человека здесь больше нет.")
    for quest in content.quests_of(state.npc_id):
        if not quest_screens.quest_button(quest).matches(command.argument):
            continue
        if not quest_rules.is_open(quest, character):
            return state.with_notice("Эта работа сейчас не для вас.")
        return replace(state, quest_id=quest.id).at(ScreenId.QUEST_OFFER)
    return state.with_notice("Не узнал задание. Нажмите работу из списка.")


def _step_panel(
    content: GameContent, character: Character, state: PlayState, command: Command
) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice(PRESS_A_BUTTON)
    if labels.KEEPER_WORLD.matches(command.argument):
        return state.at(ScreenId.KEEPER_CONTENT)
    if labels.KEEPER_PLAYERS.matches(command.argument):
        return replace(state, keeper_page=PageState()).at(ScreenId.KEEPER_PLAYERS)
    if labels.KEEPER_STATS.matches(command.argument):
        return state.at(ScreenId.KEEPER_STATS)
    if labels.KEEPER_SERVICE.matches(command.argument):
        return state.at(ScreenId.KEEPER_SERVICE)
    if labels.KEEPER_LOG.matches(command.argument):
        return state.at(ScreenId.KEEPER_LOG)
    if labels.KEEPER_TUNE.matches(command.argument):
        return replace(state, keeper_target=0, keeper_field="", keeper_typing="").at(
            ScreenId.KEEPER_TUNE
        )
    return _grant(content, character, state, command, own=True)


def _grant(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    *,
    own: bool,
) -> PlayState:
    """Четыре выдачи. Те же самые и себе, и чужому персонажу."""

    def store(changed: Character, said: str, action: KeeperAction) -> PlayState:
        # Выдача себе не пишется в журнал: смотритель отвечает за то, что сделал
        # с чужим, а свой персонаж служебный и так.
        write = (
            PendingWrite(character=changed)
            if own
            else PendingWrite(other=changed, note=_note(action, changed.name, said))
        )
        return state.storing(write).with_notice(said)

    if labels.KEEPER_GOLD.matches(command.argument):
        grown = keeper_rules.grant_gold(character)
        return store(
            grown,
            f"Выдано {keeper_rules.GOLD_STEP} золота. Теперь: {grown.gold}.",
            KeeperAction.GOLD,
        )
    if labels.KEEPER_LEVEL.matches(command.argument):
        grown, level_up = keeper_rules.raise_level(content, character)
        if not level_up.levels_gained:
            return state.with_notice("Выше некуда: это последний уровень.")
        return store(
            grown,
            f"Уровень {grown.level}. Очков характеристик: {level_up.stat_points}, "
            f"очков умений: {level_up.skill_points}.",
            KeeperAction.LEVEL,
        )
    if labels.KEEPER_HEAL.matches(command.argument):
        healed = keeper_rules.heal(content, character)
        return store(healed, f"Раны залечены. Здоровье: {healed.health}.", KeeperAction.HEAL)
    if labels.KEEPER_POINTS.matches(command.argument):
        granted = keeper_rules.grant_points(character)
        return store(
            granted,
            f"Выдано очков: характеристик {granted.unspent_stat_points}, "
            f"умений {granted.unspent_skill_points} всего.",
            KeeperAction.POINTS,
        )
    return state.with_notice(PRESS_A_BUTTON)


def _step_content(state: PlayState, command: Command) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice(PRESS_A_BUTTON)
    if labels.KEEPER_RELOAD.matches(command.argument):
        return state.storing(PendingWrite(reload=True))
    kind = keeper_screens.kind_from_button(command.argument)
    if kind is None:
        return state.with_notice(PRESS_A_BUTTON)
    return replace(state, keeper_kind=kind.value, keeper_page=PageState()).at(ScreenId.KEEPER_LIST)


def _step_list(
    content: GameContent,
    character: Character,
    state: PlayState,
    command: Command,
    view: KeeperView,
) -> PlayState:
    if state.keeper_kind not in KINDS:
        return state.at(ScreenId.KEEPER_CONTENT).with_notice("Выберите раздел заново.")
    kind = OverlayKind(state.keeper_kind)
    listed = overlay_rules.listing(content, kind)
    moved = page_move(command, state.keeper_page, total_pages(len(listed)))
    if moved is not None:
        return replace(state, keeper_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите запись из списка.")

    if labels.KEEPER_ADD.matches(command.argument):
        if kind not in overlay_rules.CREATABLE:
            return state.with_notice("Такое заводят в content, а не кнопкой.")
        fresh = _blank(content, state, character, kind, view)
        single = overlay_rules.TITLES[kind][0]
        return (
            replace(state, keeper_entity=fresh.entity_id, list_page=PageState())
            .at(ScreenId.KEEPER_ENTITY)
            .storing(PendingWrite(edit=fresh))
            .with_notice(f"{single} заведён. Заполните поля — пока он в игре не появится.")
        )

    entity_id = keeper_screens.entity_from_button(content, kind, command.argument)
    if not entity_id:
        return state.with_notice("Не узнал запись. Нажмите строку из списка.")
    return replace(state, keeper_entity=entity_id, list_page=PageState()).at(ScreenId.KEEPER_ENTITY)


def _blank(
    content: GameContent,
    state: PlayState,
    character: Character,
    kind: OverlayKind,
    view: KeeperView,
) -> OverlayRecord:
    """Новая сущность: пустая, но не бессмысленная.

    Город подставляется тот, в котором смотритель стоит, а числа — те, при которых
    запись хотя бы читается. Остальное он заполнит сам; до тех пор она не в игре.
    """
    city = _city(content, state, character)
    fields: dict[str, str] = {"city": city.id}
    match kind:
        case OverlayKind.QUEST:
            fields |= {
                "objective": "kill",
                "target_count": "3",
                "level": str(character.level),
                "reward_gold": "50",
                "reward_experience": "60",
            }
        case OverlayKind.LOCATION:
            taken = {location.slot for location in city.locations}
            free = next(
                (
                    slot
                    for slot in range(1, overlay_rules.MAX_LOCATION_SLOT + 1)
                    if slot not in taken
                ),
                overlay_rules.MAX_LOCATION_SLOT,
            )
            fields |= {"slot": str(free), "level_min": "1", "level_max": "5", "pvp": "нет"}
        case OverlayKind.ENEMY:
            fields = {
                "kind": "beast",
                "health": "1",
                "damage": "1",
                "armor": "1",
                "initiative": "1",
            }
        case _:
            pass
    return OverlayRecord(
        kind=kind,
        entity_id=overlay_rules.next_id(kind, view.records),
        fields=fields,
        author_id=character.user_id,
    )


def _step_entity(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    record = _record(content, state, view)
    # Поля карточки листаются своей страницей: у задания их четырнадцать, и в
    # одно сообщение они не помещаются.
    fields = total_pages(len(overlay_rules.fields_for(record)), keeper_screens.CARD_FIELDS)
    moved = page_move(command, state.list_page, fields)
    if moved is not None:
        return replace(state, list_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите поле, чтобы его изменить.")

    if labels.KEEPER_FORGET.matches(command.argument):
        return (
            go_back(state)
            .storing(
                PendingWrite(
                    forget=(record.kind.value, record.entity_id),
                    note=_note(KeeperAction.FORGET, record.entity_id),
                )
            )
            .with_notice("Правка снята. Осталось то, что записано в content.")
        )
    if labels.KEEPER_REMOVE.matches(command.argument):
        return state.storing(
            PendingWrite(
                edit=replace(record, removed=True),
                note=_note(KeeperAction.EDIT, record.entity_id, "убрано из игры"),
            )
        ).with_notice("Убрано из игры. Вернуть можно кнопкой «Вернуть в игру».")
    if labels.KEEPER_RETURN.matches(command.argument):
        return state.storing(
            PendingWrite(
                edit=replace(record, removed=False),
                note=_note(KeeperAction.EDIT, record.entity_id, "возвращено в игру"),
            )
        ).with_notice("Вернулось в игру.")

    spec = keeper_screens.field_from_button(record, command.argument)
    if spec is None:
        return state.with_notice("Не узнал поле. Нажмите строку из списка.")
    typing = TYPING_VALUE if spec.kind in {FieldKind.TEXT, FieldKind.NUMBER, FieldKind.RATE} else ""
    return replace(state, keeper_field=spec.key, keeper_typing=typing, keeper_page=PageState()).at(
        ScreenId.KEEPER_FIELD
    )


def _step_field(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    spec = _spec(state)
    if spec is None:
        return go_back(state).with_notice("Такого поля больше нет.")
    if state.keeper_kind == PLAYER_MODE:
        return _step_move(content, state, command, view, spec)

    record = _record(content, state, view)
    if spec.kind in {FieldKind.CHOICE, FieldKind.LIST}:
        pages = total_pages(len(overlay_rules.options(content, spec, record)))
        moved = page_move(command, state.keeper_page, pages)
        if moved is not None:
            return replace(state, keeper_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вариант или наберите значение.")

    if labels.KEEPER_CLEAR.matches(command.argument):
        return _stored(state, record.with_field(spec.key, ""), f"{spec.name}: очищено.")
    if spec.kind is FieldKind.FLAG:
        answer = "да" if command.argument.casefold() == "да" else "нет"
        return _stored(state, record.with_field(spec.key, answer), f"{spec.name}: {answer}.")

    chosen = keeper_screens.option_from_button(content, record, spec, command.argument)
    if chosen is None:
        return state.with_notice("Не узнал вариант. Нажмите строку из списка.")
    if spec.kind is FieldKind.LIST:
        return _toggled(content, state, record, spec, chosen)
    name = overlay_rules.option_name(content, spec, chosen, record)
    return _stored(state, record.with_field(spec.key, chosen), f"{spec.name}: {name}.")


def _toggled(
    content: GameContent,
    state: PlayState,
    record: OverlayRecord,
    spec: FieldSpec,
    chosen: str,
) -> PlayState:
    """Перечисление правится на месте: список набирают, а не выбирают один раз."""
    picked = list(record.listed(spec.key))
    if chosen in picked:
        picked.remove(chosen)
        said = f"{overlay_rules.option_name(content, spec, chosen, record)}: убрано."
    else:
        picked.append(chosen)
        said = f"{overlay_rules.option_name(content, spec, chosen, record)}: добавлено."
    edited = record.with_field(spec.key, ", ".join(picked))
    note = _note(KeeperAction.EDIT, edited.entity_id, said)
    return state.storing(PendingWrite(edit=edited, note=note)).with_notice(said)


def _step_move(
    content: GameContent,
    state: PlayState,
    command: Command,
    view: KeeperView,
    spec: FieldSpec,
) -> PlayState:
    """Перевод игрока в город: тот же список, что и у поля «Город»."""
    if view.target is None:
        return go_back(state).with_notice("Того персонажа больше нет.")
    moving = OverlayRecord(kind=OverlayKind.NPC, entity_id="", fields={"city": view.target.city_id})
    pages = total_pages(len(overlay_rules.options(content, spec, moving)))
    moved = page_move(command, state.keeper_page, pages)
    if moved is not None:
        return replace(state, keeper_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите город из списка.")

    chosen = keeper_screens.option_from_button(content, moving, spec, command.argument)
    if chosen is None:
        return state.with_notice("Не узнал город. Нажмите строку из списка.")
    walked = replace(go_back(state), keeper_kind="", keeper_field="")
    city_name = content.city(chosen).name
    return walked.storing(
        PendingWrite(
            other=keeper_rules.move_to(view.target, chosen),
            note=_note(KeeperAction.MOVE, view.target.name, city_name),
        )
    ).with_notice(f"{view.target.name} переведён в город {city_name}.")


def _step_tune(state: PlayState, command: Command) -> PlayState:
    """Меню точных правок: нажать, что менять, — значение набирают следующим шагом."""
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите, что менять, или «Назад».")
    key = keeper_screens.tune_key_from_button(command.argument)
    if key is None:
        return state.with_notice("Не узнал. Нажмите строку из списка.")
    return replace(state, keeper_field=key, keeper_typing=TYPING_VALUE).at(ScreenId.KEEPER_AMOUNT)


def _step_amount(state: PlayState, command: Command) -> PlayState:
    """На экране значения нажимать нечего: значение набирают сообщением."""
    return state.with_notice("Наберите значение сообщением или нажмите «Назад».")


def _tuned(
    content: GameContent, character: Character, state: PlayState, view: KeeperView, text: str
) -> PlayState:
    """Разобрать набранное значение точной правки и сложить её в ``PendingWrite``."""
    key = state.keeper_field
    if key not in keeper_screens.TUNE_KEYS:
        return go_back(state).with_notice("Той правки больше нет.")
    subject, own = _tune_subject(character, state, view)
    if subject is None:
        return go_back(state).with_notice("Того персонажа больше нет.")

    if key == "name":
        check = validate_name(text)
        if not check.ok:
            return state.with_notice(check.problem)
        renamed = keeper_rules.rename(subject, text)
        return _tune_store(state, own, renamed, KeeperAction.RENAME, f"Имя: {renamed.name}.")

    value = _to_int(text)
    if value is None:
        return state.with_notice("Нужно целое число. Наберите ещё раз.")
    if key in {"stat_points", "skill_points"} and value < 0:
        return state.with_notice("Очки только выдают, не отнимают.")

    match key:
        case "gold":
            changed = keeper_rules.grant_gold(subject, value)
            return _tune_store(state, own, changed, KeeperAction.GOLD, f"Золото: {changed.gold}.")
        case "bank":
            changed = keeper_rules.set_bank_gold(subject, value)
            return _tune_store(
                state, own, changed, KeeperAction.GOLD, f"В ячейке: {changed.bank_gold}."
            )
        case "health":
            changed = keeper_rules.set_health(content, subject, value)
            return _tune_store(
                state, own, changed, KeeperAction.HEAL, f"Здоровье: {changed.health}."
            )
        case "level":
            changed, level_up = keeper_rules.set_level(content, subject, value)
            if not level_up.levels_gained:
                return state.with_notice("Уровень не изменился: понизить нельзя.")
            said = (
                f"Уровень {changed.level}. Очков характеристик: {level_up.stat_points}, "
                f"умений: {level_up.skill_points}."
            )
            return _tune_store(state, own, changed, KeeperAction.LEVEL, said)
        case "stat_points":
            changed = keeper_rules.grant_points(subject, stat_points=value, skill_points=0)
            return _tune_store(
                state,
                own,
                changed,
                KeeperAction.POINTS,
                f"Очков характеристик всего: {changed.unspent_stat_points}.",
            )
        case _:
            changed = keeper_rules.grant_points(subject, stat_points=0, skill_points=value)
            return _tune_store(
                state,
                own,
                changed,
                KeeperAction.POINTS,
                f"Очков умений всего: {changed.unspent_skill_points}.",
            )


def _tune_store(
    state: PlayState, own: bool, changed: Character, action: KeeperAction, said: str
) -> PlayState:
    """Записать точную правку и вернуться в меню точных правок.

    Своя правка не пишется в журнал — ровно как и быстрые выдачи себе (``_grant``).
    """
    walked = replace(state, keeper_typing="")
    if walked.screen is ScreenId.KEEPER_AMOUNT:
        walked = go_back(walked)
    write = (
        PendingWrite(character=changed)
        if own
        else PendingWrite(other=changed, note=_note(action, changed.name, said))
    )
    return walked.storing(write).with_notice(said)


def _step_give(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    """Список выдаваемого: вид снаряжения ведёт к ступени и редкости, вещь — к числу."""
    if view.target is None:
        return go_back(state).with_notice("Того персонажа больше нет.")
    entries = keeper_screens.give_entries(content)
    moved = page_move(command, state.keeper_page, total_pages(len(entries)))
    if moved is not None:
        return replace(state, keeper_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите вид снаряжения или написанную вещь.")
    picked = keeper_screens.give_from_button(content, command.argument)
    if picked is None:
        return state.with_notice("Не узнал. Нажмите строку из списка.")
    kind, ident = picked
    if kind == "gear":
        return replace(state, keeper_entity=ident, keeper_field="", keeper_typing=TYPING_VALUE).at(
            ScreenId.KEEPER_GIVE_GEAR
        )
    return replace(state, keeper_entity=ident, keeper_typing=TYPING_VALUE).at(
        ScreenId.KEEPER_GIVE_ITEM
    )


def _step_give_gear(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    """Ступень набирают числом, редкость нажимают; на нажатии вещь собирается."""
    if view.target is None or not content.has_gear_archetype(state.keeper_entity):
        return go_back(state).with_notice("Той вещи больше нет.")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите редкость или наберите номер уровня.")
    if labels.KEEPER_GIVE_AT_PLAYER_LEVEL.matches(command.argument):
        return replace(state, keeper_field="").with_notice("Ступень — по уровню игрока.")
    rarity = keeper_screens.rarity_from_button(content, command.argument)
    if rarity is None:
        return state.with_notice("Не узнал редкость. Нажмите строку из списка.")
    archetype = content.gear_archetype(state.keeper_entity)
    tier = item_procgen.tier_at(content, _give_level(state, view))
    tier_level = tier.level if tier is not None else _give_level(state, view)
    item = item_procgen.build(content, archetype, tier_level, rarity)
    return _granted(state, view.target, item.id, 1, item.name)


def _step_give_item(state: PlayState, command: Command) -> PlayState:
    """На экране количества нажимать нечего: число набирают сообщением."""
    return state.with_notice("Наберите количество сообщением или нажмите «Назад».")


def _typed_give_item(
    content: GameContent, state: PlayState, view: KeeperView, text: str
) -> PlayState:
    if view.target is None or not content.has_item(state.keeper_entity):
        return go_back(state).with_notice("Той вещи больше нет.")
    value = _to_int(text)
    if value is None or value == 0:
        return state.with_notice("Нужно ненулевое целое число.")
    item = content.item(state.keeper_entity)
    return _granted(state, view.target, item.id, value, item.name)


def _granted(
    state: PlayState, target: Character, item_id: str, delta: int, item_name: str
) -> PlayState:
    """Записать выдачу в сумку и вернуться на карточку игрока."""
    walked = replace(state, keeper_typing="", keeper_field="", keeper_kind="")
    give_screens = {
        ScreenId.KEEPER_GIVE,
        ScreenId.KEEPER_GIVE_GEAR,
        ScreenId.KEEPER_GIVE_ITEM,
    }
    while walked.screen in give_screens:
        walked = go_back(walked)
    verb = "выдано" if delta > 0 else "убрано"
    said = f"{item_name}: {verb} {abs(delta)}."
    note = _note(KeeperAction.GRANT_ITEM, target.name, f"{item_name} ({delta:+d})")
    return walked.storing(
        PendingWrite(grant_item=(target.id, item_id, delta), note=note)
    ).with_notice(said)


def _step_players(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    moved = page_move(command, state.keeper_page, total_pages(len(view.players)))
    if moved is not None:
        return replace(state, keeper_page=moved, notice="")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите персонажа или «Найти по имени».")
    if labels.KEEPER_FIND.matches(command.argument):
        return replace(state, keeper_typing=TYPING_NAME).with_notice(
            "Наберите имя персонажа сообщением."
        )
    found = keeper_screens.player_from_button(content, view, command.argument)
    if found is None:
        return state.with_notice("Не узнал персонажа. Нажмите строку из списка.")
    return replace(state, keeper_target=found.id).at(ScreenId.KEEPER_PLAYER)


def _step_player(
    content: GameContent, state: PlayState, command: Command, view: KeeperView
) -> PlayState:
    if view.target is None:
        return go_back(state).with_notice("Того персонажа больше нет.")
    if command.intent is not Intent.SELECT:
        return replace(state, keeper_typing="").with_notice(PRESS_A_BUTTON)

    if labels.KEEPER_MOVE.matches(command.argument):
        return replace(
            state, keeper_kind=PLAYER_MODE, keeper_field="city", keeper_page=PageState()
        ).at(ScreenId.KEEPER_FIELD)
    if labels.KEEPER_TUNE.matches(command.argument):
        return replace(state, keeper_typing="", keeper_field="").at(ScreenId.KEEPER_TUNE)
    if labels.KEEPER_GIVE_ITEM.matches(command.argument):
        return replace(
            state,
            keeper_kind="give",
            keeper_entity="",
            keeper_field="",
            keeper_typing="",
            keeper_page=PageState(),
        ).at(ScreenId.KEEPER_GIVE)
    if labels.KEEPER_TRADES.matches(command.argument):
        return replace(state, keeper_typing="").at(ScreenId.KEEPER_TRADES)
    if labels.KEEPER_BAN.matches(command.argument):
        return replace(state, keeper_typing="", keeper_reason="").at(ScreenId.KEEPER_BAN)
    if labels.KEEPER_UNBAN.matches(command.argument):
        return _unban(state, view)
    if labels.KEEPER_DELETE.matches(command.argument):
        return _delete(state, view)

    right = _right(state, command, view)
    if right is not None:
        return right

    # Любая другая кнопка снимает взведённое удаление: смотритель передумал.
    return _grant(content, view.target, replace(state, keeper_typing=""), command, own=False)


def _right(state: PlayState, command: Command, view: KeeperView) -> PlayState | None:
    """Само право смотрителя: выдать или отобрать. ``None`` - нажали не это.

    Кнопки этих надписей нет у того, чьё право не из настройки, но набрать надпись
    руками может кто угодно, поэтому проверка стоит здесь, а не только на экране.
    Отказ при этом обычный, «нажмите кнопку панели»: кто не раздаёт право, тот и
    не должен узнать, что такая кнопка вообще бывает.
    """
    target = view.target
    if target is None:  # pragma: no cover - проверено вызывающим
        return None
    giving = labels.KEEPER_PROMOTE.matches(command.argument)
    taking = labels.KEEPER_DEMOTE.matches(command.argument)
    if not giving and not taking:
        return None
    if not view.granting or view.target_locked:
        return state.with_notice(PRESS_A_BUTTON)
    said = f"{target.name} теперь смотритель." if giving else f"{target.name} больше не смотритель."
    return (
        replace(state, keeper_typing="")
        .storing(
            PendingWrite(
                keeper_grant=(target.user_id, giving),
                note=_note(KeeperAction.PROMOTE if giving else KeeperAction.DEMOTE, target.name),
            )
        )
        .with_notice(said)
    )


def _unban(state: PlayState, view: KeeperView) -> PlayState:
    """Снять блокировку. Всегда одним нажатием и всегда без вопросов.

    Спрашивать «точно?» здесь нечего: ошибиться в сторону «человек играет» —
    не та ошибка, ради которой стоит держать второе нажатие.
    """
    target = view.target
    if target is None:  # pragma: no cover - проверено вызывающим
        return state
    walked = replace(state, keeper_typing="", keeper_reason="")
    if walked.screen is ScreenId.KEEPER_BAN:
        walked = go_back(walked)
    return walked.storing(PendingWrite(ban=(target.user_id, "", ""))).with_notice(
        f"{target.name}: блокировка снята."
    )


def _step_ban(state: PlayState, command: Command, view: KeeperView) -> PlayState:
    """Срок нажимают, причину набирают. Блокировка ложится с нажатием срока."""
    target = view.target
    if target is None:
        return go_back(state).with_notice("Того персонажа больше нет.")
    if command.intent is not Intent.SELECT:
        return state.with_notice("Нажмите срок или «Указать причину».")
    if labels.KEEPER_REASON.matches(command.argument):
        return replace(state, keeper_typing=TYPING_REASON).with_notice(
            "Наберите причину сообщением. Её прочитает тот, кого блокируют."
        )
    if labels.KEEPER_UNBAN.matches(command.argument):
        return _unban(state, view)

    sentence = keeper_screens.sentence_from_button(command.argument)
    if sentence is None:
        return state.with_notice("Не узнал срок. Нажмите строку из списка.")
    reason = state.keeper_reason
    walked = replace(go_back(state), keeper_typing="", keeper_reason="")
    said = "навсегда" if sentence.forever else sentence.name.lower()
    return walked.storing(PendingWrite(ban=(target.user_id, sentence.key, reason))).with_notice(
        f"{target.name} заблокирован {said}."
    )


def _step_trades(state: PlayState, command: Command, view: KeeperView) -> PlayState:
    """Список сделок игрока. Откат — в два нажатия, как и всё необратимое здесь.

    Взведён откат ровно одной строки: нажали другую — взводится она, а прежняя
    забывается. Иначе «ещё раз» означало бы не ту сделку, которую смотритель
    видит перед собой.
    """
    if view.target is None:
        return go_back(state).with_notice("Того персонажа больше нет.")
    if command.intent is not Intent.SELECT:
        return replace(state, keeper_typing="").with_notice("Нажмите сделку из списка.")

    record = keeper_screens.trade_from_button(view, command.argument)
    if record is None:
        return replace(state, keeper_typing="").with_notice(
            "Не узнал сделку. Нажмите строку из списка."
        )
    if not record.is_settled:
        return replace(state, keeper_typing="").with_notice(
            "Откатывать нечего: по этой сделке расчёт не проходил."
        )

    armed = f"{ROLLBACK}:{record.id}"
    if state.keeper_typing != armed:
        return replace(state, keeper_typing=armed).with_notice(
            f"Откатить расчёт: {record.offer.item_name} за {record.offer.price}? "
            "Нажмите ту же строку ещё раз."
        )
    detail = f"{record.offer.item_name} за {record.offer.price}"
    return (
        replace(state, keeper_typing="")
        .storing(
            PendingWrite(
                rollback=record.id,
                note=_note(KeeperAction.ROLLBACK, view.target.name, detail),
            )
        )
        .with_notice("Откат сделки:")
    )


def _delete(state: PlayState, view: KeeperView) -> PlayState:
    """Удаление в два нажатия. Одно неверное нажатие не должно стирать человека."""
    target = view.target
    if target is None:  # pragma: no cover - проверено вызывающим
        return state
    if state.keeper_typing != ARMED:
        return replace(state, keeper_typing=ARMED).with_notice(
            f"Удалить {target.name} насовсем? Нажмите «Удалить персонажа» ещё раз."
        )
    return (
        go_back(replace(state, keeper_target=0, keeper_typing=""))
        .storing(
            PendingWrite(
                remove_character=target.id,
                note=_note(KeeperAction.DELETE, target.name),
            )
        )
        .with_notice(f"{target.name} удалён вместе с сумкой.")
    )


def _step_service(state: PlayState, command: Command) -> PlayState:
    if command.intent is not Intent.SELECT:
        return state.with_notice(PRESS_A_BUTTON)
    if labels.KEEPER_SWEEP_DRAFTS.matches(command.argument):
        return state.storing(PendingWrite(service=SWEEP_DRAFTS))
    if labels.KEEPER_CHECK_BLOCKED.matches(command.argument):
        return state.storing(PendingWrite(service=SWEEP_CHECK))
    if labels.KEEPER_DROP_BLOCKED.matches(command.argument):
        return state.storing(PendingWrite(service=SWEEP_BLOCKED))
    return state.with_notice(PRESS_A_BUTTON)
