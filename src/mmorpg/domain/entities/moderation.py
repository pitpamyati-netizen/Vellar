"""Наказание и запись о том, кто его наложил.

Две вещи, и обе про людей, а не про мир. :class:`Ban` — временное отлучение от
игры: персонаж цел, вещи целы, но бот с этим аккаунтом не разговаривает, пока
срок не вышел. :class:`KeeperEntry` — строка журнала: что смотритель сделал, с
кем и когда.

Журнал существует потому, что смотрителей больше одного (право раздаётся из
панели, ADR 0008), а панель раздаёт золото, уровни и блокировки. Работа, которую
нельзя посмотреть, — это работа, за которую некому отвечать.

Время здесь — секунды unix, как и везде в домене: часов у домена нет, момент
приходит аргументом (``Claude.md``, правило 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class KeeperAction(StrEnum):
    """Что смотритель сделал. Записывается всё, что меняет чужое или стирает."""

    GOLD = "gold"
    LEVEL = "level"
    HEAL = "heal"
    POINTS = "points"
    MOVE = "move"
    DELETE = "delete"
    PROMOTE = "promote"
    DEMOTE = "demote"
    BAN = "ban"
    UNBAN = "unban"
    WARN = "warn"
    EDIT = "edit"
    FORGET = "forget"
    SWEEP = "sweep"
    ROLLBACK = "rollback"
    RENAME = "rename"
    GRANT_ITEM = "grant_item"
    SKILL = "skill"
    QUEST = "quest"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Ban:
    """Отлучение аккаунта от игры.

    ``until`` — момент, когда оно кончится. Ноль означает «не заблокирован»,
    отрицательное — «навсегда»: срок без конца надо было чем-то обозначить, и
    число меньше любого прошлого лучше огромного числа, которое однажды
    наступит.
    """

    until: int = 0
    reason: str = ""

    @property
    def forever(self) -> bool:
        return self.until < 0


@dataclass(frozen=True, slots=True)
class KeeperEntry:
    """Одна строка журнала смотрителя.

    Имена хранятся тем, чем были в тот момент: персонажа могут переименовать или
    стереть, а запись о том, что с ним сделали, остаётся читаемой.
    """

    at: int = 0
    keeper_id: int = 0
    keeper_name: str = ""
    action: KeeperAction = KeeperAction.EDIT
    target: str = ""
    detail: str = ""
