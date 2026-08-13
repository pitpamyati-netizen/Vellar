"""Offers between players (``Narrative.md``, section 9).

The rules that matter are the ones that protect a player from someone else's
mistake or malice: only the target may agree, nothing is guessed when a name is
ambiguous, and a stale offer refuses rather than settles. Since Roadmap 2.3 the
author also stakes their side up front, so most of these checks are about who is
still allowed to change their mind.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities.trade import Offer, OfferKind, Party
from mmorpg.domain.rules.group_offers import (
    MAX_OFFER_NUMBER,
    OFFER_TTL_SECONDS,
    ItemOption,
    Refusal,
    answerable_by,
    check_gift,
    check_gold_gift,
    check_proposal,
    check_settlement,
    is_expired,
    match_items,
    next_number,
    stakes_gold,
    stakes_item,
)

ARGUS = Party(user_id=1, character_id=10, name="Аргус")
MERLA = Party(user_id=2, character_id=20, name="Мерла")
STRANGER = 3


def offer(kind: OfferKind = OfferKind.SELL, *, price: int = 100, created_at: int = 1000) -> Offer:
    return Offer(
        number=7,
        kind=kind,
        author=ARGUS,
        target=MERLA,
        item_id="leather_armor",
        item_name="Кожаная броня",
        price=price,
        created_at=created_at,
    )


# --- who does what ----------------------------------------------------


def test_selling_means_the_author_parts_with_the_item() -> None:
    sale = offer(OfferKind.SELL)

    assert sale.giver == ARGUS
    assert sale.payer == MERLA


def test_buying_is_the_same_offer_from_the_other_side() -> None:
    purchase = offer(OfferKind.BUY)

    assert purchase.giver == MERLA
    assert purchase.payer == ARGUS


def test_only_the_target_may_agree() -> None:
    sale = offer()

    assert answerable_by(sale, MERLA.user_id) is True
    assert answerable_by(sale, ARGUS.user_id) is False
    assert answerable_by(sale, STRANGER) is False


# --- the five minutes -------------------------------------------------


def test_an_offer_expires_exactly_when_it_says_it_does() -> None:
    sale = offer(created_at=1000)

    assert is_expired(sale, 1000 + OFFER_TTL_SECONDS - 1) is False
    assert is_expired(sale, 1000 + OFFER_TTL_SECONDS) is True


def test_an_expired_offer_refuses_before_anything_else_is_checked() -> None:
    """Even a perfectly funded settlement is refused once the time is up."""
    refusal = check_settlement(
        offer(created_at=0), target_holds=5, target_gold=10_000, now=OFFER_TTL_SECONDS
    )

    assert refusal is Refusal.EXPIRED


# --- what an offer holds ----------------------------------------------


def test_a_sale_stakes_the_item_and_a_purchase_stakes_the_gold() -> None:
    """Whichever the author put up is what comes back if the offer dies."""
    assert stakes_item(offer(OfferKind.SELL)) is True
    assert stakes_gold(offer(OfferKind.SELL)) is False
    assert stakes_item(offer(OfferKind.BUY)) is False
    assert stakes_gold(offer(OfferKind.BUY)) is True


# --- publishing an offer ----------------------------------------------


def proposal(
    kind: OfferKind = OfferKind.SELL,
    *,
    author: Party = ARGUS,
    target: Party = MERLA,
    giver_holds: int = 1,
    price: int = 100,
    author_gold: int = 500,
) -> Refusal | None:
    return check_proposal(
        kind=kind,
        author=author,
        target=target,
        giver_holds=giver_holds,
        quantity=1,
        price=price,
        author_gold=author_gold,
    )


def test_an_offer_to_yourself_is_refused() -> None:
    assert proposal(target=ARGUS) is Refusal.SELF


def test_selling_what_you_do_not_have_is_your_problem() -> None:
    assert proposal(OfferKind.SELL, giver_holds=0) is Refusal.AUTHOR_LACKS_ITEM


def test_buying_what_they_do_not_have_names_the_right_side() -> None:
    assert proposal(OfferKind.BUY, giver_holds=0) is Refusal.TARGET_LACKS_ITEM


def test_a_buyer_has_to_have_the_money_before_they_offer_it() -> None:
    """The gold is staked the moment the offer is published, so it must exist."""
    assert proposal(OfferKind.BUY, price=100, author_gold=99) is Refusal.AUTHOR_LACKS_GOLD
    assert proposal(OfferKind.BUY, price=100, author_gold=100) is None


def test_the_targets_purse_is_never_read_to_publish_an_offer() -> None:
    """Checking the target's gold here would answer a question nobody asked.

    They have five minutes to find the money; refusing up front would both leak
    their balance and deny a trade they could have made. Only the author's own
    purse is read, and only when the author is the one paying.
    """
    assert proposal(OfferKind.SELL, author_gold=0) is None


# --- settling ---------------------------------------------------------


def test_a_settlement_reads_only_the_side_that_is_answering() -> None:
    assert check_settlement(offer(), target_holds=0, target_gold=100, now=1000) is None


def test_a_buyer_who_spent_the_money_meanwhile_cannot_agree() -> None:
    """The seller's item is in escrow; the buyer's purse is the open question."""
    refusal = check_settlement(offer(OfferKind.SELL), target_holds=0, target_gold=99, now=1000)

    assert refusal is Refusal.TARGET_LACKS_GOLD


def test_a_seller_who_sold_it_elsewhere_cannot_agree_to_a_purchase() -> None:
    """A purchase holds the buyer's gold, so the item is the open question."""
    refusal = check_settlement(offer(OfferKind.BUY), target_holds=0, target_gold=0, now=1000)

    assert refusal is Refusal.TARGET_LACKS_ITEM


