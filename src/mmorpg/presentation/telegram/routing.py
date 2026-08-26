"""Маршрутизация: текст нажатой кнопки и набранные команды - в намерения.

К одним и тем же намерениям ведут два входа:

- **кнопка**, сверенная по точному тексту с нынешним экраном;
- **набранная команда**, чтобы в игру можно было играть, даже когда клавиатура
  вовсе не нарисовалась (правило доступности 10).

Всё, что не совпало ни с тем ни с другим, - устаревшая кнопка старой клавиатуры.
Это не ошибка: на неё отвечают прямо и возвращают нынешнюю клавиатуру (правило
доступности 12). Игра никогда не молчит и никогда не падает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import Screen


class Intent(StrEnum):
    BACK = "back"
    LOOK = "look"
    MAIN_MENU = "main_menu"
    ATTACK = "attack"
    DEFEND = "defend"
    SKILL = "skill"
    RACIAL = "racial"
    BAG = "bag"
    FLEE = "flee"
    YIELD = "yield"
    REFRESH = "refresh"
    PARTY = "party"
    PARTY_CREATE = "party_create"
    PARTY_DISBAND = "party_disband"
    PARTY_INVITE = "party_invite"
    PARTY_ACCEPT = "party_accept"
    PARTY_DECLINE = "party_decline"
    PARTY_LEAVE = "party_leave"
    PAGE = "page"
    NEXT_PAGE = "next_page"
    PREVIOUS_PAGE = "previous_page"
    SEARCH = "search"
    FILTERS = "filters"
    RESET_FILTERS = "reset_filters"
    SELECT = "select"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    intent: Intent
    argument: str = ""
    number: int | None = None


# Набираемые двойники каждого действия. Команды русские, потому что интерфейс русский;
# латинские написания принимаются, чтобы игрок с одной лишь латинской раскладкой не
# оказался заперт.
SIMPLE_COMMANDS: dict[str, Intent] = {
    "/назад": Intent.BACK,
    "/back": Intent.BACK,
    "/осмотреться": Intent.LOOK,
    "/look": Intent.LOOK,
    "/меню": Intent.MAIN_MENU,
    "/menu": Intent.MAIN_MENU,
    "/сумка": Intent.BAG,
    "/bag": Intent.BAG,
    "/бежать": Intent.FLEE,
    "/flee": Intent.FLEE,
    "/защита": Intent.DEFEND,
    "/защититься": Intent.DEFEND,
    "/defend": Intent.DEFEND,
    # Выход из боя, который бросили с той стороны, и способ услышать его заново.
    "/сдаться": Intent.YIELD,
    "/yield": Intent.YIELD,
    "/обновить": Intent.REFRESH,
    "/refresh": Intent.REFRESH,
    # Отряд: одна команда на всё, что с ним делают.
    "/отряд": Intent.PARTY,
    "/party": Intent.PARTY,
    # Списки: то же, что три кнопки под страницами, но набором.
    "/поиск": Intent.SEARCH,
    "/search": Intent.SEARCH,
    "/фильтры": Intent.FILTERS,
    "/filters": Intent.FILTERS,
    "/сбросить": Intent.RESET_FILTERS,
    "/reset": Intent.RESET_FILTERS,
    # «/род» и «/народ» остаются: оба называли этот шаг до того, как вернулось «/раса».
    "/раса": Intent.RACIAL,
    "/род": Intent.RACIAL,
    "/народ": Intent.RACIAL,
    "/racial": Intent.RACIAL,
}

_SKILL_COMMAND = re.compile(r"^/(?:умение|skill)\s+(\d+)$", re.IGNORECASE)
_PAGE_COMMAND = re.compile(r"^/(?:страница|page)\s+(\d+)$", re.IGNORECASE)
_COMBAT_COMMAND = re.compile(r"^/(?:бой|combat)\s+(\S+)$", re.IGNORECASE)

_COMBAT_WORDS: dict[str, Intent] = {
    "атака": Intent.ATTACK,
    "attack": Intent.ATTACK,
    "защита": Intent.DEFEND,
    "defend": Intent.DEFEND,
    "сумка": Intent.BAG,
    "bag": Intent.BAG,
    "бежать": Intent.FLEE,
    "flee": Intent.FLEE,
    "сдаться": Intent.YIELD,
    "yield": Intent.YIELD,
    "обновить": Intent.REFRESH,
    "refresh": Intent.REFRESH,
}

#: Слова после ``/отряд``: завести, расформировать, позвать, принять зов, уйти.
_PARTY_COMMAND = re.compile(r"^/(?:отряд|party)\s+(\S+)$", re.IGNORECASE)
_PARTY_WORDS: dict[str, Intent] = {
    "создать": Intent.PARTY_CREATE,
    "create": Intent.PARTY_CREATE,
    "расформировать": Intent.PARTY_DISBAND,
    "распустить": Intent.PARTY_DISBAND,
    "disband": Intent.PARTY_DISBAND,
    "пригласить": Intent.PARTY_INVITE,
    "позвать": Intent.PARTY_INVITE,
    "invite": Intent.PARTY_INVITE,
    "принять": Intent.PARTY_ACCEPT,
    "accept": Intent.PARTY_ACCEPT,
    "отказать": Intent.PARTY_DECLINE,
    "decline": Intent.PARTY_DECLINE,
    "уйти": Intent.PARTY_LEAVE,
    "leave": Intent.PARTY_LEAVE,
}


def parse_command(text: str) -> Command | None:
    """Разобрать набранную команду. ``None``, когда текст командой не является."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    lowered = stripped.casefold()
    if lowered in SIMPLE_COMMANDS:
        return Command(intent=SIMPLE_COMMANDS[lowered])

    if (match := _SKILL_COMMAND.match(stripped)) is not None:
        return Command(intent=Intent.SKILL, number=int(match.group(1)))

    if (match := _PAGE_COMMAND.match(stripped)) is not None:
        return Command(intent=Intent.PAGE, number=int(match.group(1)))

    if (match := _PARTY_COMMAND.match(stripped)) is not None:
        word = match.group(1).casefold()
        if word in _PARTY_WORDS:
            return Command(intent=_PARTY_WORDS[word])
        return Command(intent=Intent.PARTY, argument=word)

    if (match := _COMBAT_COMMAND.match(stripped)) is not None:
        word = match.group(1).casefold()
        if word in _COMBAT_WORDS:
            return Command(intent=_COMBAT_WORDS[word])
        return Command(intent=Intent.UNKNOWN, argument=word)

    return Command(intent=Intent.UNKNOWN, argument=stripped)


