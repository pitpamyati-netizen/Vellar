"""Offers between players (``Narrative.md``, section 9).

The rules that matter are the ones that protect a player from someone else's
mistake or malice: only the target may agree, nothing is guessed when a name is
ambiguous, and a stale offer refuses rather than settles.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.rules.group_offers import (
    MAX_OFFER_NUMBER,
    OFFER_TTL_SECONDS,
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
    is_expired,
    match_items,
    next_number,
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
        offer(created_at=0), giver_holds=5, payer_gold=10_000, now=OFFER_TTL_SECONDS
    )

    assert refusal is Refusal.EXPIRED


# --- publishing an offer ----------------------------------------------


def test_an_offer_to_yourself_is_refused() -> None:
    refusal = check_proposal(
        kind=OfferKind.SELL, author=ARGUS, target=ARGUS, giver_holds=1, quantity=1
    )

    assert refusal is Refusal.SELF


def test_selling_what_you_do_not_have_is_your_problem() -> None:
    refusal = check_proposal(
        kind=OfferKind.SELL, author=ARGUS, target=MERLA, giver_holds=0, quantity=1
    )

    assert refusal is Refusal.AUTHOR_LACKS_ITEM


def test_buying_what_they_do_not_have_names_the_right_side() -> None:
    refusal = check_proposal(
        kind=OfferKind.BUY, author=ARGUS, target=MERLA, giver_holds=0, quantity=1
    )

    assert refusal is Refusal.TARGET_LACKS_ITEM


def test_a_purse_is_never_read_to_publish_an_offer() -> None:
    """Checking the target's gold here would answer a question nobody asked.

    They have five minutes to find the money; refusing up front would both leak
    their balance and deny a trade they could have made.
    """
    assert (
        check_proposal(kind=OfferKind.SELL, author=ARGUS, target=MERLA, giver_holds=1, quantity=1)
        is None
    )


# --- settling ---------------------------------------------------------


def test_a_settlement_needs_the_goods_and_the_money() -> None:
    assert check_settlement(offer(), giver_holds=1, payer_gold=100, now=1000) is None


def test_the_item_may_be_gone_by_the_time_the_answer_arrives() -> None:
    """Nothing is held in escrow, so the seller can sell it elsewhere first."""
    refusal = check_settlement(offer(), giver_holds=0, payer_gold=100, now=1000)

    assert refusal is Refusal.AUTHOR_LACKS_ITEM


def test_the_payer_is_named_by_the_kind_of_offer() -> None:
    sale = check_settlement(offer(OfferKind.SELL), giver_holds=1, payer_gold=1, now=1000)
    purchase = check_settlement(offer(OfferKind.BUY), giver_holds=1, payer_gold=1, now=1000)

    assert sale is Refusal.TARGET_LACKS_GOLD
    assert purchase is Refusal.AUTHOR_LACKS_GOLD


def test_paying_the_exact_price_is_enough() -> None:
    assert check_settlement(offer(price=100), giver_holds=1, payer_gold=100, now=1000) is None


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
