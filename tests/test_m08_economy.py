"""Наблюдаемые данные рынка, правила цены и понятный отчёт без служб."""

from datetime import UTC, datetime

import pytest
from scripts.m08_economy import Report, demand_snapshot, npc_cycles, read_report, render

from mmorpg.domain.entities.market import CraftOrder, Lot, MarketState


def test_market_snapshot_excludes_expired_records_and_counts_order_quantities(content):
    recipe = content.recipes[0]
    state = MarketState(
        lots=(
            Lot(1, 11, "iron_scrap", 2, 10, 200),
            Lot(2, 11, "iron_scrap", 3, 10, 200, status="reserved", reserved_until=150),
            Lot(3, 11, "iron_scrap", 4, 10, 99),
            Lot(4, 11, "iron_scrap", 5, 10, 200, status="reserved", reserved_until=99),
            Lot(5, 11, "iron_scrap", 6, 10, 99, status="sold", receipts=("recent",)),
            Lot(6, 11, "iron_scrap", 7, 10, 99, status="sold", receipts=("old",)),
            Lot(7, 11, "iron_scrap", 8, 10, 200, status="cancelled"),
        ),
        orders=(
            CraftOrder(8, 11, recipe.id, 10, 200),
            CraftOrder(9, 11, recipe.id, 10, 99),
        ),
    )
    rows, awaiting = demand_snapshot(state, content, 100, {"recent"})
    supply = next(one for one in rows if one.item_id == "iron_scrap")
    assert (supply.offered, supply.reserved, supply.sold) == (2, 3, 6)
    demand = next(one for one in rows if one.item_id == recipe.output_id)
    assert demand.orders == 1 and demand.ordered_base == recipe.output_count
    assert awaiting == 3


def test_same_equipment_template_combines_physical_instances_in_supply(content):
    state = MarketState(
        lots=(
            Lot(1, 11, "sword@1#common!123", 1, 10, 200),
            Lot(2, 11, "sword@1#common!124", 1, 10, 200),
        )
    )
    rows, awaiting = demand_snapshot(state, content, 100, set())
    assert len(rows) == 1 and rows[0].item_id == "sword@1#common"
    assert rows[0].offered == 2 and awaiting == 0


@pytest.mark.parametrize("hours", [-1, float("inf"), float("nan")])
async def test_invalid_period_is_rejected_before_connecting_or_reading(content, hours):
    with pytest.raises(ValueError, match="Часы"):
        await read_report(None, content, hours=hours)


async def test_invalid_lot_is_rejected_before_reading(content):
    with pytest.raises(ValueError, match="Номер"):
        await read_report(None, content, lot_id=-1)


def test_current_npc_rules_do_not_offer_profitable_repurchase_cycles(content):
    checks, profitable = npc_cycles(content)
    assert checks == len(content.items) and checks > 0
    assert not profitable


def test_empty_report_explains_limits_and_missing_lot_without_inflation_claim(content):
    report = Report(datetime(2026, 10, 9, tzinfo=UTC), 24, (), (), 0, None, 8, 0, ())
    text = render(report, content)
    assert "За период экономических записей нет" in text
    assert "Лот или заказ 8 не найден" in text
    assert "сам по себе не доказывает инфляцию" in text
