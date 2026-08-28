"""Сроки блокировки и слова, которыми о ней говорят.

Правило здесь одно и простое: заблокированный аккаунт не играет, пока срок не
вышел, а вышел он или нет — считается от момента, который передали. Ничего не
стирается и ничего не отбирается: блокировка — это пауза, а не наказание
кошельком, и снимается она одним нажатием.

Сроки перечислены наперёд, а не набираются числом: смотритель выбирает из
списка, и «случайно на тысячу лет» не получается. Навсегда — тоже строка списка,
потому что оно тоже иногда нужно.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mmorpg.domain.entities.moderation import Ban, KeeperAction

HOUR = 60 * 60
DAY = 24 * HOUR

#: Чем помечен срок без конца (``entities/moderation.Ban``).
FOREVER = -1


@dataclass(frozen=True, slots=True)
class Sentence:
    """Один срок из списка. ``seconds`` — ноль только у бессрочного."""

    key: str
    name: str
    seconds: int

    @property
    def forever(self) -> bool:
        return self.seconds <= 0


SENTENCES: tuple[Sentence, ...] = (
    Sentence("hour", "На час", HOUR),
    Sentence("day", "На сутки", DAY),
    Sentence("week", "На неделю", 7 * DAY),
    Sentence("month", "На месяц", 30 * DAY),
    Sentence("forever", "Навсегда", 0),
)

#: Названия действий смотрителя для журнала. Слова русские, потому что журнал
#: читает человек, а не машина.
ACTIONS: Mapping[KeeperAction, str] = {
    KeeperAction.GOLD: "выдал золото",
    KeeperAction.LEVEL: "поднял уровень",
    KeeperAction.HEAL: "залечил раны",
    KeeperAction.POINTS: "выдал очки",
    KeeperAction.MOVE: "перевёл в город",
    KeeperAction.DELETE: "удалил персонажа",
    KeeperAction.PROMOTE: "сделал смотрителем",
    KeeperAction.DEMOTE: "убрал из смотрителей",
    KeeperAction.BAN: "заблокировал",
    KeeperAction.UNBAN: "снял блокировку",
    KeeperAction.EDIT: "правил содержимое",
    KeeperAction.FORGET: "снял правку",
    KeeperAction.SWEEP: "убрался в базе",
    KeeperAction.ROLLBACK: "откатил сделку",
    KeeperAction.RENAME: "переименовал",
    KeeperAction.GRANT_ITEM: "выдал вещь",
    KeeperAction.SKILL: "правил умения",
    KeeperAction.QUEST: "правил задание",
    KeeperAction.GROUP: "правил отряд или гильдию",
}


def sentence_of(key: str) -> Sentence | None:
    return next((sentence for sentence in SENTENCES if sentence.key == key), None)


def sentence_named(name: str) -> Sentence | None:
    """Срок по надписи кнопки. Регистр и пробелы по краям не считаются."""
    wanted = name.strip().casefold()
    return next((sentence for sentence in SENTENCES if sentence.name.casefold() == wanted), None)


def imposed(sentence: Sentence, reason: str, *, now: int) -> Ban:
    """Блокировка, наложенная сейчас. Бессрочная не зависит от часов вовсе."""
    until = FOREVER if sentence.forever else now + sentence.seconds
    return Ban(until=until, reason=reason.strip())


def lifted() -> Ban:
    """Снятая блокировка. Причина стирается вместе со сроком: её больше нет."""
    return Ban()


def is_banned(ban: Ban, *, now: int) -> bool:
    return ban.forever or ban.until > now


def remaining(ban: Ban, *, now: int) -> int:
    """Сколько секунд осталось. Ноль — блокировки нет, ``FOREVER`` — без конца."""
    if ban.forever:
        return FOREVER
    return max(0, ban.until - now)
