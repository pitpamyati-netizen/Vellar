"""Правила рынка проверяются вместе с остальным каталогом."""

from collections.abc import Mapping, Sequence
from typing import Any

from mmorpg.domain.entities.craft import Recipe
from mmorpg.domain.entities.market import MarketRules


def parse_market(
    raw: Mapping[str, Any], recipes: Sequence[Recipe], problems: list[str]
) -> MarketRules:
    data = raw.get("rules", {})
    try:
        rules = MarketRules(**data)
        rules = MarketRules(
            rules.lifetime,
            rules.reservation_lifetime,
            rules.open_limit,
            rules.max_price,
            rules.max_quantity,
            tuple(rules.order_recipes),
        )
        if any(
            type(value) is not int or value <= 0
            for value in (
                rules.lifetime,
                rules.reservation_lifetime,
                rules.open_limit,
                rules.max_price,
                rules.max_quantity,
            )
        ):
            raise ValueError("positive integer limits required")
        if rules.reservation_lifetime > rules.lifetime:
            raise ValueError("reservation exceeds lot lifetime")
        known = {one.id for one in recipes}
        if not rules.order_recipes or any(one not in known for one in rules.order_recipes):
            raise ValueError("unknown or empty order recipes")
        return rules
    except (TypeError, ValueError) as error:
        problems.append(f"market.toml: {error}")
        return MarketRules()
