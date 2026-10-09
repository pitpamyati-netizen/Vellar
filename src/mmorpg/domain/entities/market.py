"""Товар и оплата в резерве принадлежат указанному участнику."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketRules:
    lifetime: int = 604800
    reservation_lifetime: int = 86400
    open_limit: int = 10
    max_price: int = 1000000000
    max_quantity: int = 99
    order_recipes: tuple[str, ...] = ("smith_ingot@1",)


@dataclass(frozen=True, slots=True)
class Lot:
    id: int
    seller_id: int
    item_id: str
    quantity: int
    price: int
    expires: int
    status: str = "open"
    buyer_id: int = 0
    reserved_until: int = 0
    tax: int = 0
    receipts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CraftOrder:
    id: int
    buyer_id: int
    recipe_id: str
    price: int
    expires: int
    status: str = "open"
    maker_id: int = 0
    item_id: str = ""
    quantity: int = 0
    tax: int = 0
    receipts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketState:
    next_id: int = 1
    lots: tuple[Lot, ...] = ()
    orders: tuple[CraftOrder, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketResult:
    notice: str
    id: int = 0
    ok: bool = False
