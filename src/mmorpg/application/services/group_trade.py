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

An offer holds the author's side in escrow. Publishing a sale takes the item out
of the seller's pack; publishing a purchase takes the gold out of the buyer's
purse. Both come back the moment the offer is declined or runs out of time. The
target stakes nothing until they answer, because nobody has asked them yet.

Three things follow from that, and they are the whole point of Roadmap 2.3:

- an offer cannot fail because the *author* spent their side meanwhile - they no
  longer have it to spend;
- the escrow outlives a restart, because it lives in the trade journal in
  PostgreSQL rather than in a cache that expires on its own;
- every settled trade pays a duty, which is the one place gold leaves the game
  (``domain/rules/economy.trade_tax``).

What the target answers *with*, though, has to be there in the instant they
answer, and it is checked one step earlier. So every purse in this module moves
by ``spend_gold``/``grant_gold`` - one conditional step in the database - and
never by writing back a character read a few awaits ago. Checking a purse and
then writing a purse are two different moments, and a player who is fighting in
their private chat while their group offer settles lives in between them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg import economy_log
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.trade import (
    Offer,
    OfferKind,
    Party,
    TradeRecord,
    TradeStatus,
)
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    PrivacyRepository,
    TradeRepository,
)
from mmorpg.domain.rules.economy import payout, refund, trade_tax
from mmorpg.domain.rules.group_commands import GroupCommand, GroupIntent
from mmorpg.domain.rules.group_offers import (
    OFFER_KIND_FOR_INTENT,
    ItemOption,
    Refusal,
    answerable_by,
    check_contact,
    check_gift,
    check_gold_gift,
    check_profile,
    check_proposal,
    check_settlement,
    match_items,
    stakes_gold,
    stakes_item,
    sweep_before,
)
from mmorpg.domain.rules.stats import DerivedStats, derived_stats


async def return_stake(
    offer: Offer, *, characters: CharacterRepository, inventory: InventoryRepository
) -> None:
    """Give the author back what publishing the offer took from them.

    A free function rather than a method, because a stake is returned in two very
    different situations: by the group service while the game runs, and by
    :func:`release_expired_offers` when it starts. Both move the same two things.
    """
    if stakes_item(offer):
        await inventory.add(offer.author.character_id, offer.item_id, offer.quantity)
        return
    await characters.grant_gold(offer.author.character_id, offer.price)


async def release_expired_offers(
    *,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
    now: int,
) -> int:
    """Roll back every offer that ran out and was never answered. Returns how many.

    The sweep before each group command (``GroupTrade._sweep``) is the usual way
    this happens, and it is enough while somebody is talking. It is not enough on
    its own: an offer published into a group that then went quiet - or into a
    group the bot was removed from - would hold its author's item until that group
    spoke again, which it may never do. So the game also sweeps once at startup,
    and the group sweep no longer looks at one scope only.

    This is not a timer (``Claude.md``, rule 3): nothing is scheduled and nothing
    wakes up. It runs once, where the game already stops to build itself.
    """
    stale = await trades.expire(before=sweep_before(now))
    for record in stale:
        await return_stake(record.offer, characters=characters, inventory=inventory)
    return len(stale)


@dataclass(frozen=True, slots=True)
class Rollback:
    """Чем кончился откат сделки. Числами, а фразу составит экран."""

    #: Сделка, какой она была до отката. ``None`` — откатывать было нечего.
    record: TradeRecord | None = None
    #: Вернулась ли вещь тому, кто её отдал.
    item_returned: bool = False
    #: Сколько золота вернулось плательщику.
    gold_returned: int = 0
    #: Сколько золота вернуть не удалось: у получателя его уже нет.
    gold_missing: int = 0

    @property
    def done(self) -> bool:
        return self.record is not None

    @property
    def whole(self) -> bool:
        """Вернулось ли всё, что двигала сделка."""
        return self.done and self.item_returned and not self.gold_missing


