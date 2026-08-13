"""Where a pending offer lives for the five minutes it is worth anything.

An offer is short-lived by definition, so it never reaches PostgreSQL: it goes
into the state cache under a key that expires on its own (``Claude.md``, rule 7).
If the process restarts and the cache is gone, every offer is simply gone too -
which is the correct outcome, because nothing had been moved yet.

Numbers are short because a player types them out loud into a keyboard: the
counter is kept beside the offers and wraps at 999. A collision would need two
offers made in the same second after a wrap, and the reservation loop steps over
any number that is somehow still taken.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mmorpg.domain.ports.repositories import StateCache
from mmorpg.domain.rules.group_offers import (
    MAX_OFFER_NUMBER,
    OFFER_TTL_SECONDS,
    Offer,
    OfferKind,
    Party,
    next_number,
)

COUNTER_TTL_SECONDS = 86_400


def _encode(offer: Offer) -> str:
    return json.dumps(
        {
            "number": offer.number,
            "kind": offer.kind.value,
            "author": [offer.author.user_id, offer.author.character_id, offer.author.name],
            "target": [offer.target.user_id, offer.target.character_id, offer.target.name],
            "item_id": offer.item_id,
            "item_name": offer.item_name,
            "price": offer.price,
            "quantity": offer.quantity,
            "created_at": offer.created_at,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _party(raw: Any) -> Party:
    user_id, character_id, name = raw
    return Party(user_id=int(user_id), character_id=int(character_id), name=str(name))


def _decode(raw: str) -> Offer | None:
    """Rebuild an offer, or ``None`` if the payload is not one.

    A cache is shared infrastructure and its contents outlive deployments, so a
    value written by an older version is treated as absent rather than as a crash.
    """
    try:
        data = json.loads(raw)
        return Offer(
            number=int(data["number"]),
            kind=OfferKind(data["kind"]),
            author=_party(data["author"]),
            target=_party(data["target"]),
            item_id=str(data["item_id"]),
            item_name=str(data["item_name"]),
            price=int(data["price"]),
            quantity=int(data["quantity"]),
            created_at=int(data["created_at"]),
        )
    except ValueError, TypeError, KeyError, IndexError:
        return None


@dataclass(frozen=True, slots=True)
class OfferStore:
    """Pending offers of one group, keyed by the number players see."""

    cache: StateCache
    scope: str = "group"
    ttl: int = OFFER_TTL_SECONDS

    def _key(self, number: int) -> str:
        return f"offer:{self.scope}:{number}"

    @property
    def _counter_key(self) -> str:
        return f"offer:{self.scope}:counter"

    async def get(self, number: int) -> Offer | None:
        raw = await self.cache.get(self._key(number))
        return _decode(raw) if raw is not None else None

    async def put(self, offer: Offer) -> None:
        await self.cache.set(self._key(offer.number), _encode(offer), self.ttl)

    async def drop(self, number: int) -> None:
        await self.cache.delete(self._key(number))

    async def reserve_number(self) -> int:
        """The next free number. Taken ones are stepped over, not overwritten."""
        raw = await self.cache.get(self._counter_key)
        current = int(raw) if raw is not None and raw.isdecimal() else 0

        for _ in range(MAX_OFFER_NUMBER):
            current = next_number(current)
            if await self.cache.get(self._key(current)) is None:
                break
        await self.cache.set(self._counter_key, str(current), COUNTER_TTL_SECONDS)
        return current
