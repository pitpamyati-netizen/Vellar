"""Parsing what a player typed in the game group.

The group has no screens and no state: a message either is a command aimed at
another player or it is none of the bot's business. So the parser is strict and
silent - it returns ``None`` for anything it does not recognise, and the handler
says nothing at all rather than answering strangers' conversations.

Grammar (``Narrative.md``, section 9), always as a reply to the target's message:

    профиль
    продать <цена> <предмет>
    купить  <цена> <предмет>
    передать <предмет>
    передать <количество> <предмет>
    передать <количество> золота
    принять <номер>
    отказ    <номер>

The item is written the way a player says it, so matching is case-insensitive,
ignores ``ё`` and collapses spaces. It is resolved against the speaker's inventory
by the caller, not here: this module knows grammar, not goods.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_PRICE = 1_000_000
MAX_QUANTITY = 999


class GroupIntent(StrEnum):
    """What the player asked for."""

    PROFILE = "profile"
    SELL = "sell"
    BUY = "buy"
    GIVE_ITEM = "give_item"
    GIVE_GOLD = "give_gold"
    ACCEPT = "accept"
    DECLINE = "decline"


@dataclass(frozen=True, slots=True)
class GroupCommand:
    """A parsed command. ``amount`` is a price, a quantity or an offer number."""

    intent: GroupIntent
    amount: int = 0
    item_query: str = ""


# Written as a player writes it: no slash required, "ё" optional, any spacing.
_VERBS: dict[str, GroupIntent] = {
    "профиль": GroupIntent.PROFILE,
    "продать": GroupIntent.SELL,
    "купить": GroupIntent.BUY,
    "передать": GroupIntent.GIVE_ITEM,
    "принять": GroupIntent.ACCEPT,
    "отказ": GroupIntent.DECLINE,
}
_GOLD_WORDS = frozenset({"золота", "золото", "золотых"})
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, ``ё`` to ``е``, one space between words."""
    return _SPACES.sub(" ", text.replace("ё", "е").replace("Ё", "Е").strip().lower())


def parse_group_command(text: str) -> GroupCommand | None:
    """Parse one group message, or ``None`` if it is not a command for the bot."""
    cleaned = normalise(text).removeprefix("/")
    if not cleaned:
        return None

    verb, _, tail = cleaned.partition(" ")
    intent = _VERBS.get(verb)
    if intent is None:
        return None

    match intent:
        case GroupIntent.PROFILE:
            # "профиль" and nothing else: an argument means the player meant
            # something the bot does not do, and guessing would be worse.
            return GroupCommand(intent=intent) if not tail else None
        case GroupIntent.SELL | GroupIntent.BUY:
            return _parse_offer(intent, tail)
        case GroupIntent.GIVE_ITEM:
            return _parse_give(tail)
        case GroupIntent.ACCEPT | GroupIntent.DECLINE:
            return _parse_number_only(intent, tail)
        case GroupIntent.GIVE_GOLD:
            # No verb maps here: gold is a shape of "передать", not a word.
            return None


def _parse_offer(intent: GroupIntent, tail: str) -> GroupCommand | None:
    """``продать 100 кожаная броня`` - price first, then the item."""
    price_text, _, item = tail.partition(" ")
    price = _as_int(price_text, ceiling=MAX_PRICE)
    if price is None or price <= 0 or not item.strip():
        return None
    return GroupCommand(intent=intent, amount=price, item_query=item.strip())


def _parse_give(tail: str) -> GroupCommand | None:
    """``передать`` takes an item, a counted item, or gold."""
    first, _, rest = tail.partition(" ")
    if not first:
        return None

    # Gold is counted in thousands, items in units, so the first number is read
    # against the wider ceiling and narrowed once it is clear which it was.
    count = _as_int(first, ceiling=MAX_PRICE)
    if count is None:
        # No number at all: the whole tail is the item, one of it.
        return GroupCommand(intent=GroupIntent.GIVE_ITEM, amount=1, item_query=tail.strip())
    if count <= 0:
        return None

    item = rest.strip()
    if not item:
        # A bare number is not gold: "передать 100" could as easily be a slip,
        # and silently moving money on a slip is the one mistake worth avoiding.
        return None
    if item in _GOLD_WORDS:
        return GroupCommand(intent=GroupIntent.GIVE_GOLD, amount=count)
    if count > MAX_QUANTITY:
        return None
    return GroupCommand(intent=GroupIntent.GIVE_ITEM, amount=count, item_query=item)


def _parse_number_only(intent: GroupIntent, tail: str) -> GroupCommand | None:
    number = _as_int(tail.strip(), ceiling=MAX_PRICE)
    if number is None or number <= 0:
        return None
    return GroupCommand(intent=intent, amount=number)


def _as_int(text: str, *, ceiling: int) -> int | None:
    """Digits only: ``100р``, ``1e3`` and ``-5`` are not numbers a player meant."""
    if not text.isdecimal():
        return None
    value = int(text)
    return value if value <= ceiling else None