async def roll_back(
    trade_id: int,
    *,
    trades: TradeRepository,
    characters: CharacterRepository,
    inventory: InventoryRepository,
) -> Rollback:
    """Отменить расчёт, который уже прошёл. Работа смотрителя (``docs/keeper.md``).

    Строку журнала переводит в «откачено» само хранилище и только один раз
    (``TradeRepository.revert``), поэтому два смотрителя, нажавшие вместе,
    двигают вещи один раз.

    Вещь и золото возвращаются порознь, и это решение, а не недосмотр. Откат
    нужен там, где сделку признали обманом, а обманувший к этому времени успел и
    вещь надеть, и золото потратить. Требовать, чтобы вернулось всё или ничего,
    значило бы, что чаще всего не возвращается ничего. Поэтому возвращается то,
    что есть, а чего нет — названо числом, и остаток смотритель выдаёт руками.

    Плательщику приходит ровно то, что получил продавец: пошлина ушла из игры в
    момент расчёта, и вернуть её означало бы её напечатать
    (``domain/rules/economy.refund``).
    """
    record = await trades.revert(trade_id)
    if record is None:
        return Rollback()

    offer = record.offer
    # Вещь у того, кто за неё платил, — в обе стороны: и в продаже, и в скупке.
    returned = await inventory.remove(offer.payer.character_id, offer.item_id, offer.quantity)
    if returned:
        await inventory.add(offer.giver.character_id, offer.item_id, offer.quantity)

    owed = refund(offer.price, record.tax)
    paid_back = bool(owed) and await characters.spend_gold(offer.giver.character_id, owed)
    if paid_back:
        await characters.grant_gold(offer.payer.character_id, owed)
        economy_log.record(
            economy_log.TRADE_ROLLBACK,
            -owed,
            character_id=offer.giver.character_id,
            detail=f"trade {trade_id}",
        )
        economy_log.record(
            economy_log.TRADE_ROLLBACK,
            owed,
            character_id=offer.payer.character_id,
            detail=f"trade {trade_id}",
        )
    return Rollback(
        record=record,
        item_returned=returned,
        gold_returned=owed if paid_back else 0,
        gold_missing=0 if paid_back else owed,
    )


