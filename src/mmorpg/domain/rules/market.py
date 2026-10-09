"""Цена за всю партию, фиксированные номера и сроки без скрытого времени."""

from dataclasses import replace

from mmorpg.domain.entities.market import CraftOrder, Lot, MarketRules, MarketState


def refusal(
    rules: MarketRules, state: MarketState, owner_id: int, price: int, quantity: int
) -> str:
    if not 1 <= price <= rules.max_price or not 1 <= quantity <= rules.max_quantity:
        return "Цена и количество должны быть положительными и в пределах рынка."
    active = sum(
        one.seller_id == owner_id and one.status in {"open", "reserved"} for one in state.lots
    )
    active += sum(one.buyer_id == owner_id and one.status == "open" for one in state.orders)
    if active >= rules.open_limit:
        return "Предел открытых лотов и заказов достигнут. Закройте прежнее объявление."
    return ""


def change_lot(state: MarketState, lot: Lot) -> MarketState:
    return replace(state, lots=tuple(lot if one.id == lot.id else one for one in state.lots))


def change_order(state: MarketState, order: CraftOrder) -> MarketState:
    return replace(
        state, orders=tuple(order if one.id == order.id else one for one in state.orders)
    )


def supply_and_demand(
    state: MarketState, recipes: dict[str, str]
) -> tuple[tuple[str, int, int, int], ...]:
    supply: dict[str, int] = {}
    demand: dict[str, int] = {}
    sold: dict[str, int] = {}
    for lot in state.lots:
        target = (
            sold if lot.status == "sold" else supply if lot.status in {"open", "reserved"} else None
        )
        if target is not None:
            target[lot.item_id] = target.get(lot.item_id, 0) + lot.quantity
    for order in state.orders:
        item = recipes[order.recipe_id]
        if order.status == "open":
            demand[item] = demand.get(item, 0) + 1
        elif order.status == "filled":
            sold[order.item_id] = sold.get(order.item_id, 0) + order.quantity
    return tuple(
        (item, supply.get(item, 0), demand.get(item, 0), sold.get(item, 0))
        for item in sorted(supply.keys() | demand.keys() | sold.keys())
    )
