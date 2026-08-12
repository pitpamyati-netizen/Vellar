"""Shop assortment and prices.

The assortment is a pure function of ``(city, cycle, reputation)`` - it is rolled
from a seed, never stored, and it rotates once per world cycle, which gives a
player a reason to come back (``docs/procgen.md``).
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


def roll_assortment(
    content: GameContent,
    *,
    world_seed: str,
    city_id: str,
    cycle: int,
    character_level: int,
    reputation: int = 0,
) -> tuple[Item, ...]:
    """What this city sells during this cycle.

    Higher reputation widens the shelf, so a regular customer sees more.
    """
    source = rng(shop_seed(world_seed, city_id, cycle))
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


def affordable(items: Sequence[Item], gold: int, prices: dict[str, int]) -> tuple[Item, ...]:
    return tuple(item for item in items if prices.get(item.id, 0) <= gold)


def charisma_of(stats: object) -> int:
    """Read charisma out of a stat block without the caller importing StatCode."""
    from mmorpg.domain.entities.stats import StatBlock

    if isinstance(stats, StatBlock):
        return stats[StatCode.CHA]
    return 0
