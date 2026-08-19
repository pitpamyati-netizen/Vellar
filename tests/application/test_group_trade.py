"""Trades between players, end to end through the repositories.

Three properties are worth more than the rest: nothing is ever created by a
trade, everything an offer holds comes back to its author when the offer dies,
and the duty is the only way gold leaves the game.
"""

from __future__ import annotations

import pytest

from mmorpg.application.services.group_trade import (
    GroupResult,
    GroupTrade,
    release_expired_offers,
    roll_back,
)
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.trade import OfferKind, TradeStatus
from mmorpg.domain.rules.economy import trade_tax
from mmorpg.domain.rules.group_commands import parse_group_command
from mmorpg.domain.rules.group_offers import (
    OFFER_TTL_SECONDS,
    SWEEP_GRACE_SECONDS,
    Refusal,
)
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
    InMemoryPrivacyRepository,
    InMemoryTradeRepository,
)

ARGUS_ACCOUNT = 1
MERLA_ACCOUNT = 2
STRANGER_ACCOUNT = 3
NOW = 10_000
SWORD = "rusty_sword"
SWORD_NAME = "Ржавый меч"


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def inventory() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


@pytest.fixture
def trades() -> InMemoryTradeRepository:
    return InMemoryTradeRepository()


@pytest.fixture
def privacy() -> InMemoryPrivacyRepository:
    return InMemoryPrivacyRepository()


@pytest.fixture
def trade(
    content: GameContent,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    trades: InMemoryTradeRepository,
    privacy: InMemoryPrivacyRepository,
) -> GroupTrade:
    return GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        trades=trades,
        privacy=privacy,
    )


async def make(
    characters: InMemoryCharacterRepository,
    account: int,
    name: str,
    *,
    gold: int = 0,
) -> Character:
    return await characters.create(
        Character(
            id=0,
            user_id=account,
            name=name,
            race_id="human",
            class_id="warrior",
            gold=gold,
        )
    )


@pytest.fixture
async def argus(characters: InMemoryCharacterRepository) -> Character:
    return await make(characters, ARGUS_ACCOUNT, "Аргус", gold=500)


@pytest.fixture
async def merla(characters: InMemoryCharacterRepository) -> Character:
    return await make(characters, MERLA_ACCOUNT, "Мерла", gold=300)


async def run(trade: GroupTrade, text: str, *, author: int, target: int | None, now: int = NOW):
    command = parse_group_command(text)
    assert command is not None, text
    return await trade.run(command, author_id=author, target_id=target, now=now)


async def purse(characters: InMemoryCharacterRepository, character: Character) -> int:
    read = await characters.get(character.id)
    assert read is not None
    return read.gold


# --- profile ----------------------------------------------------------


async def test_a_profile_is_the_character_of_whoever_was_replied_to(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.result is GroupResult.PROFILE
    assert outcome.character is not None and outcome.character.name == "Мерла"
    assert outcome.stats is not None


async def test_a_player_without_a_character_gets_told_so(
    trade: GroupTrade, merla: Character
) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.NO_CHARACTER


async def test_replying_to_someone_who_never_played(trade: GroupTrade, argus: Character) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=STRANGER_ACCOUNT)

    assert outcome.refusal is Refusal.TARGET_HAS_NO_CHARACTER


# --- privacy ----------------------------------------------------------


