"""Проверка отделов торговой площади и семейств товаров."""

from collections.abc import Mapping, Sequence
from typing import Any

from mmorpg.domain.entities.content import Item
from mmorpg.domain.entities.shop import Shop


def parse_shops(
    raw: Mapping[str, Any], items: Sequence[Item], problems: list[str]
) -> tuple[Shop, ...]:
    result: list[Shop] = []
    known = {item.id: item for item in items}
    ids: set[str] = set()
    names: set[str] = set()
    kinds: set[str] = set()
    assigned: set[str] = set()
    for entry in raw.get("shop", []):
        try:
            shop = Shop(
                id=entry["id"],
                name=entry["name"],
                description=entry["description"],
                equipment=entry.get("equipment", ""),
                families=tuple(tuple(family) for family in entry.get("families", [])),
            )
            if not all(
                isinstance(s, str) and s.strip() for s in (shop.id, shop.name, shop.description)
            ):
                raise ValueError("id, name and description must be nonempty strings")
            if shop.id in ids or shop.name.casefold() in names:
                raise ValueError("duplicate shop id or name")
            if shop.equipment not in {"", "weapon", "armor", "trinket", "tool"}:
                raise ValueError("unknown equipment kind")
            if shop.equipment and shop.equipment in kinds:
                raise ValueError("equipment kind belongs to two shops")
            if not shop.equipment and not shop.families:
                raise ValueError("empty shop")
            for family in shop.families:
                if not family or any(i not in known for i in family):
                    raise ValueError("empty family or unknown item")
                levels = [known[i].level for i in family]
                if levels != sorted(set(levels)):
                    raise ValueError("family levels must strictly increase")
                for item_id in family:
                    if item_id in assigned or known[item_id].is_equipment:
                        raise ValueError("duplicate or equipment item in family")
                    assigned.add(item_id)
            ids.add(shop.id)
            names.add(shop.name.casefold())
            kinds.add(shop.equipment)
            result.append(shop)
        except (KeyError, TypeError, ValueError) as error:
            problems.append(f"shops.toml: {error}")
    if kinds - {""} != {"weapon", "armor", "trinket", "tool"}:
        problems.append("shops.toml: every equipment kind needs a shop")
    if assigned != set(known):
        problems.append("shops.toml: every written item needs exactly one family")
    return tuple(result)
