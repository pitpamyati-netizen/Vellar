"""Состояние игрового автомата и то, что один шаг просит записать.

Отдельный модуль, потому что состояние общее: по нему ходят и обычные экраны
(``flows/play.py``), и панель смотрителя (``flows/keeper.py``), а импортировать
одну ветку автомата из другой значило бы завязать их в кольцо.

Само состояние ничего не пишет и ничего не читает. Всё, что шаг решил изменить,
складывается в :class:`PendingWrite`, и записывает это хендлер — в одном месте, где
видно целиком, что игра вообще сохраняет (``docs/architecture.md``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import Item
from mmorpg.domain.entities.moderation import KeeperEntry
from mmorpg.domain.entities.overlay import OverlayRecord
from mmorpg.domain.ports.repositories import AccessibilitySettings, PlayerFilter
from mmorpg.domain.rules.guild import Guild
from mmorpg.domain.rules.party import Party
from mmorpg.presentation.telegram.routing import Command, Intent
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import ListFilters, PageState
from mmorpg.presentation.telegram.screens.shop import OwnedItem
from mmorpg.presentation.telegram.states.screens import NavigationStack, back_target

#: Правка чужого персонажа переиспользует экран выбора значения, и вот чем она
#: отличается от правки содержимого: разновидности с таким именем нет.
PLAYER_MODE = "player"

#: Что означает набранный текст на текущем экране. Пусто - ничего не означает.
TYPING_NAME = "name"
TYPING_VALUE = "value"
#: Набирается причина блокировки: её прочитает заблокированный, а не смотритель.
TYPING_REASON = "reason"


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """Что хендлер обязан сохранить после этого шага.

    ``items`` — список изменений сумки: положительное число добавляет,
    отрицательное убирает. Ветка сама не применяет здесь ничего.

    Последние поля - работа смотрителя. Они отделены от остальных нарочно: всё,
    что стирает или меняет чужое, должно быть видно в одном списке.
    """

    character: Character | None = None
    settings: AccessibilitySettings | None = None
    items: tuple[tuple[str, int], ...] = ()
    #: Правка содержимого, которую надо записать.
    edit: OverlayRecord | None = None
    #: Правка, которую надо снять целиком: разновидность и ключ сущности.
    forget: tuple[str, str] | None = None
    #: Чужой персонаж, изменённый смотрителем.
    other: Character | None = None
    #: Чей персонаж стереть. Ноль - ничей.
    remove_character: int = 0
    #: Кому дать или у кого отобрать право смотрителя: аккаунт и что с ним делать.
    keeper_grant: tuple[int, bool] | None = None
    #: Кого заблокировать: аккаунт, срок из ``domain/rules/moderation.SENTENCES``
    #: и причина. Пустой срок — снять блокировку. Сам момент конца считает
    #: хендлер: у автомата часов нет.
    ban: tuple[int, str, str] | None = None
    #: Кого замолчать в группе: аккаунт, срок и причина. Пустой срок — снять.
    #: Момент конца считает хендлер, как и у ``ban``.
    mute: tuple[int, str, str] | None = None
    #: Живая операция смотрителя (ADR 0045): ``(действие, аргумент)``. Действия —
    #: ``maint_on``, ``maint_off``, ``announce``, ``free_battle``,
    #: ``reset_player``, ``reset_location``.
    ops: tuple[str, str] | None = None
    #: Кому изменить счётчик предупреждений: аккаунт и шаг (``+1`` или ``-1``).
    warn: tuple[int, int] | None = None
    #: Что записать в журнал смотрителя. Момент и имя проставляет хендлер.
    note: KeeperEntry | None = None
    #: Какую уборку выполнить (``application/services/keeper_panel.py``).
    service: str = ""
    #: Какой расчёт откатить: строка журнала сделок. Ноль — никакой.
    rollback: int = 0
    #: Что смотритель выдал в сумку: чей персонаж, вещь и сколько. Число со
    #: знаком, как и у ``items``, но адрес здесь чужой, а не персонажа нажавшего.
    grant_item: tuple[int, str, int] | None = None
    #: Правка сумки чужого персонажа, идущая рядом с правкой снаряжения (``other``):
    #: снятое кладётся в сумку, надетое из неё убирается. Адрес — ``other.id``.
    bag_changes: tuple[tuple[str, int], ...] = ()
    #: Отряд игрока, изменённый смотрителем (вывод из состава): ``parties.save``.
    party_save: Party | None = None
    #: Отряд игрока, который смотритель расформировал: id собравшего. Ноль - нет.
    party_disband: int = 0
    #: Гильдия игрока, изменённая смотрителем (состав, звания): ``guilds.save``.
    guild_save: Guild | None = None
    #: Гильдия, которую смотритель распустил: её id. Ноль - нет.
    guild_disband: int = 0
    #: Казна гильдии, выставленная числом: id гильдии и новое значение. Двигает её
    #: хендлер условным ``UPDATE`` (``Claude.md``, правило 8), а не записью целиком.
    guild_vault: tuple[int, int] | None = None
    #: Перечитать правки из хранилища.
    reload: bool = False
    #: Почему изменился кошелёк: метка для денежного журнала
    #: (``mmorpg.economy_log``). Сама по себе ничего не записывает.
    gold_flow: str = ""
    #: Узел, из которого шаг забрал одну единицу волны: обыскал тайник, собрал
    #: горсть руды. Минус один - ничего не забрал. Записывает это хендлер: узел
    #: общий, и считает его не автомат (``domain/rules/nodes.py``).
    node_take: int = -1

    @property
    def empty(self) -> bool:
        return (
            self.character is None
            and self.settings is None
            and not self.items
            and self.edit is None
            and self.forget is None
            and self.other is None
            and not self.remove_character
            and self.keeper_grant is None
            and self.ban is None
            and self.mute is None
            and self.ops is None
            and self.warn is None
            and self.note is None
            and not self.service
            and not self.rollback
            and self.grant_item is None
            and not self.bag_changes
            and self.party_save is None
            and not self.party_disband
            and self.guild_save is None
            and not self.guild_disband
            and self.guild_vault is None
            and not self.reload
            and self.node_take < 0
        )

    def with_items(self, *changes: tuple[str, int]) -> PendingWrite:
        return replace(self, items=(*self.items, *changes))

    def because(self, flow: str) -> PendingWrite:
        """Сказать, почему изменился кошелёк, для книги золота (``mmorpg.economy_log``).

        Только имя и ничего больше: ветка остаётся чистой, а строку пишет тот хендлер,
        который и делает запись.
        """
        return replace(self, gold_flow=flow)


@dataclass(frozen=True, slots=True)
class Clock:
    """Две вещи со сроком, оставшиеся в игре, и момент, на который их читают.

    Мир больше не переворачивается по общей страже: карта локации перекладывается
    по выработке узлов, а не по часам (ADR 0035). Со сроком остались прилавок
    лавки и личный откат сбора, и оба приходят сюда значениями, чтобы ветка
    оставалась без часов.
    """

    now: int = 0
    shop_rotation: int = 0
    gather_cooldown: int = 900


@dataclass(frozen=True, slots=True)
class Goods:
    """Что у игрока есть и чем торгует нынешний город.

    Передаётся хендлером: сама ветка не трогает хранилищ.
    """

    gold: int = 0
    owned: tuple[OwnedItem, ...] = ()
    stock: tuple[Item, ...] = ()
    prices: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocationSession:
    """Вылазка в одну локацию: где стоит игрок, и больше ничего.

    Карта постоянна, поэтому вылазке нечего о ней запоминать. То, что осталось в
    узлах, общее для всех, кто в месте, и читается заново на каждом шаге
    (``domain/rules/nodes.py``, docs/adr/0003).
    """

    city_id: str = ""
    slot: int = 0
    node: int = 0

    @property
    def active(self) -> bool:
        return bool(self.city_id and self.slot)


@dataclass(frozen=True, slots=True)
class Descent:
    """Незаконченный заход в подземелья города (ADR 0036, ADR 0041).

    ``started_at`` - момент, когда заход начался; он входит в сид, поэтому два
    захода подряд - это два разных подземелья.

    ``dungeon_id`` - какое из названных подземелий города (``[[city.dungeon]]``,
    ADR 0041). ``difficulty`` - выбранная сложность
    (``domain/rules/dungeon.Difficulty``). Оба входят в сид.

    ``layer`` - на каком слое заход стоит сейчас (0 - вход); ``room`` - вид
    комнаты этого слоя (``domain/rules/dungeon.RoomKind``). Карта нигде не
    хранится: и то и другое - чистая функция от сида захода и номера слоя.
    """

    city_id: str = ""
    level: int = 0
    layer: int = 0
    started_at: int = 0
    dungeon_id: str = ""
    difficulty: str = "recon"
    room: str = "skirmish"
    #: Это блуждающее подземелье в локации (ADR 0037), а не городской спуск. Тогда
    #: ``slot`` называет локацию, ``stamp`` - окно появления подземелья, и оба
    #: входят в сид захода вместо города и ``dungeon_id``.
    roamer: bool = False
    slot: int = 0
    stamp: int = 0
    group: bool = False

    @property
    def active(self) -> bool:
        return bool(self.city_id)


@dataclass(frozen=True, slots=True)
class PlayState:
    screen: ScreenId = ScreenId.MAIN_MENU
    stack: NavigationStack = field(default_factory=lambda: NavigationStack((ScreenId.MAIN_MENU,)))
    world_page: PageState = field(default_factory=PageState)
    location_page: PageState = field(default_factory=PageState)
    city_id: str = ""
    session: LocationSession = field(default_factory=LocationSession)
    descent: Descent = field(default_factory=Descent)
    notice: str = ""
    list_page: PageState = field(default_factory=PageState)
    skill_page: PageState = field(default_factory=PageState)
    mentor_page: PageState = field(default_factory=PageState)
    board_page: PageState = field(default_factory=PageState)
    # Что игрок сейчас выбирает: слот, грань, задание, подземелье.
    pick_slot: int = 0
    edge_skill: str = ""
    quest_id: str = ""
    craft_id: str = ""
    dungeon_pick: str = ""
    npc_id: str = ""
    # Момент, на который открыли экран ремесла: строка отката не должна тикать, пока
    # игрок ещё читает тот экран, на котором она напечатана.
    craft_moment: int = 0
    # Что правит смотритель: разновидность, сущность, поле, чужой персонаж. Всё
    # остальное панель читает заново на каждом шаге, потому что мир между двумя
    # нажатиями мог измениться - в том числе её же прошлым нажатием.
    keeper_kind: str = ""
    keeper_entity: str = ""
    keeper_field: str = ""
    keeper_target: int = 0
    keeper_typing: str = ""
    keeper_page: PageState = field(default_factory=PageState)
    #: Чем сужен список игроков в панели (уровень, город, гильдия, блокировка,
    #: активность). Переживает уход с экрана: смотритель настраивает фильтр и
    #: возвращается к списку.
    keeper_player_filter: PlayerFilter = field(default_factory=PlayerFilter)
    #: Причина блокировки, набранная до выбора срока. Живёт в состоянии, потому
    #: что набирают её одним сообщением, а срок нажимают следующим.
    keeper_reason: str = ""
    #: Набранное сообщение сейчас означает строку поиска по списку на экране.
    searching: bool = False
    #: Вещь, карточку которой открыли из сумки или с прилавка.
    item_id: str = ""
    # Скоропортящееся: очищается в начале каждого шага, читается хендлером.
    pending: PendingWrite = field(default_factory=PendingWrite)
    fight: str = ""
    #: Кого шаг попросил позвать в отряд. Переходное, как и ``fight``: хендлер
    #: читает и обнуляет, потому что звать умеет только он (``rules/party.py``).
    invite: int = 0
    #: Кого попросили позвать по набранному имени - когда позванного нет рядом.
    invite_name: str = ""
    #: Что шаг попросил сделать с отрядом: ``create``, ``disband``, ``leave``,
    #: ``accept``, ``decline``. Переходное, как и ``invite``: отряд лежит в общем
    #: хранилище, а автомат не читает и не пишет ничего.
    party_action: str = ""
    #: То же для гильдии: ``found``, ``disband``, ``leave``, ``accept``,
    #: ``decline``, ``invite``, ``promote``, ``demote``, ``kick``, ``deposit``,
    #: ``withdraw``. ``guild_arg`` - имя или сумма к нему. Переходное.
    guild_action: str = ""
    guild_arg: str = ""
    #: Адресная передача вещи в отряде или гильдии. ``transfer_scope`` —
    #: ``party`` или ``guild``; ``transfer_to`` — имя получателя;
    #: ``transfer_item`` — что передают. Всё это выбор игрока и переживает уход
    #: с экрана, а ``transfer_amount`` переходное: хендлер читает его и обнуляет,
    #: потому что двигать сумку другого игрока умеет только он.
    transfer_scope: str = ""
    transfer_to: str = ""
    transfer_item: str = ""
    transfer_amount: int = 0

    def at(self, screen: ScreenId) -> PlayState:
        return replace(self, screen=screen, stack=self.stack.push(screen), notice="")

    def with_notice(self, notice: str) -> PlayState:
        return replace(self, notice=notice)

    def storing(self, write: PendingWrite) -> PlayState:
        return replace(self, pending=write)

    def serialise(self) -> str:
        return json.dumps(
            {
                "screen": self.screen.value,
                "stack": self.stack.serialise(),
                "world_page": self.world_page.page,
                "location_page": self.location_page.page,
                "city": self.city_id,
                "session": [
                    self.session.city_id,
                    self.session.slot,
                    self.session.node,
                ],
                "descent": [
                    self.descent.city_id,
                    self.descent.level,
                    self.descent.layer,
                    self.descent.started_at,
                    self.descent.dungeon_id,
                    self.descent.difficulty,
                    self.descent.room,
                    self.descent.roamer,
                    self.descent.slot,
                    self.descent.stamp,
                    self.descent.group,
                ],
                "pick": self.pick_slot,
                "dungeon_pick": self.dungeon_pick,
                "edge": self.edge_skill,
                "quest": self.quest_id,
                "npc": self.npc_id,
                "item": self.item_id,
                "transfer": [self.transfer_scope, self.transfer_to, self.transfer_item],
                "searching": self.searching,
                "list_filters": [
                    self.list_page.filters.category,
                    self.list_page.filters.query,
                ],
                "craft": [self.craft_id, self.craft_moment],
                "pages": [self.list_page.page, self.skill_page.page, self.board_page.page],
                "keeper": [
                    self.keeper_kind,
                    self.keeper_entity,
                    self.keeper_field,
                    self.keeper_target,
                    self.keeper_typing,
                    self.keeper_page.page,
                    self.keeper_reason,
                ],
                "keeper_pf": [
                    self.keeper_player_filter.level_min,
                    self.keeper_player_filter.level_max,
                    self.keeper_player_filter.city_id,
                    self.keeper_player_filter.guild,
                    self.keeper_player_filter.banned,
                    self.keeper_player_filter.active_since,
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def deserialise(cls, raw: str) -> PlayState:
        data = json.loads(raw)
        # Хвост читается с запасом: состояние переживает выкатку, и запись
        # старого образца называла ещё поколение и маску пройденного.
        session_parts = [*data.get("session", []), "", 0, 0][:3]
        city_id, slot, node = session_parts
        # Хвост читается с запасом: запись старого образца не называла ни
        # подземелье, ни сложность, ни вид комнаты. На месте ``dungeon_id`` там
        # лежал числовой ``tier`` (1/2): ``str(1)`` не совпадёт ни с одним id,
        # и заход развалится в город (``Claude.md``, правило 8, ADR 0041).
        raw_descent = data.get("descent", [])
        descent_city = raw_descent[0] if len(raw_descent) > 0 else ""
        descent_level = raw_descent[1] if len(raw_descent) > 1 else 0
        descent_layer = raw_descent[2] if len(raw_descent) > 2 else 0
        descent_started = raw_descent[3] if len(raw_descent) > 3 else 0
        descent_dungeon = raw_descent[4] if len(raw_descent) > 4 else ""
        descent_difficulty = raw_descent[5] if len(raw_descent) > 5 else "recon"
        descent_room = raw_descent[6] if len(raw_descent) > 6 else "skirmish"
        descent_roamer = raw_descent[7] if len(raw_descent) > 7 else False
        descent_slot = raw_descent[8] if len(raw_descent) > 8 else 0
        descent_stamp = raw_descent[9] if len(raw_descent) > 9 else 0
        descent_group = raw_descent[10] if len(raw_descent) > 10 else False
        # Раньше здесь лежала пара [вид, слот]: пассивные умения тоже клали в
        # слоты. Сохранённая пара читается как её второй член - номер слота.
        pick_raw = data.get("pick", 0)
        pick_slot = pick_raw[1] if isinstance(pick_raw, list) else pick_raw
        list_page, skill_page, board_page = data.get("pages", [1, 1, 1])
        craft_id, craft_moment = data.get("craft", ["", 0])
        list_category, list_query = [*data.get("list_filters", []), "", ""][:2]
        # Хвост списка читается с запасом: состояние переживает выкатку, а
        # сохранённому состоянию не верят (``Claude.md``, правило 8).
        keeper = [*data.get("keeper", []), "", "", "", 0, "", 1, ""][:7]
        pf = [*data.get("keeper_pf", []), 0, 0, "", "", False, 0][:6]
        # Хвост читается с запасом: запись старого образца не называла передачу.
        transfer_scope, transfer_to, transfer_item = [*data.get("transfer", []), "", "", ""][:3]
        return cls(
            screen=ScreenId(data["screen"]),
            stack=NavigationStack.deserialise(data.get("stack", "")),
            world_page=PageState(page=int(data.get("world_page", 1))),
            location_page=PageState(page=int(data.get("location_page", 1))),
            city_id=data.get("city", ""),
            session=LocationSession(city_id=str(city_id), slot=int(slot), node=int(node)),
            descent=Descent(
                city_id=descent_city,
                level=int(descent_level),
                layer=int(descent_layer),
                started_at=int(descent_started),
                dungeon_id=str(descent_dungeon),
                difficulty=str(descent_difficulty) or "recon",
                room=str(descent_room) or "skirmish",
                roamer=bool(descent_roamer),
                slot=int(descent_slot),
                stamp=int(descent_stamp),
                group=bool(descent_group),
            ),
            list_page=PageState(
                page=int(list_page),
                filters=ListFilters(category=str(list_category), query=str(list_query)),
            ),
            skill_page=PageState(page=int(skill_page)),
            board_page=PageState(page=int(board_page)),
            pick_slot=int(pick_slot),
            dungeon_pick=str(data.get("dungeon_pick", "")),
            edge_skill=data.get("edge", ""),
            quest_id=data.get("quest", ""),
            npc_id=data.get("npc", ""),
            item_id=data.get("item", ""),
            searching=bool(data.get("searching", False)),
            craft_id=craft_id,
            craft_moment=int(craft_moment),
            keeper_kind=str(keeper[0]),
            keeper_entity=str(keeper[1]),
            keeper_field=str(keeper[2]),
            keeper_target=int(keeper[3]),
            keeper_typing=str(keeper[4]),
            keeper_page=PageState(page=int(keeper[5])),
            keeper_reason=str(keeper[6]),
            keeper_player_filter=PlayerFilter(
                level_min=int(pf[0]),
                level_max=int(pf[1]),
                city_id=str(pf[2]),
                guild=str(pf[3]),
                banned=bool(pf[4]),
                active_since=int(pf[5]),
            ),
            transfer_scope=str(transfer_scope),
            transfer_to=str(transfer_to),
            transfer_item=str(transfer_item),
        )


def go_back(state: PlayState) -> PlayState:
    """Один шаг назад, с сохранением всего, что уже выбрано."""
    stack, previous = state.stack.pop()
    # Уходя, забываем и заданный вопрос: набранное после «Поиска» на другом
    # экране означало бы уже не поиск, а неизвестную кнопку.
    cleaned = replace(state, pending=PendingWrite(), fight="", keeper_typing="", searching=False)
    if previous is None:
        target = back_target(state.screen) or ScreenId.MAIN_MENU
        return replace(cleaned, screen=target, stack=NavigationStack((target,)), notice="")
    leaving_location = state.screen is ScreenId.LOCATION
    return replace(
        cleaned,
        screen=previous,
        stack=stack,
        notice="",
        session=LocationSession() if leaving_location else state.session,
    )


def page_move(command: Command, current: PageState, pages: int) -> PageState | None:
    """Общая арифметика страниц. ``None``, когда команда страницу не листала."""
    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return current.moved(delta, pages)
    if command.intent is Intent.PAGE and command.number is not None:
        return current.jumped(command.number, pages)
    return None


#: Какое состояние списка держит каждый экран со списком. Поиск и разделы
#: правят именно его, поэтому знать это надо в одном месте, а не в девяти.
LIST_PAGE_FIELD: dict[ScreenId, str] = {
    ScreenId.INVENTORY: "list_page",
    ScreenId.SHOP: "list_page",
    ScreenId.SELL: "list_page",
    ScreenId.GUILD_ROSTER: "list_page",
    ScreenId.TRANSFER_TO: "list_page",
    ScreenId.TRANSFER_ITEM: "list_page",
    ScreenId.SKILL_PICK: "list_page",
    ScreenId.DUNGEON: "list_page",
    ScreenId.CHAMBER_PLEDGE: "list_page",
    ScreenId.SKILLS: "skill_page",
    ScreenId.MENTOR: "mentor_page",
    ScreenId.QUEST_BOARD: "board_page",
    ScreenId.WORLD: "world_page",
    ScreenId.LOCATION_LIST: "location_page",
}


def list_page(state: PlayState) -> PageState:
    """Состояние списка, который сейчас на экране."""
    match LIST_PAGE_FIELD.get(state.screen, "list_page"):
        case "skill_page":
            return state.skill_page
        case "mentor_page":
            return state.mentor_page
        case "board_page":
            return state.board_page
        case "world_page":
            return state.world_page
        case "location_page":
            return state.location_page
        case _:
            return state.list_page


def with_list_page(state: PlayState, page: PageState) -> PlayState:
    """Тот же выбор наоборот. Расписан руками: полей всего шесть, а
    ``replace(**{...})`` — это дыра, в которую пройдёт любая опечатка."""
    match LIST_PAGE_FIELD.get(state.screen, "list_page"):
        case "skill_page":
            return replace(state, skill_page=page)
        case "mentor_page":
            return replace(state, mentor_page=page)
        case "board_page":
            return replace(state, board_page=page)
        case "world_page":
            return replace(state, world_page=page)
        case "location_page":
            return replace(state, location_page=page)
        case _:
            return replace(state, list_page=page)
