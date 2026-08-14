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
    блок
    разблок
    принять <номер>
    отказ    <номер>

Two commands speak about the sender rather than about the person they answer, so
they need no reply at all - they are the privacy switch (Roadmap 2.5):

    скрыть профиль
    открыть профиль

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
    BLOCK = "block"
    UNBLOCK = "unblock"
    HIDE_PROFILE = "hide_profile"
    SHOW_PROFILE = "show_profile"


# Commands about the sender, not about whoever they replied to. They are the only
# ones that mean something shouted into the room (``Narrative.md``, section 9).
UNADDRESSED = frozenset(
    {GroupIntent.ACCEPT, GroupIntent.DECLINE, GroupIntent.HIDE_PROFILE, GroupIntent.SHOW_PROFILE}
)


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
    "блок": GroupIntent.BLOCK,
    "разблок": GroupIntent.UNBLOCK,
}
# Said in two words, and only in these two words: a phrase is matched whole, so
# "скрыть" alone stays somebody's sentence rather than becoming a command.
_PHRASES: dict[str, GroupIntent] = {
    "скрыть профиль": GroupIntent.HIDE_PROFILE,
    "открыть профиль": GroupIntent.SHOW_PROFILE,
    "снять блок": GroupIntent.UNBLOCK,
}
# Commands that take nothing after the verb.
_BARE = frozenset({GroupIntent.PROFILE, GroupIntent.BLOCK, GroupIntent.UNBLOCK})
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

    phrase = _PHRASES.get(cleaned)
    if phrase is not None:
        return GroupCommand(intent=phrase)

    verb, _, tail = cleaned.partition(" ")
    intent = _VERBS.get(verb)
    if intent is None:
        return None
    if intent in _BARE:
        # "профиль" and nothing else: an argument means the player meant
        # something the bot does not do, and guessing would be worse.
        return GroupCommand(intent=intent) if not tail else None

    match intent:
        case GroupIntent.SELL | GroupIntent.BUY:
            return _parse_offer(intent, tail)
        case GroupIntent.GIVE_ITEM:
            return _parse_give(tail)
        case GroupIntent.ACCEPT | GroupIntent.DECLINE:
            return _parse_number_only(intent, tail)
        case _:
            # Nothing else comes from a verb: gold is a shape of "передать", and
            # the privacy switch is a phrase.
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