def test_the_author_can_no_longer_break_their_own_offer() -> None:
    """Whatever the author staked is already gone from their side of the table."""
    assert check_settlement(offer(OfferKind.BUY), target_holds=1, target_gold=0, now=1000) is None


def test_paying_the_exact_price_is_enough() -> None:
    assert check_settlement(offer(price=100), target_holds=0, target_gold=100, now=1000) is None


# --- hand-overs -------------------------------------------------------


def test_a_gift_only_needs_the_stock() -> None:
    assert check_gift(author=ARGUS, target=MERLA, holds=3, quantity=3) is None
    assert check_gift(author=ARGUS, target=MERLA, holds=2, quantity=3) is Refusal.AUTHOR_LACKS_ITEM


def test_gold_cannot_be_given_to_yourself_or_out_of_thin_air() -> None:
    assert check_gold_gift(author=ARGUS, target=ARGUS, purse=500, amount=1) is Refusal.SELF
    assert (
        check_gold_gift(author=ARGUS, target=MERLA, purse=99, amount=100)
        is Refusal.AUTHOR_LACKS_GOLD
    )
    assert check_gold_gift(author=ARGUS, target=MERLA, purse=100, amount=100) is None


# --- numbering --------------------------------------------------------


def test_offer_numbers_stay_short_enough_to_type() -> None:
    assert next_number(0) == 1
    assert next_number(11) == 12
    assert next_number(MAX_OFFER_NUMBER) == 1


# --- naming an item the way a player names it -------------------------


CATALOGUE = (
    ItemOption(item_id="leather_armor", name="Кожаная броня"),
    ItemOption(item_id="leather_boots", name="Кожаные сапоги"),
    ItemOption(item_id="salt_blade", name="Соляной клинок, щербатый"),
)


@pytest.mark.parametrize(
    "query",
    ["Кожаная броня", "кожаная броня", "  КОЖАНАЯ   БРОНЯ ", "leather_armor"],
)
def test_an_exact_name_wins_however_it_is_typed(query: str) -> None:
    found = match_items(query, CATALOGUE)

    assert [option.item_id for option in found] == ["leather_armor"]


def test_a_prefix_is_enough_when_it_is_unambiguous() -> None:
    found = match_items("соляной", CATALOGUE)

    assert [option.item_id for option in found] == ["salt_blade"]


def test_an_ambiguous_prefix_returns_every_candidate() -> None:
    """Two leather things and one word: the caller must ask, never guess."""
    found = match_items("кожа", CATALOGUE)

    assert len(found) == 2


def test_a_word_from_the_middle_still_finds_the_item() -> None:
    found = match_items("щербатый", CATALOGUE)

    assert [option.item_id for option in found] == ["salt_blade"]


def test_nothing_matches_nothing() -> None:
    assert match_items("подорожная", CATALOGUE) == ()
    assert match_items("   ", CATALOGUE) == ()


def test_an_exact_name_beats_a_longer_one_that_contains_it() -> None:
    catalogue = (
        ItemOption(item_id="rope", name="Верёвка"),
        ItemOption(item_id="rope_long", name="Верёвка длинная"),
    )

    found = match_items("верёвка", catalogue)

    assert [option.item_id for option in found] == ["rope"]