class GroupResult(StrEnum):
    """What happened, in the terms the group message will describe."""

    PROFILE = "profile"
    PROFILE_CLOSED = "profile_closed"
    PROFILE_OPENED = "profile_opened"
    BLOCK_ADDED = "block_added"
    BLOCK_REMOVED = "block_removed"
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
    # The duty on this trade: charged on settlement, quoted when the offer is made.
    tax: int = 0
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
    trades: TradeRepository
    privacy: PrivacyRepository
    # Offers are numbered per group, so two groups never fight over "принять 7".
    scope: str = "group"

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
        await self._sweep(now)

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
        if command.intent in (GroupIntent.HIDE_PROFILE, GroupIntent.SHOW_PROFILE):
            return await self._set_visible(
                author, visible=command.intent is GroupIntent.SHOW_PROFILE
            )

        if target_id is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target_character = await self.characters.get_active(target_id)
        if target_character is None:
            return _refused(Refusal.TARGET_HAS_NO_CHARACTER)
        target = _party(target_id, target_character)

        # Drawing the line is always allowed, in either direction: a black list
        # somebody else's block could freeze would be a trap, not a list.
        if command.intent in (GroupIntent.BLOCK, GroupIntent.UNBLOCK):
            return await self._list_entry(
                author, target, adding=command.intent is GroupIntent.BLOCK, now=now
            )

        wall = check_contact(
            blocked_by_target=await self.privacy.blocks(target.user_id, author.user_id),
            blocks_target=await self.privacy.blocks(author.user_id, target.user_id),
        )
        if wall is not None:
            return _refused(wall, author_name=author.name, target_name=target.name)

        match command.intent:
            case GroupIntent.PROFILE:
                # A player always sees their own card, closed or not.
                if author.user_id != target.user_id:
                    hidden = check_profile(
                        visible=await self.privacy.profile_visible(target.user_id)
                    )
                    if hidden is not None:
                        return _refused(hidden, target_name=target.name)
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
            # Nothing else reaches here: answers, privacy and the black list were
            # handled above, and the parser produces no other intent.
            case _:
                return await self._propose(command, author, target, now=now)

    # --- privacy ------------------------------------------------------

    async def _set_visible(self, author: Party, *, visible: bool) -> GroupOutcome:
        """Open or close the profile card. Saying it twice is not an error."""
        await self.privacy.set_profile_visible(author.user_id, visible)
        return GroupOutcome(
            result=GroupResult.PROFILE_OPENED if visible else GroupResult.PROFILE_CLOSED,
            author_name=author.name,
        )

    async def _list_entry(
        self, author: Party, target: Party, *, adding: bool, now: int
    ) -> GroupOutcome:
        """Add somebody to the black list, or take them off it."""
        if author.user_id == target.user_id:
            return _refused(Refusal.SELF, author_name=author.name, target_name=target.name)
        if adding:
            await self.privacy.block(author.user_id, target.user_id, at=now)
        else:
            await self.privacy.unblock(author.user_id, target.user_id)
        # The answer states the state, not the change, so a repeated command reads
        # the same as the first one - which is what a player who lost the reply needs.
        return GroupOutcome(
            result=GroupResult.BLOCK_ADDED if adding else GroupResult.BLOCK_REMOVED,
            author_name=author.name,
            target_name=target.name,
        )

    # --- hand-overs ---------------------------------------------------

    async def _give_gold(self, command: GroupCommand, author: Party, target: Party) -> GroupOutcome:
        giver = await self._character(author)
        refusal = check_gold_gift(
            author=author, target=target, purse=giver.gold, amount=command.amount
        )
        if refusal is not None:
            return _refused(refusal, author_name=author.name, target_name=target.name)

        # Both sides move as increments, not as whole characters written back: a
        # purse read a moment ago is already out of date if its owner is playing.
        if not await self.characters.spend_gold(author.character_id, command.amount):
            return _refused(
                Refusal.AUTHOR_LACKS_GOLD, author_name=author.name, target_name=target.name
            )
        await self.characters.grant_gold(target.character_id, command.amount)
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

        purse = await self._character(author)
        refusal = check_proposal(
            kind=kind,
            author=author,
            target=target,
            giver_holds=await self.inventory.count(owner.character_id, found.item_id),
            quantity=1,
            price=command.amount,
            author_gold=purse.gold,
        )
        if refusal is not None:
            return _refused(
                refusal,
                item_name=found.name,
                author_name=author.name,
                target_name=target.name,
            )

        record = await self.trades.open(
            Offer(
                # The repository assigns the number a player will type; until it
                # does, this offer has none.
                number=0,
                kind=kind,
                author=author,
                target=target,
                item_id=found.item_id,
                item_name=found.name,
                price=command.amount,
                created_at=now,
            ),
            scope=self.scope,
        )
        if record is None:
            return _refused(
                Refusal.TOO_MANY_OFFERS, author_name=author.name, target_name=target.name
            )

        # The row exists before the stake moves, so a stake that moved is always
        # recorded somewhere. If it cannot move, the row closes again immediately.
        if not await self._take_stake(record.offer):
            await self.trades.close(
                record.number, scope=self.scope, status=TradeStatus.DECLINED, settled_at=now
            )
            lacking = (
                Refusal.AUTHOR_LACKS_ITEM
                if stakes_item(record.offer)
                else Refusal.AUTHOR_LACKS_GOLD
            )
            return _refused(
                lacking, item_name=found.name, author_name=author.name, target_name=target.name
            )

        return GroupOutcome(
            result=GroupResult.OFFER_MADE,
            offer=record.offer,
            tax=trade_tax(record.offer.price),
        )

    async def _answer(
        self, number: int, *, accept: bool, answering: Party, now: int
    ) -> GroupOutcome:
        record = await self.trades.pending(number, scope=self.scope)
        if record is None:
            return _refused(Refusal.UNKNOWN_OFFER)
        offer = record.offer

        # Either side may walk away from an offer; only the target may agree to it.
        if not accept and answering.user_id in (offer.target.user_id, offer.author.user_id):
            closed = await self._close(number, TradeStatus.DECLINED, now=now)
            if closed is None:
                return _refused(Refusal.UNKNOWN_OFFER)
            return GroupOutcome(result=GroupResult.OFFER_DECLINED, offer=offer)
        if not answerable_by(offer, answering.user_id):
            return _refused(Refusal.NOT_YOURS, offer=offer)

        # A block drawn while the offer stood cancels it: the stake goes back to
        # its author, and neither side is left waiting on business they refused.
        wall = check_contact(
            blocked_by_target=await self.privacy.blocks(offer.author.user_id, answering.user_id),
            blocks_target=await self.privacy.blocks(answering.user_id, offer.author.user_id),
        )
        if wall is not None:
            await self._close(number, TradeStatus.DECLINED, now=now)
            return _refused(wall, offer=offer)

        target = await self._character(offer.target)
        refusal = check_settlement(
            offer,
            target_holds=await self.inventory.count(offer.target.character_id, offer.item_id),
            target_gold=target.gold,
            now=now,
        )
        if refusal is not None:
            status = TradeStatus.EXPIRED if refusal is Refusal.EXPIRED else TradeStatus.DECLINED
            await self._close(number, status, now=now)
            return _refused(refusal, offer=offer)

        return await self._settle(record, now=now)

    async def _settle(self, record: TradeRecord, *, now: int) -> GroupOutcome:
        """Move everything, once. The trade row decides who gets to do it."""
        offer = record.offer
        tax = trade_tax(offer.price)

        # A purchase takes the target's item first, because that is the only side
        # whose removal is atomic: if it is gone in this very instant, nothing has
        # happened yet and the offer simply refuses.
        took_item = stakes_gold(offer) and await self.inventory.remove(
            offer.target.character_id, offer.item_id, offer.quantity
        )
        if stakes_gold(offer) and not took_item:
            await self._close(offer.number, TradeStatus.DECLINED, now=now)
            return _refused(Refusal.TARGET_LACKS_ITEM, offer=offer)

        # The target's side is taken before the row closes, and taken atomically:
        # a buyer answering a sale pays out of a purse they may be spending in
        # this very instant, and ``check_settlement`` only read it (Roadmap,
        # "Риски"). A buyer who *proposed* the purchase paid at publication, and
        # their gold is already in escrow.
        paid = stakes_item(offer) and await self.characters.spend_gold(
            offer.payer.character_id, offer.price
        )
        if stakes_item(offer) and not paid:
            await self._close(offer.number, TradeStatus.DECLINED, now=now)
            if took_item:  # pragma: no cover - a sale never takes an item first
                await self.inventory.add(offer.target.character_id, offer.item_id, offer.quantity)
            return _refused(Refusal.TARGET_LACKS_GOLD, offer=offer)

        closed = await self._close(offer.number, TradeStatus.ACCEPTED, now=now, tax=tax)
        if closed is None:
            # Somebody answered first. Undo whatever was already done.
            if took_item:
                await self.inventory.add(offer.target.character_id, offer.item_id, offer.quantity)
            if paid:
                await self.characters.grant_gold(offer.payer.character_id, offer.price)
            return _refused(Refusal.UNKNOWN_OFFER)

        # The item goes to whoever paid for it, whichever way the offer was worded:
        # out of escrow for a sale, straight from the target for a purchase.
        await self.inventory.add(offer.payer.character_id, offer.item_id, offer.quantity)

        # The seller is paid out of escrow or out of the buyer's purse; either way
        # the duty is simply never credited to anyone. Both halves are written
        # down, because "is five per cent the right number" is a question about a
        # day of real trading and not about arithmetic (``mmorpg.economy_log``).
        await self.characters.grant_gold(offer.giver.character_id, payout(offer.price))
        economy_log.record(
            economy_log.TRADE_PRICE,
            offer.price,
            character_id=offer.giver.character_id,
            detail=f"payer {offer.payer.character_id}",
        )
        economy_log.record(economy_log.TRADE_DUTY, -tax, character_id=offer.giver.character_id)
        return GroupOutcome(result=GroupResult.OFFER_ACCEPTED, offer=offer, tax=tax)

    # --- escrow -------------------------------------------------------

    async def _take_stake(self, offer: Offer) -> bool:
        """Hold the author's side of a published offer. False if it is not there."""
        if stakes_item(offer):
            return await self.inventory.remove(
                offer.author.character_id, offer.item_id, offer.quantity
            )
        return await self.characters.spend_gold(offer.author.character_id, offer.price)

    async def _return_stake(self, offer: Offer) -> None:
        """Give the author back what publishing the offer took from them."""
        await return_stake(offer, characters=self.characters, inventory=self.inventory)

    async def _close(
        self, number: int, status: TradeStatus, *, now: int, tax: int = 0
    ) -> TradeRecord | None:
        """Close a pending trade and, unless it settled, hand the stake back.

        Only the caller that actually closed the row returns the stake, so two
        answers arriving together cannot refund the same item twice.
        """
        closed = await self.trades.close(
            number, scope=self.scope, status=status, settled_at=now, tax=tax
        )
        if closed is not None and status is not TradeStatus.ACCEPTED:
            await self._return_stake(closed.offer)
        return closed

    async def _sweep(self, now: int) -> None:
        """Return the stakes of every offer nobody answered in time, anywhere.

        Run before each command rather than on a timer: the group is the only
        place offers are made, so anything that expired is discovered the next
        time somebody speaks, and an idle group needs no background work at all.

        Deliberately not limited to ``self.scope``. Whoever speaks next sweeps for
        everyone, so one busy group frees the stakes left behind in a quiet one;
        what a scope that never speaks again would otherwise hold is picked up by
        :func:`release_expired_offers` the next time the game starts.

        The grace period is what keeps "просрочено" a real answer instead of
        "не найдено" - see ``SWEEP_GRACE_SECONDS``.
        """
        for record in await self.trades.expire(before=sweep_before(now)):
            await self._return_stake(record.offer)

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
