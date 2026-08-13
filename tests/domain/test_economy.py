"""Prices, and the duty the Salt Watch takes from a deal between players.

The duty is the only outflow of gold in the game, so what matters is that it
cannot be dodged by splitting a trade into small ones, and that what the seller
is promised is exactly what the seller gets.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.rules.economy import TRADE_TAX_PERCENT, payout, trade_tax


def test_the_duty_is_a_share_of_the_price() -> None:
    assert trade_tax(100) == TRADE_TAX_PERCENT
    assert trade_tax(1000) == 50


def test_a_priced_trade_always_costs_at_least_one_gold() -> None:
    """Otherwise a thousand one-coin sales would move a fortune untaxed."""
    assert trade_tax(1) == 1
    assert trade_tax(10) == 1


def test_a_free_offer_is_not_taxed() -> None:
    """Price zero is a hand-over wearing an offer's clothes; there is nothing to take."""
    assert trade_tax(0) == 0
    assert payout(0) == 0


def test_what_the_seller_is_promised_is_the_price_less_the_duty() -> None:
    assert payout(100) == 95
    assert payout(100) + trade_tax(100) == 100


@pytest.mark.parametrize("price", [1, 7, 13, 99, 100, 4_321, 1_000_000])
def test_nothing_is_ever_created_by_rounding(price: int) -> None:
    """Whatever the buyer pays is either received or burned - never invented."""
    assert payout(price) + trade_tax(price) == price
    assert 0 <= payout(price) <= price
