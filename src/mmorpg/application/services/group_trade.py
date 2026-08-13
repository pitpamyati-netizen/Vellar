"""What a group command actually does.

The handler parses a message and prints a sentence; everything between those two
things happens here (``Claude.md``, rule 5). This service knows repositories and
domain rules, and nothing about Telegram: it takes account ids and a timestamp,
and returns an outcome that the presentation layer turns into words.

Two shapes of operation live side by side:

- a **hand-over** takes effect immediately - it costs the receiver nothing, so
  asking them to confirm a gift would only add a step (``Narrative.md``, section 9);
- an **offer** costs both sides something, so it is published, numbered, and waits
  for the target to answer within five minutes.

Nothing is held in escrow while an offer stands. Both purses and both packs are
re-read at the moment of settlement, so the worst case of a stale offer is a
refusal, never a debt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, InventoryRepository
from mmorpg.domain.rules.group_commands import GroupCommand, GroupIntent
from mmorpg.domain.rules.group_offers import (
    OFFER_KIND_FOR_INTENT,
    ItemOption,
    Offer,
    OfferKind,
    Party,
    Refusal,
    answerable_by,
    check_gift,
    check_gold_gift,
    check_proposal,
    check_settlement,
    match_items,
)
from mmorpg.domain.rules.stats import DerivedStats, derived_stats

from .offers import OfferStore


class GroupResult(StrEnum):
    """What happened, in the terms the group message will describe."""

    PROFILE = "profile"
    GOLD_GIVEN = "gold_given"
    ITEM_GIVEN = "item_given"
    OFFER_MADE = "offer_made"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class GroupOutcome:
    """The result of one group command, ready to be worded."""

    result: GroupResult
    refusal: Refusal | None = None
    offer: Offer | None = None
    character: Character | None = None
    stats: DerivedStats | None = None
    item_name: str = ""
    quantity: int = 1
    gold: int = 0
    author_name: str = ""
    target_name: str = ""
    # The candidates behind AMBIGUOUS_ITEM, so the answer can list them.
    options: tuple[str, ...] = ()


def _refused(
    refusal: Refusal,
    *,
    offer: Offer | None = None,
    item_name: str = "",
    quantity: int = 1,
    options: tuple[str, ...] = (),
    author_name: str = "",
    target_name: str = "",
) -> GroupOutcome:
    return GroupOutcome(
        result=GroupResult.REFUSED,
        refusal=refusal,
        offer=offer,
        item_name=item_name,
        quantity=quantity,
        options=options,
        author_name=author_name,
        target_name=target_name,
    )


@dataclass(frozen=True, slots=True)
class GroupTrade:
    """Group operations over the repositories. One instance per application."""

    content: GameContent
    characters: CharacterRepository
    inventory: InventoryRepository
    offers: OfferStore

    # --- entry points -------------------------------------------------

    async def run(
        self,
        command: GroupCommand,
        *,
        author_id: int,
        target_id: int | None,
        now: int,
    ) -> GroupOutcome:
        """Perform one parsed command. ``target_id`` is who the author replied to."""
        author_character = await self.characters.get_active(author_id)
        if author_character is None:
            return _refused(Refusal.NO_CHARACTER)
        author = _party(author_id, author_character)

        if command.intent in (GroupIntent.ACCEPT, GroupIntent.DECLINE):
            return await self._answer(
                command.amount,
                accept=command.intent is GroupIntent.ACCEPT,
                answering=author,
                now=now,
            )

        if target_id is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target_character = await self.characters.get_active(target_id)
        if target_character is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target = _party(target_id, target_character)

        match command.intent:
            case GroupIntent.PROFILE:
                return GroupOutcome(
                    result=GroupResult.PROFILE,
                    character=target_character,
                    stats=derived_stats(self.content, target_character),
                    target_name=target.name,
                )
            case GroupIntent.GIVE_GOLD:
                return await self._give_gold(command, author, target)
            case GroupIntent.GIVE_ITEM:
                return await self._give_item(command, author, target)
            # Nothing else reaches here: the answers were handled above and the
            # parser produces no other intent.
            case GroupIntent.SELL | GroupIntent.BUY:
                return await self._propose(command, author, target, now=now)

    # --- hand-overs ---------------------------------------------------

    async def _give_gold(self, command: GroupCommand, author: Party, target: Party) -> GroupOutcome:
        giver = await self._character(author)
        refusal = check_gold_gift(
            author=author, target=target, purse=giver.gold, amount=command.amount
        )
        if refusal is not None:
            return _refused(refusal, author_name=author.name, target_name=target.name)

        receiver = await self._character(target)
        await self.characters.save(giver.with_gold(-command.amount))
        await self.characters.save(receiver.with_gold(command.amount))
        return GroupOutcome(
            result=GroupResult.GOLD_GIVEN,
            gold=command.amount,
            author_name=author.name,
            target_name=target.name,
        )

    async def _give_item(self, command: GroupCommand, author: Party, target: Party) -> GroupOutcome:
        found = await self._resolve(command.item_query, author)
        if isinstance(found, GroupOutcome):
            return replace(found, author_name=author.name, target_name=target.name)

        held = await self.inventory.count(author.character_id, found.item_id)
        refusal = check_gift(author=author, target=target, holds=held, quantity=command.amount)
        if refusal is not None:
            return _refused(
                refusal,
                item_name=found.name,
                quantity=command.amount,
                author_name=author.name,
                target_name=target.name,
            )

        await self._move_item(found.item_id, command.amount, author, target)
        return GroupOutcome(
            result=GroupResult.ITEM_GIVEN,
            item_name=found.name,
            quantity=command.amount,
            author_name=author.name,
            target_name=target.name,
        )

    # --- offers -------------------------------------------------------

    async def _propose(
        self, command: GroupCommand, author: Party, target: Party, *, now: int
    ) -> GroupOutcome:
        kind = OFFER_KIND_FOR_INTENT[command.intent]
        owner = author if kind is OfferKind.SELL else target

        found = await self._resolve(command.item_query, owner)
        if isinstance(found, GroupOutcome):
            return replace(found, author_name=author.name, target_name=target.name)

        held = await self.inventory.count(owner.character_id, found.item_id)
        refusal = check_proposal(
            kind=kind, author=author, target=target, giver_holds=held, quantity=1
        )
        if refusal is not None:
            return _refused(
                refusal,
                item_name=found.name,
                author_name=author.name,
                target_name=target.name,
            )

        offer = Offer(
            number=await self.offers.reserve_number(),
            kind=kind,
            author=author,
            target=target,
            item_id=found.item_id,
            item_name=found.name,
            price=command.amount,
            created_at=now,
        )
        await self.offers.put(offer)
        return GroupOutcome(result=GroupResult.OFFER_MADE, offer=offer)

    async def _answer(
        self, number: int, *, accept: bool, answering: Party, now: int
    ) -> GroupOutcome:
        offer = await self.offers.get(number)
        if offer is None:
            return _refused(Refusal.UNKNOWN_OFFER)

        # Either side may walk away from an offer; only the target may agree to it.
        if not accept and answering.user_id in (offer.target.user_id, offer.author.user_id):
            await self.offers.drop(number)
            return GroupOutcome(result=GroupResult.OFFER_DECLINED, offer=offer)
        if not answerable_by(offer, answering.user_id):
            return _refused(Refusal.NOT_YOURS, offer=offer)

        giver = await self._character(offer.giver)
        payer = await self._character(offer.payer)
        refusal = check_settlement(
            offer,
            giver_holds=await self.inventory.count(offer.giver.character_id, offer.item_id),
            payer_gold=payer.gold,
            now=now,
        )
        if refusal is not None:
            await self.offers.drop(number)
            return _refused(refusal, offer=offer)

        # The item goes to whoever paid for it, whichever way the offer was worded.
        await self._move_item(offer.item_id, offer.quantity, offer.giver, offer.payer)
        await self.characters.save(payer.with_gold(-offer.price))
        await self.characters.save(giver.with_gold(offer.price))
        await self.offers.drop(number)
        return GroupOutcome(result=GroupResult.OFFER_ACCEPTED, offer=offer)

    # --- shared steps -------------------------------------------------

    async def _resolve(self, query: str, owner: Party) -> ItemOption | GroupOutcome:
        """Turn what the player typed into one item of the owner's pack."""
        entries = await self.inventory.list_items(owner.character_id)
        catalogue = [
            ItemOption(item_id=entry.item_id, name=self.content.item(entry.item_id).name)
            for entry in entries
        ]
        found = match_items(query, catalogue)
        if not found:
            return _refused(Refusal.UNKNOWN_ITEM, item_name=query)
        if len(found) > 1:
            return _refused(
                Refusal.AMBIGUOUS_ITEM,
                item_name=query,
                options=tuple(option.name for option in found),
            )
        return found[0]

    async def _move_item(self, item_id: str, quantity: int, giver: Party, taker: Party) -> None:
        """Take first, then give: a failed take must not conjure the item."""
        if await self.inventory.remove(giver.character_id, item_id, quantity):
            await self.inventory.add(taker.character_id, item_id, quantity)

    async def _character(self, party: Party) -> Character:
        character = await self.characters.get(party.character_id)
        if character is None:  # pragma: no cover - the party was built from a character
            msg = f"character {party.character_id} disappeared mid-trade"
            raise LookupError(msg)
        return character


def _party(user_id: int, character: Character) -> Party:
    return Party(user_id=user_id, character_id=character.id, name=character.name)
