"""Места в отряде: кто держит удар, кто лечит, кто бьёт, кто идёт первым.

Отряд из пятерых, где все дерутся одинаково, - это один и тот же бой, только
впятеро быстрее. Место в отряде делает его разговором: щит принимает удар и за
это бьёт слабее, лекарь лечит вдвое лучше и почти не бьёт вовсе, клинок бьёт
сильнее и получает больше, дозорный ходит раньше всех и падает раньше всех.

Ни одно место не даёт прибавки даром. Отряд не делает игру легче (``Claude.md``,
правило 3): противник тот же, плата делится, а места только решают, кому в этом
бою что достанется.

Вожак - не боевое место: он зовёт, распускает отряд и роздаёт места, а дерётся
тем, что взял себе сам. Прибавок у вожака нет, и обещать их нечего.

Место занято одним: два щита в отряде из пятерых - это не строй, а два человека,
которых бьют по очереди.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class PartyRole(StrEnum):
    """Пять мест. Больше их не бывает."""

    #: Тот, кто собрал отряд: зовёт, распускает, роздаёт места.
    LEADER = "leader"
    #: Тот, кого бьют: стая идёт на него, пока он держится.
    SHIELD = "shield"
    MENDER = "mender"
    BLADE = "blade"
    SCOUT = "scout"


#: Как место называется вслух.
ROLE_NAMES: Mapping[PartyRole, str] = MappingProxyType(
    {
        PartyRole.LEADER: "Вожак",
        PartyRole.SHIELD: "Щит",
        PartyRole.MENDER: "Лекарь",
        PartyRole.BLADE: "Клинок",
        PartyRole.SCOUT: "Дозорный",
    }
)

#: Чем место занимаются, одной строкой: ровно то, что движок считает.
ROLE_DUTIES: Mapping[PartyRole, str] = MappingProxyType(
    {
        PartyRole.LEADER: "зовёт и роздаёт места; прибавок не даёт",
        PartyRole.SHIELD: "броня выше на 40 процентов, удар слабее на 25; "
        "стая бьёт по нему, пока он держится выше четверти здоровья",
        PartyRole.MENDER: "лечение выше на 40 процентов, удар слабее на 30",
        PartyRole.BLADE: "удар выше на 25 процентов, чужой удар по нему тоже выше на 25",
        PartyRole.SCOUT: "инициатива выше на 30 процентов, здоровья меньше на 15",
    }
)

#: Что место делает с числами бойца. Ключи - те, что движок считает
#: (``rules/modifiers.EFFECTIVE_KEYS``): места обещают ровно это и ничего сверх.
ROLE_MODIFIERS: Mapping[PartyRole, Mapping[str, float]] = MappingProxyType(
    {
        PartyRole.LEADER: MappingProxyType({}),
        PartyRole.SHIELD: MappingProxyType({"armor_percent": 40.0, "damage_percent": -25.0}),
        PartyRole.MENDER: MappingProxyType({"healing_done_percent": 40.0, "damage_percent": -30.0}),
        PartyRole.BLADE: MappingProxyType({"damage_percent": 25.0, "damage_taken_percent": 25.0}),
        PartyRole.SCOUT: MappingProxyType({"initiative_percent": 30.0, "health_percent": -15.0}),
    }
)

#: Пока щит держится выше этой доли здоровья, стая идёт на него. Ниже - чует
#: слабину и берётся за тех, кто мягче: держать щит на ногах и есть работа
#: лекаря (ADR 0025).
SHIELD_HOLDS_ABOVE = 0.25

#: Слова, которыми место называют в команде. Русские и латинские - как везде:
#: игрок с одной раскладкой не должен оказаться заперт (``routing.py``).
ROLE_WORDS: Mapping[str, PartyRole] = MappingProxyType(
    {
        "вожак": PartyRole.LEADER,
        "leader": PartyRole.LEADER,
        "щит": PartyRole.SHIELD,
        "shield": PartyRole.SHIELD,
        "танк": PartyRole.SHIELD,
        "tank": PartyRole.SHIELD,
        "лекарь": PartyRole.MENDER,
        "mender": PartyRole.MENDER,
        "healer": PartyRole.MENDER,
        "клинок": PartyRole.BLADE,
        "blade": PartyRole.BLADE,
        "дозорный": PartyRole.SCOUT,
        "дозор": PartyRole.SCOUT,
        "scout": PartyRole.SCOUT,
    }
)

#: Слова, которыми место освобождают.
CLEAR_WORDS: frozenset[str] = frozenset({"снять", "никто", "clear", "none"})


def role_by_word(word: str) -> PartyRole | None:
    """Место по набранному слову. ``None`` - такого места нет."""
    return ROLE_WORDS.get(word.casefold())


def role_name(role: PartyRole | None) -> str:
    """Как место называется вслух. Пустое место - «без места»."""
    return ROLE_NAMES[role] if role is not None else "без места"


def modifiers_of(role: PartyRole | None) -> Mapping[str, float]:
    """Прибавки этого места. Ни одно место не прибавляет молча."""
    return ROLE_MODIFIERS[role] if role is not None else MappingProxyType({})
