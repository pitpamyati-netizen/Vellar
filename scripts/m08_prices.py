"""Воспроизводимые цены и проверка составных обменов M08; базы не нужны."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from random import Random

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.procgen.enemies import gold_at
from mmorpg.domain.rules import crafts, economy, salvage
from mmorpg.infrastructure.content import load_content

CHECKPOINTS = (1, 30, 75, 150)
SEED = 96


@dataclass(frozen=True)
class PriceRow:
    level: int
    item_level: int
    rarity: str
    base: int
    previous_buy: int
    buy: int
    trader_buy: int
    minimum_buy: int
    sell: int
    trader_sell: int
    full_repair: int
    reforge: int
    salvage: tuple[tuple[str, int], ...]
    fights_to_buy: float
    fights_to_repair: float


@dataclass(frozen=True)
class Audit:
    seed: int
    catalogue_items: int
    recipes: int
    direct_checks: int
    salvage_checks: int
    craft_checks: int
    reforge_checks: int
    unavailable_salvage: tuple[str, ...]
    violations: tuple[str, ...]
    prices: tuple[PriceRow, ...]


def price_rows(content: GameContent) -> tuple[PriceRow, ...]:
    rows: list[PriceRow] = []
    for level in CHECKPOINTS:
        tier = gear_procgen.tier_at(content, level)
        assert tier is not None
        for rarity in content.rarities:
            item = content.item(gear_procgen.gear_id("sword", tier.level, rarity.id))
            rows.append(
                PriceRow(
                    level=level,
                    item_level=tier.level,
                    rarity=rarity.id,
                    base=item.price,
                    previous_buy=round(item.price * rarity.price_factor),
                    buy=economy.buy_price(content, item),
                    trader_buy=economy.buy_price(
                        content, item, charisma=40, modifiers={"shop_price_percent": -18}
                    ),
                    minimum_buy=economy.buy_price(
                        content, item, charisma=1000, modifiers={"shop_price_percent": -1000}
                    ),
                    sell=economy.sell_price(content, item),
                    trader_sell=economy.sell_price(
                        content, item, modifiers={"sell_price_percent": 25}
                    ),
                    full_repair=economy.repair_price(item.price, item.durability, item.durability),
                    reforge=salvage.reforge_price(content, item),
                    salvage=salvage.yield_of(content, item),
                    fights_to_buy=round(economy.buy_price(content, item) / gold_at(level), 2),
                    fights_to_repair=round(
                        economy.repair_price(item.price, item.durability, item.durability)
                        / gold_at(level),
                        2,
                    ),
                )
            )
    return tuple(rows)


def audit(content: GameContent) -> Audit:
    """Проверить уменьшающуюся ценность на каждом шаге графа обменов.

    Любая составная цепочка состоит из этих шагов: их сумма не создаёт
    ценность. Качества перебираются все, вероятность и удачный сид не
    оправдывают прибыль. Бонусы нарочно выше возможных ныне сборок.
    """
    violations: list[str] = []
    unavailable_salvage: list[str] = []
    direct_checks = salvage_checks = craft_checks = reforge_checks = 0
    values: dict[str, Fraction] = {}

    def value(item_id: str) -> Fraction:
        if item_id not in values:
            values[item_id] = economy.recovery_value(content, content.item(item_id))
        return values[item_id]

    for item in content.items:
        direct_checks += 1
        minimum = economy.buy_price(
            content, item, charisma=1000, modifiers={"shop_price_percent": -1000}
        )
        maximum_sale = economy.sell_price(content, item, modifiers={"sell_price_percent": 1000})
        if not maximum_sale < minimum or maximum_sale > value(item.id):
            violations.append(f"purchase-sale: {item.id}")
        if item.is_equipment and not item.is_tool:
            salvage_checks += 1
            made = salvage.yield_of(content, item, modifiers={"salvage_yield_percent": 1000})
            recovered = sum((value(key) * amount for key, amount in made), Fraction(0))
            if made and recovered >= value(item.id):
                violations.append(f"salvage: {item.id}")
            if not made:
                unavailable_salvage.append(item.id)
            if (
                item.durability
                and economy.repair_price(item.price, item.durability, item.durability) >= minimum
            ):
                violations.append(f"repair affordability: {item.id}")
        if salvage.can_reforge(content, item):
            reforge_checks += 1
            forged = salvage.reforged(content, item.id, source=Random(SEED))
            if value(forged) != value(item.id) or salvage.reforge_price(content, item) <= 0:
                violations.append(f"reforge: {item.id}")
    for recipe in content.recipes:
        for quality in content.craft_rules.qualities:
            craft_checks += 1
            made_id = crafts.upgraded(content, recipe.output_id, quality, source=Random(SEED))
            gear = gear_procgen.parse_gear_id(recipe.output_id) is not None
            amount = recipe.output_count + (0 if gear else quality.extra)
            cost = sum(
                (
                    value(need.item_id) * (1 if quality.refund_percent > 0 else need.count)
                    for need in recipe.inputs
                ),
                Fraction(0),
            )
            if value(made_id) * amount > cost:
                violations.append(f"craft: {recipe.id}/{quality.id}")
    return Audit(
        seed=SEED,
        catalogue_items=len(content.items),
        recipes=len(content.recipes),
        direct_checks=direct_checks,
        salvage_checks=salvage_checks,
        craft_checks=craft_checks,
        reforge_checks=reforge_checks,
        unavailable_salvage=tuple(unavailable_salvage),
        violations=tuple(violations),
        prices=price_rows(content),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(load_content(Path(__file__).resolve().parents[1] / "content"))
    serialized = json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return int(bool(result.violations))


if __name__ == "__main__":
    raise SystemExit(main())
