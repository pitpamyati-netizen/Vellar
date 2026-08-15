"""Shop assortment, prices, and the duty on a trade between players.

The assortment is a pure function of ``(city, rotation, reputation)`` - it is
rolled from a seed, never stored, and the shelf turns over every half hour, which
is the one reason left to come back to a city on a clock (``docs/procgen.md``).
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen.seeds import rng, shop_seed

STOCK_MIN = 6
STOCK_MAX = 12
LEVEL_WINDOW_BELOW = 6
LEVEL_WINDOW_ABOVE = 4
CHARISMA_DISCOUNT_PER_POINT = 0.4
MAX_CHARISMA_DISCOUNT = 15.0
SELL_FRACTION = 0.35

# The Treaty of Cities holds on duties, not on loyalty (``Narrative.md``, section
# 1), and the Salt Watch takes its share of every deal struck between players.
# The number is the whole reason gold does not pile up forever: it is the one
# outflow that scales with how much the players actually trade.
TRADE_TAX_PERCENT = 5


def roll_assortment(
    content: GameContent,
    *,
    world_seed: str,
    city_id: str,
    rotation: int,
    character_level: int,
    reputation: int = 0,
) -> tuple[Item, ...]:
    """What this city sells in this rotation of its shelf.

    Higher reputation widens the shelf, so a regular customer sees more.
    """
    source = rng(shop_seed(world_seed, city_id, rotation))
    low = max(1, character_level - LEVEL_WINDOW_BELOW)
    high = character_level + LEVEL_WINDOW_ABOVE + reputation // 100

    candidates = [item for item in content.items if low <= item.level <= high]
    if not candidates:
        candidates = sorted(content.items, key=lambda item: abs(item.level - character_level))[:8]

    size = min(len(candidates), source.randint(STOCK_MIN, STOCK_MAX) + reputation // 200)
    chosen = source.sample(candidates, k=size)
    return tuple(sorted(chosen, key=lambda item: (item.level, item.name)))


def buy_price(
    content: GameContent,
    item: Item,
    *,
    modifiers: dict[str, float] | None = None,
    charisma: int = 0,
) -> int:
    """Shop price after rarity, charisma and trait discounts."""
    rarity = content.rarity(item.rarity)
    price = item.price * rarity.price_factor

    discount = min(MAX_CHARISMA_DISCOUNT, charisma * CHARISMA_DISCOUNT_PER_POINT)
    if modifiers:
        # shop_price_percent is negative when it helps, so it subtracts directly.
        discount += -modifiers.get("shop_price_percent", 0.0)
    return max(1, round(price * max(0.4, 1.0 - discount / 100.0)))


def sell_price(
    content: GameContent, item: Item, *, modifiers: dict[str, float] | None = None
) -> int:
    """What a merchant pays for an item the player brings in."""
    rarity = content.rarity(item.rarity)
    price = item.price * rarity.price_factor * SELL_FRACTION
    bonus = 1.0 + (modifiers.get("sell_price_percent", 0.0) if modifiers else 0.0) / 100.0
    return max(1, round(price * bonus))


def trade_tax(price: int, *, percent: int = TRADE_TAX_PERCENT) -> int:
    """The duty on a settled trade between two players.

    Charged once, on the price, and it leaves the game entirely - nobody receives
    it. A free hand-over (``передать``) is not a trade and is not taxed: taxing a
    gift would only punish players for helping each other, and two hand-overs can
    always replace a sale anyway. That is not a loophole to close but a trade
    without the protection of a confirmation.

    A priced trade always costs at least one gold, so a shower of one-coin sales
    cannot launder value past the duty.
    """
    if price <= 0:
        return 0
    return max(1, round(price * percent / 100))


def payout(price: int, *, percent: int = TRADE_TAX_PERCENT) -> int:
    """What the seller actually receives: the price, less the duty."""
    return max(0, price - trade_tax(price, percent=percent))


# --- what a city charges for its services ----------------------------
#
# A bed, a teacher and a strongbox are the three ways gold leaves a player
# without another player receiving it. All three scale with level, because a
# level 40 character earns in one watch what a level 4 character earns in ten.

INN_PRICE_BASE = 5
INN_PRICE_PER_LEVEL = 3
STRAW_HEAL_PERCENT = 30
MENTOR_PRICE_BASE = 40
MENTOR_PRICE_PER_LEVEL = 10
BANK_DEPOSIT_STEP = 50


def inn_price(level: int) -> int:
    """A night at the inn: full health, priced by what the guest can pay."""
    return max(1, INN_PRICE_BASE + INN_PRICE_PER_LEVEL * max(0, level - 1))


def mentor_price(level: int) -> int:
    """What a teacher charges to unpick a rank or an edge and hand the point back."""
    return max(1, MENTOR_PRICE_BASE + MENTOR_PRICE_PER_LEVEL * max(0, level - 1))


def affordable(items: Sequence[Item], gold: int, prices: dict[str, int]) -> tuple[Item, ...]:
    return tuple(item for item in items if prices.get(item.id, 0) <= gold)


def charisma_of(stats: object) -> int:
    """Read charisma out of a stat block without the caller importing StatCode."""
    from mmorpg.domain.entities.stats import StatBlock

    if isinstance(stats, StatBlock):
        return stats[StatCode.CHA]
    return 0
