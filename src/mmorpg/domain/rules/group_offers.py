"""Offers made in the group: what one player may propose to another, and when it
may actually settle.

The grammar lives in ``group_commands`` and the nouns in ``entities/trade``; this
module is about consequences. Three rules shape everything here:

- **only the target may answer** - an offer names one person, and a stranger
  pressing the button gets a refusal, not the goods (``Narrative.md``, section 9);
- **the author stakes their side up front** - publishing an offer takes the item
  out of the seller's pack, or the gold out of the buyer's purse, and holds it
  until the offer is answered (Roadmap 2.3). An offer is therefore a promise the
  author can no longer break by accident: they cannot spend what they have already
  put on the table;
- **the target stakes nothing until they say yes** - taking gold from someone who
  has not agreed would be theft, so their side is read at the moment they answer,
  and that is the only thing a settlement still has to check.

There is no clock here. ``now`` is a unix timestamp handed in by the caller, which
is what keeps expiry testable without waiting five minutes (``Claude.md``, rule 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.entities.trade import Offer, OfferKind, Party
from mmorpg.domain.rules.group_commands import GroupIntent, normalise

# Five minutes, from Narrative.md: long enough to read the message aloud and
# think, short enough that a forgotten offer cannot be accepted the next day.
OFFER_TTL_SECONDS = 300
MAX_OFFER_NUMBER = 999

# The sweep that returns stakes runs a minute behind expiry on purpose. A player
# who presses "Принять" a moment too late should be told that the offer ran out,
# not that it never existed - and that answer only exists while the row does.
# The cost is one extra minute of escrow on an offer nobody answered at all.
SWEEP_GRACE_SECONDS = 60

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
    TOO_MANY_OFFERS = "too_many_offers"
    PROFILE_HIDDEN = "profile_hidden"
    BLOCKED_BY_TARGET = "blocked_by_target"
    BLOCKED_TARGET = "blocked_target"


def is_expired(offer: Offer, now: int, *, ttl: int = OFFER_TTL_SECONDS) -> bool:
    return now - offer.created_at >= ttl


def answerable_by(offer: Offer, user_id: int) -> bool:
    """Only the target answers. Both sides may walk away, but only one may agree."""
    return user_id == offer.target.user_id


def next_number(previous: int) -> int:
    """Offer numbers are short because a player types them: 1 to 999, then round."""
    return previous % MAX_OFFER_NUMBER + 1


# --- escrow ----------------------------------------------------------


def stakes_item(offer: Offer) -> bool:
    """Whether publishing this offer took an item out of the author's pack."""
    return offer.kind is OfferKind.SELL


def stakes_gold(offer: Offer) -> bool:
    """Whether publishing this offer took gold out of the author's purse."""
    return offer.kind is OfferKind.BUY


# --- checks ----------------------------------------------------------


def check_proposal(
    *,
    kind: OfferKind,
    author: Party,
    target: Party,
    giver_holds: int,
    quantity: int,
    price: int,
    author_gold: int,
) -> Refusal | None:
    """Whether an offer may be published at all.

    The item is checked on whichever side is meant to hand it over, because that
    is also how the item was named. The **author's** gold is checked too, and only
    the author's: a buyer stakes their money the moment they offer it, while
    reading the target's purse would answer a question nobody asked and leak a
    balance to the whole group.
    """
    if author.user_id == target.user_id:
        return Refusal.SELF
    if giver_holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM if kind is OfferKind.SELL else Refusal.TARGET_LACKS_ITEM
    if kind is OfferKind.BUY and author_gold < price:
        return Refusal.AUTHOR_LACKS_GOLD
    return None


def check_settlement(
    offer: Offer, *, target_holds: int, target_gold: int, now: int
) -> Refusal | None:
    """Whether the offer can still be honoured right now.

    Only the target is read: the author's side has been in escrow since the offer
    was published, so the one thing that can still have changed is the purse - or
    the pack - of the person now answering.
    """
    if is_expired(offer, now):
        return Refusal.EXPIRED
    if offer.kind is OfferKind.SELL and target_gold < offer.price:
        return Refusal.TARGET_LACKS_GOLD
    if offer.kind is OfferKind.BUY and target_holds < offer.quantity:
        return Refusal.TARGET_LACKS_ITEM
    return None


def check_gift(*, author: Party, target: Party, holds: int, quantity: int) -> Refusal | None:
    """A hand-over asks nothing in return, so it needs no confirmation - only stock."""
    if author.user_id == target.user_id:
        return Refusal.SELF
    if holds < quantity:
        return Refusal.AUTHOR_LACKS_ITEM
    return None


def check_contact(*, blocked_by_target: bool, blocks_target: bool) -> Refusal | None:
    """Whether these two players deal with each other at all (Roadmap 2.5).

    A block works both ways on purpose. Whoever drew the line, the pair is closed:
    a one-way block would let the blocker keep pushing offers at somebody who has
    already said no, which is the behaviour a black list exists to stop.
    """
    if blocked_by_target:
        return Refusal.BLOCKED_BY_TARGET
    if blocks_target:
        return Refusal.BLOCKED_TARGET
    return None


def check_profile(*, visible: bool) -> Refusal | None:
    """A closed profile is a refusal, not silence: the asker is owed an answer."""
    return None if visible else Refusal.PROFILE_HIDDEN


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
