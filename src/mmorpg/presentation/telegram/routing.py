"""Routing: pressed button text and typed commands to intents.

Two entry points lead to the same intents:

- a **button**, matched by exact text against the current screen;
- a **typed command**, so the game stays playable when the keyboard fails to
  render at all (accessibility rule 10).

Anything that matches neither is a stale button from an older keyboard. That is
not an error - it gets an explicit answer and the current keyboard back
(accessibility rule 12). The game never stays silent and never raises.
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
    SKILL = "skill"
    RACIAL = "racial"
    BAG = "bag"
    FLEE = "flee"
    PAGE = "page"
    NEXT_PAGE = "next_page"
    PREVIOUS_PAGE = "previous_page"
    SELECT = "select"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    intent: Intent
    argument: str = ""
    number: int | None = None


# Typed duplicates for every action. Russian commands, because the interface is
# Russian; Latin aliases are accepted so a player on a Latin-only keyboard is not
# locked out.
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
    # "/род" and "/народ" stay: both named this slot before "/раса" came back.
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
    "сумка": Intent.BAG,
    "bag": Intent.BAG,
    "бежать": Intent.FLEE,
    "flee": Intent.FLEE,
}


def parse_command(text: str) -> Command | None:
    """Parse a typed command. Returns ``None`` when the text is not a command."""
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
    (labels.BAG, Intent.BAG),
    (labels.FLEE, Intent.FLEE),
    (labels.NEXT_PAGE, Intent.NEXT_PAGE),
    (labels.PREVIOUS_PAGE, Intent.PREVIOUS_PAGE),
)


def resolve(text: str, screen: Screen) -> Command:
    """Turn an incoming message into an intent, in the context of a screen.

    Typed commands win over buttons, so ``/меню`` works from anywhere even if some
    screen ever labels a button the same way.
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
    """The answer to a button that belongs to a different screen (rule 12)."""
    return f"Действие сейчас недоступно, вы находитесь в: {screen_title}. Ниже актуальные кнопки."
