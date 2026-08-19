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
from mmorpg.domain.ports.repositories import AccessibilitySettings
from mmorpg.presentation.telegram.routing import Command, Intent
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import PageState
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
    """What the handler must store after this step.

    ``items`` is a list of bag changes: a positive number adds, a negative one
    takes away. Nothing here is applied by the flow itself.

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
    #: Что записать в журнал смотрителя. Момент и имя проставляет хендлер.
    note: KeeperEntry | None = None
    #: Какую уборку выполнить (``application/services/keeper_panel.py``).
    service: str = ""
    #: Перечитать правки из хранилища.
    reload: bool = False
    #: Почему изменился кошелёк: метка для денежного журнала
    #: (``mmorpg.economy_log``). Сама по себе ничего не записывает.
    gold_flow: str = ""

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
            and self.note is None
            and not self.service
            and not self.reload
        )

    def with_items(self, *changes: tuple[str, int]) -> PendingWrite:
        return replace(self, items=(*self.items, *changes))

    def because(self, flow: str) -> PendingWrite:
        """Say why the purse changed, for the gold journal (``mmorpg.economy_log``).

        A label and nothing more: the flow stays pure and the handler that does
        the writing is the one that writes the line.
        """
        return replace(self, gold_flow=flow)


@dataclass(frozen=True, slots=True)
class Clock:
    """The two timed things left in the game, and the moment they are read at.

    The world no longer turns over on a shared watch: a location keeps its map
    until it is cleared out. What is still timed is a shop shelf and a personal
    gathering cooldown, and both arrive here as values so the flow stays free of
    the clock (``docs/adr/0003-location-generations.md``).
    """

    now: int = 0
    shop_rotation: int = 0
    gather_cooldown: int = 900


@dataclass(frozen=True, slots=True)
class Goods:
    """What the player owns and what the current city sells.

    Passed in from the handler: the flow itself never touches a repository.
    """

    gold: int = 0
    owned: tuple[OwnedItem, ...] = ()
    stock: tuple[Item, ...] = ()
    prices: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocationSession:
    """A visit to one location.

    ``generation`` is which map of this place is standing: it is captured on
    entry and kept until the player leaves, so the ground never changes under
    their feet mid-visit. It goes up only when the location is cleared out, and
    it is shared by everybody in it (docs/adr/0003).
    """

    city_id: str = ""
    slot: int = 0
    generation: int = 0
    node: int = 0
    cleared: int = 0

    @property
    def active(self) -> bool:
        return bool(self.city_id and self.slot)


@dataclass(frozen=True, slots=True)
class Descent:
    """An unfinished run into the dungeons of a city.

    ``started_at`` is the moment the descent began; it is part of the seed, so
    two descents in a row are two different descents.
    """

    city_id: str = ""
    level: int = 0
    depth: int = 0
    started_at: int = 0

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
    stub_title: str = ""
    notice: str = ""
    list_page: PageState = field(default_factory=PageState)
    skill_page: PageState = field(default_factory=PageState)
    mentor_page: PageState = field(default_factory=PageState)
    board_page: PageState = field(default_factory=PageState)
    # What the player is in the middle of choosing: a slot, an edge, a contract.
    pick_kind: str = ""
    pick_slot: int = 0
    edge_skill: str = ""
    quest_id: str = ""
    craft_id: str = ""
    npc_id: str = ""
    # The moment the craft screen was opened at: the cooldown line must not tick
    # down while the player is still reading the screen it was printed on.
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
    #: Причина блокировки, набранная до выбора срока. Живёт в состоянии, потому
    #: что набирают её одним сообщением, а срок нажимают следующим.
    keeper_reason: str = ""
    # Transient: cleared at the start of every step, read by the handler.
    pending: PendingWrite = field(default_factory=PendingWrite)
    fight: str = ""

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
                    self.session.generation,
                    self.session.node,
                    self.session.cleared,
                ],
                "descent": [
                    self.descent.city_id,
                    self.descent.level,
                    self.descent.depth,
                    self.descent.started_at,
                ],
                "stub": self.stub_title,
                "pick": [self.pick_kind, self.pick_slot],
                "edge": self.edge_skill,
                "quest": self.quest_id,
                "npc": self.npc_id,
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
            },
            ensure_ascii=False,
        )

    @classmethod
    def deserialise(cls, raw: str) -> PlayState:
        data = json.loads(raw)
        city_id, slot, generation, node, cleared = data.get("session", ["", 0, 0, 0, 0])
        descent_city, descent_level, depth, descent_started = data.get("descent", ["", 0, 0, 0])
        pick_kind, pick_slot = data.get("pick", ["", 0])
        list_page, skill_page, board_page = data.get("pages", [1, 1, 1])
        craft_id, craft_moment = data.get("craft", ["", 0])
        # Хвост списка читается с запасом: состояние переживает выкатку, а
        # сохранённому состоянию не верят (``Claude.md``, правило 8).
        keeper = [*data.get("keeper", []), "", "", "", 0, "", 1, ""][:7]
        return cls(
            screen=ScreenId(data["screen"]),
            stack=NavigationStack.deserialise(data.get("stack", "")),
            world_page=PageState(page=int(data.get("world_page", 1))),
            location_page=PageState(page=int(data.get("location_page", 1))),
            city_id=data.get("city", ""),
            session=LocationSession(
                city_id=city_id,
                slot=int(slot),
                generation=int(generation),
                node=int(node),
                cleared=int(cleared),
            ),
            descent=Descent(
                city_id=descent_city,
                level=int(descent_level),
                depth=int(depth),
                started_at=int(descent_started),
            ),
            stub_title=data.get("stub", ""),
            list_page=PageState(page=int(list_page)),
            skill_page=PageState(page=int(skill_page)),
            board_page=PageState(page=int(board_page)),
            pick_kind=str(pick_kind),
            pick_slot=int(pick_slot),
            edge_skill=data.get("edge", ""),
            quest_id=data.get("quest", ""),
            npc_id=data.get("npc", ""),
            craft_id=craft_id,
            craft_moment=int(craft_moment),
            keeper_kind=str(keeper[0]),
            keeper_entity=str(keeper[1]),
            keeper_field=str(keeper[2]),
            keeper_target=int(keeper[3]),
            keeper_typing=str(keeper[4]),
            keeper_page=PageState(page=int(keeper[5])),
            keeper_reason=str(keeper[6]),
        )


def go_back(state: PlayState) -> PlayState:
    """Один шаг назад, с сохранением всего, что уже выбрано."""
    stack, previous = state.stack.pop()
    cleaned = replace(state, pending=PendingWrite(), fight="", keeper_typing="")
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
    """Shared paging arithmetic. ``None`` when this command was not a page move."""
    if command.intent in {Intent.NEXT_PAGE, Intent.PREVIOUS_PAGE}:
        delta = 1 if command.intent is Intent.NEXT_PAGE else -1
        return current.moved(delta, pages)
    if command.intent is Intent.PAGE and command.number is not None:
        return current.jumped(command.number, pages)
    return None