_BUTTON_INTENTS: tuple[tuple[object, Intent], ...] = (
    (labels.BACK, Intent.BACK),
    (labels.LOOK, Intent.LOOK),
    (labels.MAIN_MENU, Intent.MAIN_MENU),
    (labels.ATTACK, Intent.ATTACK),
    (labels.DEFEND, Intent.DEFEND),
    (labels.BAG, Intent.BAG),
    (labels.FLEE, Intent.FLEE),
    (labels.BATTLE_YIELD, Intent.YIELD),
    (labels.BATTLE_REFRESH, Intent.REFRESH),
    (labels.PARTY, Intent.PARTY),
    (labels.PARTY_CREATE, Intent.PARTY_CREATE),
    (labels.PARTY_DISBAND, Intent.PARTY_DISBAND),
    (labels.PARTY_INVITE, Intent.PARTY_INVITE),
    (labels.PARTY_ACCEPT, Intent.PARTY_ACCEPT),
    (labels.PARTY_DECLINE, Intent.PARTY_DECLINE),
    (labels.PARTY_LEAVE, Intent.PARTY_LEAVE),
    (labels.NEXT_PAGE, Intent.NEXT_PAGE),
    (labels.PREVIOUS_PAGE, Intent.PREVIOUS_PAGE),
    (labels.SEARCH, Intent.SEARCH),
    (labels.FILTERS, Intent.FILTERS),
    (labels.RESET_FILTERS, Intent.RESET_FILTERS),
)


def resolve(text: str, screen: Screen) -> Command:
    """Превратить пришедшее сообщение в намерение, в рамках экрана.

    Набранные команды сильнее кнопок, поэтому ``/меню`` работает откуда угодно, даже
    если когда-нибудь какой-то экран назовёт кнопку так же.
    """
    if (command := parse_command(text)) is not None:
        return command

    for candidate, intent in _BUTTON_INTENTS:
        if isinstance(candidate, labels.Label) and candidate.matches(text):
            return Command(intent=intent)

    matched = screen.find(text)
    if matched is not None:
        return Command(intent=Intent.SELECT, argument=matched.text)

    return Command(intent=Intent.UNKNOWN, argument=text.strip())


def stale_button_answer(screen_title: str) -> str:
    """Ответ на кнопку, принадлежащую другому экрану (правило 12)."""
    return f"Действие сейчас недоступно, вы находитесь в: {screen_title}. Ниже актуальные кнопки."
