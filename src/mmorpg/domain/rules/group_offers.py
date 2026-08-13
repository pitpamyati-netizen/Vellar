"""Offers made in the group: what one player may propose to another, and when it
may actually settle.

The grammar lives in ``group_commands``; this module is about consequences. Two
rules shape everything here:

- **only the target may answer** - an offer names one person, and a stranger
  pressing the button gets a refusal, not the goods (``Narrative.md``, section 9);
- **an offer is a promise, not a hold** - nothing is moved when it is made, so both
  sides are re-checked at the moment it settles. A player who spent their gold
  while the offer stood simply cannot accept it.

There is no clock here. ``now`` is a unix timestamp handed in by the caller, which
is what keeps expiry testable without waiting five minutes (``Claude.md``, rule 1).

Escrow, the trade tax and the persisted journal are Roadmap 2.3; this module
knows about neither, and deliberately settles from live balances instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.rules.group_commands import GroupIntent, normalise

# Five minutes, from Narrative.md: long enough to read the message aloud and
# think, short enough that a forgotten offer cannot be accepted the next day.
OFFER_TTL_SECONDS = 300
MAX_OFFER_NUMBER = 999


class OfferKind(StrEnum):
    """Which side parts with the item.

    ``SELL`` - the author offers their own item and the target pays.
    ``BUY``  - the author offers gold for the target's item.
    """

    SELL = "sell"
    BUY = "buy"


OFFER_KIND_FOR_INTENT: dict[GroupIntent, OfferKind] = {
    GroupIntent.SELL: OfferKind.SELL,
    GroupIntent.BUY: OfferKind.BUY,
}


class Refusal(StrEnum):
    """Why the bot said no. The wording lives in the presentation layer."""

    SELF = "self"
    NO_CHARACTER = "no_character"
    TARGET_HAS_NO_CHARACTER = "target_has_no_character"
    UNKNOWN_ITEM = "unknown_item"
    AMBIGUOUS_ITEM = "ambiguous_item"
    AUTHOR_LACKS_ITEM = "author_lacks_item"
    TARGET_LACKS_ITEM = "target_lacks_item"
    AUTHOR_LACKS_GOLD = "author_lacks_gold"
    TARGET_LACKS_GOLD = "target_lacks_gold"
    NOT_YOURS = "not_yours"
    UNKNOWN_OFFER = "unknown_offer"
    EXPIRED = "expired"
    TOO_MANY_COMMANDS = "too_many_commands"


@dataclass(frozen=True, slots=True)
class Party:
    """One side of an offer: the Telegram account and the character behind it."""

    user_id: int
    character_id: int
    name: str


@dataclass(frozen=True, slots=True)
class Offer:
    """A published proposal, waiting for exactly one person to answer it."""

    number: int
    kind: OfferKind
    author: Party
    target: Party
    item_id: str
    item_name: str
    price: int
    quantity: int = 1
    created_at: int = 0

    @property
    def giver(self) -> Party:
        """The side that parts with the item."""
        return self.author if self.kind is OfferKind.SELL else self.target

    @property
    def payer(self) -> Party:
        """The side that parts with the gold."""
        return self.target if self.kind is OfferKind.SELL else self.author


def is_expired(offer: Offer, now: int, *, ttl: int = OFFER_TTL_SECONDS) -> bool:
    return now - offer.created_at >= ttl


def answerable_by(offer: Offer, user_id: int) -> bool:
    """Only the target answers. Both sides may walk away, but only one may agree."""
    return user_id == offer.target.user_id


def next_number(previous: int) -> int:
    """Offer numbers are short because a player types them: 1 to 999, then round."""
    return previous % MAX_OFFER_NUMBER + 1


# --- checks ----------------------------------------------------------


def check_proposal(
    *,
    kind: OfferKind,
    author: Party,
    target: Party,
    giver_holds: int,
    quantity: int,
) -> Refusal | None:
    """Whether an offer may be published at all.

    Gold is deliberately **not** checked here: for a sale that would mean reading
    the target's purse to answer someone else's message, and an offer they cannot
    afford yet is still a fair offer - they have five minutes to find the money.
    """
    if author.user_id == target.user_id:
        return Refusal.SELF
    if giver_holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM if kind is OfferKind.SELL else Refusal.TARGET_LACKS_ITEM
    return None


def check_settlement(
    offer: Offer, *, giver_holds: int, payer_gold: int, now: int
) -> Refusal | None:
    """Whether the offer can still be honoured right now.

    Both sides are read fresh: between the offer and the answer either of them
    could have spent the gold or sold the item elsewhere.
    """
    if is_expired(offer, now):
        return Refusal.EXPIRED
    if giver_holds < offer.quantity:
        return (
            Refusal.AUTHOR_LACKS_ITEM if offer.giver == offer.author else Refusal.TARGET_LACKS_ITEM
        )
    if payer_gold < offer.price:
        return (
            Refusal.AUTHOR_LACKS_GOLD if offer.payer == offer.author else Refusal.TARGET_LACKS_GOLD
        )
    return None


def check_gift(*, author: Party, target: Party, holds: int, quantity: int) -> Refusal | None:
    """A hand-over asks nothing in return, so it needs no confirmation - only stock."""
    if author.user_id == target.user_id:
        return Refusal.SELF
    if holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM
    return None


def check_gold_gift(*, author: Party, target: Party, purse: int, amount: int) -> Refusal | None:
    if author.user_id == target.user_id:
        return Refusal.SELF
    if purse < amount:
        return Refusal.AUTHOR_LACKS_GOLD
    return None


# --- naming goods the way a player names them ------------------------


@dataclass(frozen=True, slots=True)
class ItemOption:
    """One candidate the player might have meant."""

    item_id: str
    name: str


def match_items(query: str, catalogue: Sequence[ItemOption]) -> tuple[ItemOption, ...]:
    """Resolve a typed item name against what someone actually holds.

    Players do not type identifiers, they type "кожаная броня" and sometimes just
    "броня". So matching walks from strict to loose and stops at the first tier
    that finds anything: an exact name beats a prefix, a prefix beats a substring.
    Returning several candidates is not a failure - it means "say which one", and
    guessing between them would move the wrong goods.
    """
    wanted = normalise(query)
    if not wanted:
        return ()

    exact = [option for option in catalogue if normalise(option.name) == wanted]
    if exact:
        return tuple(exact)

    # An identifier still works, for anyone who reads the content files.
    by_id = [option for option in catalogue if option.item_id.casefold() == wanted]
    if by_id:
        return tuple(by_id)

    starts = [option for option in catalogue if normalise(option.name).startswith(wanted)]
    if starts:
        return tuple(starts)

    return tuple(option for option in catalogue if wanted in normalise(option.name))
