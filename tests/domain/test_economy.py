"""Цены и пошлина, которую Соляная стража берёт со сделки между игроками.

Пошлина - единственный отток золота в игре, поэтому важно, что её нельзя обойти,
разбив сделку на мелкие, и что обещанное продавцу - ровно то, что продавец и
получит.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.rules.economy import (
    TRADE_TAX_PERCENT,
    payout,
    trade_tax,
    travel_price,
)


def test_the_duty_is_a_share_of_the_price() -> None:
    assert trade_tax(100) == TRADE_TAX_PERCENT
    assert trade_tax(1000) == 50


def test_a_priced_trade_always_costs_at_least_one_gold() -> None:
    """Иначе тысяча продаж по монете провела бы состояние мимо пошлины."""
    assert trade_tax(1) == 1
    assert trade_tax(10) == 1


def test_a_free_offer_is_not_taxed() -> None:
    """Цена в ноль - это передача в одежде предложения; брать здесь нечего."""
    assert trade_tax(0) == 0
    assert payout(0) == 0


def test_what_the_seller_is_promised_is_the_price_less_the_duty() -> None:
    assert payout(100) == 95
    assert payout(100) + trade_tax(100) == 100


@pytest.mark.parametrize("price", [1, 7, 13, 99, 100, 4_321, 1_000_000])
def test_nothing_is_ever_created_by_rounding(price: int) -> None:
    """Всё, что платит покупатель, либо получено, либо сожжено, но не выдумано."""
    assert payout(price) + trade_tax(price) == price
    assert 0 <= payout(price) <= price


def test_the_road_costs_more_the_farther_and_the_higher_the_level() -> None:
    """Плата за дорогу растёт и с числом городов между, и с уровнем путника."""
    assert travel_price(1, 1) < travel_price(1, 5)
    assert travel_price(1, 3) < travel_price(50, 3)
    # Соседний город и «остаться на месте» стоят одинаково: меньше одной ноги дороги нет.
    assert travel_price(10, 0) == travel_price(10, 1)
    assert travel_price(10, 1) >= 1