async def test_a_closed_profile_is_refused_rather_than_shown(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    await run(trade, "скрыть профиль", author=MERLA_ACCOUNT, target=None)

    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.PROFILE_HIDDEN


async def test_closing_a_profile_hides_nothing_else(
    trade: GroupTrade, inventory: InMemoryInventoryRepository, argus: Character, merla: Character
) -> None:
    """Privacy is about the card, not about trading: business goes on."""
    await run(trade, "скрыть профиль", author=MERLA_ACCOUNT, target=None)
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(trade, "передать ржавый меч", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.result is GroupResult.ITEM_GIVEN


async def test_a_block_stops_business_from_both_sides(
    trade: GroupTrade, inventory: InMemoryInventoryRepository, argus: Character, merla: Character
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    blocked = await run(trade, "блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    from_them = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )
    from_us = await run(trade, "профиль", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    assert blocked.result is GroupResult.BLOCK_ADDED
    assert from_them.refusal is Refusal.BLOCKED_BY_TARGET
    assert from_us.refusal is Refusal.BLOCKED_TARGET
    # The refused sale never took the sword: an offer that cannot exist holds nothing.
    assert await inventory.count(argus.id, SWORD) == 1


async def test_blocking_twice_says_the_same_thing(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    """A player who lost the answer retypes the command; it must not surprise them."""
    first = await run(trade, "блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)
    again = await run(trade, "блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    assert first.result is again.result is GroupResult.BLOCK_ADDED


async def test_a_block_can_be_lifted(trade: GroupTrade, argus: Character, merla: Character) -> None:
    await run(trade, "блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    lifted = await run(trade, "снять блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)
    outcome = await run(trade, "профиль", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    assert lifted.result is GroupResult.BLOCK_REMOVED
    assert outcome.result is GroupResult.PROFILE


async def test_a_block_mid_offer_returns_the_stake(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    await run(trade, "блок", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT)

    outcome = await run(trade, "принять 1", author=MERLA_ACCOUNT, target=None)

    assert outcome.refusal is Refusal.BLOCKED_TARGET
    assert await inventory.count(argus.id, SWORD) == 1
    assert await inventory.count(merla.id, SWORD) == 0


# --- hand-overs -------------------------------------------------------


async def test_gold_moves_immediately_and_nothing_is_created(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    argus: Character,
    merla: Character,
) -> None:
    before = argus.gold + merla.gold

    outcome = await run(trade, "передать 100 золота", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.result is GroupResult.GOLD_GIVEN
    after = (await purse(characters, argus), await purse(characters, merla))
    assert after == (400, 400)
    assert sum(after) == before


async def test_a_hand_over_pays_no_duty(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    argus: Character,
    merla: Character,
) -> None:
    """A gift is not a trade: taxing it would only punish players for helping."""
    await run(trade, "передать 100 золота", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert await purse(characters, merla) == 400


async def test_gold_you_do_not_have_stays_where_it_is(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    argus: Character,
    merla: Character,
) -> None:
    outcome = await run(trade, "передать 5000 золота", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.AUTHOR_LACKS_GOLD
    assert await purse(characters, argus) == 500


async def test_an_item_is_handed_over_without_asking_the_receiver(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 2)

    outcome = await run(
        trade, f"передать 2 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.result is GroupResult.ITEM_GIVEN
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 2


async def test_an_item_nobody_holds_cannot_be_named(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(trade, "передать медный шлем", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_an_ambiguous_name_asks_instead_of_guessing(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, "iron_helm", 1)
    await inventory.add(argus.id, "iron_scrap", 1)

    outcome = await run(trade, "передать железный", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.AMBIGUOUS_ITEM
    assert len(outcome.options) == 2
    assert await inventory.count(argus.id, "iron_helm") == 1


async def test_giving_to_yourself_is_refused(
    trade: GroupTrade, inventory: InMemoryInventoryRepository, argus: Character
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(trade, f"передать {SWORD_NAME}", author=ARGUS_ACCOUNT, target=ARGUS_ACCOUNT)

    assert outcome.refusal is Refusal.SELF
    assert await inventory.count(argus.id, SWORD) == 1


# --- publishing an offer takes the author's side --------------------


async def test_a_sale_holds_the_item_and_asks_the_target_for_nothing(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.result is GroupResult.OFFER_MADE
    assert outcome.offer is not None
    assert outcome.offer.kind is OfferKind.SELL
    assert outcome.offer.price == 100
    # The seller's side is in escrow; the buyer has not been touched.
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 0
    assert await purse(characters, merla) == 300


async def test_a_purchase_holds_the_buyers_gold(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(merla.id, SWORD, 1)

    outcome = await run(
        trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.result is GroupResult.OFFER_MADE
    assert await purse(characters, argus) == 400
    # The target's item stays with them until they agree to part with it.
    assert await inventory.count(merla.id, SWORD) == 1


async def test_offering_gold_you_do_not_have_is_refused_up_front(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """A buyer stakes their money when they offer it, so it has to be there."""
    await inventory.add(merla.id, SWORD, 1)

    outcome = await run(
        trade, f"купить 5000 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.AUTHOR_LACKS_GOLD
    assert await purse(characters, argus) == 500


async def test_the_author_cannot_sell_the_same_item_twice(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """The first offer holds the sword, so the second has nothing to hold."""
    await inventory.add(argus.id, SWORD, 1)

    first = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )
    second = await run(
        trade, f"продать 200 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert first.result is GroupResult.OFFER_MADE
    assert second.refusal is Refusal.UNKNOWN_ITEM


async def test_selling_something_you_do_not_own_is_refused_up_front(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_buying_reads_the_targets_pack_not_your_own(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(
        trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_offers_get_distinct_numbers(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    await inventory.add(argus.id, "militia_spear", 1)

    first = await run(trade, f"продать 10 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    second = await run(
        trade, "продать 20 Ополченское копьё", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert first.offer is not None and second.offer is not None
    assert first.offer.number != second.offer.number


# --- settling ---------------------------------------------------------


async def test_accepting_a_sale_swaps_goods_for_gold_less_the_duty(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    accepted = await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert accepted.result is GroupResult.OFFER_ACCEPTED
    assert accepted.tax == trade_tax(100) == 5
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 1
    # The buyer pays 100, the seller receives 95, and five gold leave the game.
    assert (await purse(characters, argus), await purse(characters, merla)) == (595, 200)


async def test_a_purchase_runs_the_same_trade_from_the_other_side(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert await inventory.count(argus.id, SWORD) == 1
    assert await inventory.count(merla.id, SWORD) == 0
    assert (await purse(characters, argus), await purse(characters, merla)) == (400, 395)


async def test_a_settled_trade_is_the_only_way_gold_leaves_the_game(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    before = argus.gold + merla.gold
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 200 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    after = await purse(characters, argus) + await purse(characters, merla)
    assert before - after == trade_tax(200)


async def test_a_stranger_cannot_accept_someone_elses_offer(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await make(characters, STRANGER_ACCOUNT, "Довен", gold=1000)
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    outcome = await run(trade, f"принять {made.offer.number}", author=STRANGER_ACCOUNT, target=None)

    assert outcome.refusal is Refusal.NOT_YOURS
    # Refused, and the sword is still held by the offer, not by anyone.
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 0


async def test_the_author_may_cancel_but_not_accept(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    refused = await run(trade, f"принять {made.offer.number}", author=ARGUS_ACCOUNT, target=None)
    cancelled = await run(trade, f"отказ {made.offer.number}", author=ARGUS_ACCOUNT, target=None)

    assert refused.refusal is Refusal.NOT_YOURS
    assert cancelled.result is GroupResult.OFFER_DECLINED


async def test_a_declined_offer_is_gone_for_good(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"отказ {made.offer.number}", author=MERLA_ACCOUNT, target=None)
    again = await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert again.refusal is Refusal.UNKNOWN_OFFER


# --- what the escrow gives back ---------------------------------------


async def test_a_declined_sale_returns_the_item(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"отказ {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert await inventory.count(argus.id, SWORD) == 1


async def test_a_declined_purchase_returns_the_gold(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"отказ {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert await purse(characters, argus) == 500


async def test_an_offer_answered_too_late_says_so_and_gives_everything_back(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    outcome = await run(
        trade,
        f"принять {made.offer.number}",
        author=MERLA_ACCOUNT,
        target=None,
        now=NOW + OFFER_TTL_SECONDS,
    )

    assert outcome.refusal is Refusal.EXPIRED
    assert await inventory.count(argus.id, SWORD) == 1


async def test_a_stale_offer_is_swept_by_the_next_command_from_anyone(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Nobody answers, and the group moves on: the gold still comes back."""
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None
    assert await purse(characters, argus) == 400

    await run(
        trade,
        "профиль",
        author=MERLA_ACCOUNT,
        target=ARGUS_ACCOUNT,
        now=NOW + OFFER_TTL_SECONDS + SWEEP_GRACE_SECONDS,
    )

    assert await purse(characters, argus) == 500
    journal = await trades.journal(argus.id)
    assert [record.status for record in journal] == [TradeStatus.EXPIRED]


async def test_a_stake_comes_back_exactly_once(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Two sweeps and a late refusal must not mint a second refund."""
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None
    late = NOW + OFFER_TTL_SECONDS + SWEEP_GRACE_SECONDS

    for _ in range(3):
        await run(trade, "профиль", author=MERLA_ACCOUNT, target=ARGUS_ACCOUNT, now=late)
    await run(trade, f"отказ {made.offer.number}", author=ARGUS_ACCOUNT, target=None, now=late)

    assert await purse(characters, argus) == 500


async def test_a_quiet_group_does_not_hold_a_stake_hostage(
    trade: GroupTrade,
    content: GameContent,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    trades: InMemoryTradeRepository,
    privacy: InMemoryPrivacyRepository,
    argus: Character,
    merla: Character,
) -> None:
    """An offer made in one group is swept by a command in another.

    The number of an offer belongs to its group; the five minutes it lives do
    not. A group that fell silent must not keep the item of somebody who is
    playing elsewhere.
    """
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None
    assert await inventory.count(argus.id, SWORD) == 0

    elsewhere = GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        trades=trades,
        privacy=privacy,
        scope="another-group",
    )
    await run(
        elsewhere,
        "профиль",
        author=MERLA_ACCOUNT,
        target=ARGUS_ACCOUNT,
        now=NOW + OFFER_TTL_SECONDS + SWEEP_GRACE_SECONDS,
    )

    assert await inventory.count(argus.id, SWORD) == 1
    assert [record.status for record in await trades.journal(argus.id)] == [TradeStatus.EXPIRED]


async def test_a_restart_rolls_back_what_nobody_ever_answered(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    trades: InMemoryTradeRepository,
    argus: Character,
    merla: Character,
) -> None:
    """The last resort: a group nobody ever speaks in again still lets go."""
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None
    assert await purse(characters, argus) == 400

    late = NOW + OFFER_TTL_SECONDS + SWEEP_GRACE_SECONDS
    released = await release_expired_offers(
        trades=trades, characters=characters, inventory=inventory, now=late
    )

    assert released == 1
    assert await purse(characters, argus) == 500
    # Starting twice is not a way to print gold.
    assert (
        await release_expired_offers(
            trades=trades, characters=characters, inventory=inventory, now=late
        )
        == 0
    )
    assert await purse(characters, argus) == 500


async def test_a_restart_leaves_an_offer_that_is_still_standing(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    trades: InMemoryTradeRepository,
    argus: Character,
    merla: Character,
) -> None:
    """An offer published a moment before the restart is still answerable after it."""
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    released = await release_expired_offers(
        trades=trades, characters=characters, inventory=inventory, now=NOW + 1
    )

    assert released == 0
    assert await purse(characters, argus) == 400
    outcome = await run(
        trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None, now=NOW + 1
    )
    assert outcome.result is GroupResult.OFFER_ACCEPTED


# --- the journal ------------------------------------------------------


async def test_every_trade_leaves_a_row_behind(
    trade: GroupTrade,
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 2)
    first = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )
    assert first.offer is not None
    await run(trade, f"принять {first.offer.number}", author=MERLA_ACCOUNT, target=None)
    second = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )
    assert second.offer is not None
    await run(trade, f"отказ {second.offer.number}", author=MERLA_ACCOUNT, target=None)

    journal = await trades.journal(argus.id)

    assert [record.status for record in journal] == [TradeStatus.DECLINED, TradeStatus.ACCEPTED]
    settled = journal[-1]
    assert settled.tax == trade_tax(100)
    assert settled.settled_at == NOW
    # A trade that never happened cost nobody anything.
    assert journal[0].tax == 0


async def test_a_number_is_free_again_once_its_offer_closes(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Numbers are short on purpose, so closed ones have to come back round."""
    await inventory.add(argus.id, SWORD, 1)
    first = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )
    assert first.offer is not None
    await run(trade, f"отказ {first.offer.number}", author=MERLA_ACCOUNT, target=None)

    second = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert second.offer is not None
    assert second.offer.number == first.offer.number


# --- the gap between checking a purse and taking from it ---------------


class RacingCharacters(InMemoryCharacterRepository):
    """A repository where the purse empties between the check and the payment.

    Which is not a contrivance: the target of an offer is a player, and a player
    is somewhere else in the bot buying a bed with the same gold. What the trade
    read one await ago is a claim, not a fact.
    """

    async def spend_gold(self, character_id: int, amount: int) -> bool:
        return False


async def test_a_settlement_pays_out_of_the_purse_at_that_instant(
    content: GameContent,
    inventory: InMemoryInventoryRepository,
    trades: InMemoryTradeRepository,
    privacy: InMemoryPrivacyRepository,
) -> None:
    """The buyer's gold went elsewhere between "принять" and the payment.

    Nothing may move: not the sword out of escrow, not the price to the seller.
    """
    characters = RacingCharacters()
    seller = await make(characters, ARGUS_ACCOUNT, "Аргус", gold=500)
    buyer = await make(characters, MERLA_ACCOUNT, "Мерла", gold=300)
    trade = GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        trades=trades,
        privacy=privacy,
    )
    await inventory.add(seller.id, SWORD, 1)
    await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    outcome = await run(trade, "принять 1", author=MERLA_ACCOUNT, target=None)

    assert outcome.refusal is Refusal.TARGET_LACKS_GOLD
    # The sword is back with its owner, and nobody was paid for nothing.
    assert await inventory.count(seller.id, SWORD) == 1
    assert await inventory.count(buyer.id, SWORD) == 0
    assert await purse(characters, seller) == 500
    assert await purse(characters, buyer) == 300
    assert (await trades.pending(1, scope="group")) is None


# --- откат сделки (работа смотрителя, docs/keeper.md) ------------------


async def settled_sale(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
) -> int:
    """Продажа, по которой расчёт уже прошёл. Возвращает строку журнала."""
    await inventory.add(argus.id, SWORD, 1)
    await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    accepted = await run(trade, "принять 1", author=MERLA_ACCOUNT, target=None)
    assert accepted.result is GroupResult.OFFER_ACCEPTED
    journal = await trade.trades.journal(argus.id)
    assert [record.status for record in journal] == [TradeStatus.ACCEPTED]
    return journal[0].id


async def test_a_rolled_back_trade_puts_both_sides_back(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Вещь возвращается отдавшему, золото - платившему, пошлина не печатается."""
    trade_id = await settled_sale(trade, characters, inventory, argus)
    tax = trade_tax(100)

    undone = await roll_back(trade_id, trades=trades, characters=characters, inventory=inventory)

    assert undone.whole
    assert undone.gold_returned == 100 - tax
    assert await inventory.count(argus.id, SWORD) == 1
    assert await inventory.count(merla.id, SWORD) == 0
    # Продавец остался ровно с тем, с чем начинал; у покупателя не хватает
    # пошлины, и это честно: её в игре больше нет.
    assert await purse(characters, argus) == 500
    assert await purse(characters, merla) == 300 - tax
    assert [record.status for record in await trades.journal(argus.id)] == [TradeStatus.REVERTED]


async def test_a_trade_is_rolled_back_once_and_not_twice(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Второе нажатие ничего не двигает: иначе откат печатал бы вещи."""
    trade_id = await settled_sale(trade, characters, inventory, argus)

    first = await roll_back(trade_id, trades=trades, characters=characters, inventory=inventory)
    second = await roll_back(trade_id, trades=trades, characters=characters, inventory=inventory)

    assert first.done and not second.done
    assert await inventory.count(argus.id, SWORD) == 1


async def test_a_rollback_returns_what_it_can_and_names_what_it_cannot(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    trades: InMemoryTradeRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Обманувший успел потратить: возвращается вещь, а недостача названа числом."""
    trade_id = await settled_sale(trade, characters, inventory, argus)
    assert await characters.spend_gold(argus.id, await purse(characters, argus))

    undone = await roll_back(trade_id, trades=trades, characters=characters, inventory=inventory)

    assert undone.done and not undone.whole
    assert undone.item_returned
    assert undone.gold_returned == 0
    assert undone.gold_missing == 100 - trade_tax(100)
    assert await inventory.count(argus.id, SWORD) == 1
