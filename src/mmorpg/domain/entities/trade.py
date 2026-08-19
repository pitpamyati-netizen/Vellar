"""A trade between two players: who offered what, and what became of it.

These are the nouns of the group economy. The verbs - who may answer, what may
settle - live in ``domain/rules/group_offers.py``; the checks there read these
objects and never change them.

An :class:`Offer` is what one player proposes. A :class:`TradeRecord` is that
offer as the database holds it: the same proposal plus what happened to it. The
record exists because an offer now **holds real value** - the side that proposed
it has already parted with the stake (Roadmap 2.3), so a lost offer would be a
lost item, and losing it in a cache that expires on its own is not acceptable.

Times here are unix seconds, not ``datetime``: the domain has no clock, ``now``
arrives as an argument (``Claude.md``, rule 1), and expiry must mean the same
thing to PostgreSQL and to the in-memory adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OfferKind(StrEnum):
    """Which side parts with the item.

    ``SELL`` - the author offers their own item and the target pays.
    ``BUY``  - the author offers gold for the target's item.
    """

    SELL = "sell"
    BUY = "buy"


class TradeStatus(StrEnum):
    """Where a trade ended up. Only ``PENDING`` holds anything in escrow.

    ``REVERTED`` is a settled trade a keeper undid (``docs/keeper.md``). It is a
    status of its own rather than a return to ``PENDING``: what happened did
    happen, and the journal has to keep saying so.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVERTED = "reverted"


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


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One row of the trade journal.

    ``scope`` is the group the offer was made in, so two groups never collide on
    the short numbers players type. ``tax`` is filled in when the trade settles -
    a trade that never settled cost nobody anything.

    ``id`` is what the journal calls this row and nothing else does: the short
    number players type is reused as soon as an offer closes, so it names a
    standing offer and cannot name a settled one. A keeper undoing a trade is
    pointing at a settled one, which is why the identity exists.
    """

    offer: Offer
    scope: str
    status: TradeStatus = TradeStatus.PENDING
    tax: int = 0
    settled_at: int | None = None
    id: int = 0

    @property
    def number(self) -> int:
        return self.offer.number

    @property
    def is_pending(self) -> bool:
        return self.status is TradeStatus.PENDING

    @property
    def is_settled(self) -> bool:
        """Whether this trade actually moved anything, and so can be undone."""
        return self.status is TradeStatus.ACCEPTED
