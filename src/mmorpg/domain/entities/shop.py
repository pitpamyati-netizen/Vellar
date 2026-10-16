"""Лавки площади: род снаряжения и семейства товаров из каталога."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mmorpg.domain.entities.content import Item


@dataclass(frozen=True, slots=True)
class Shop:
    id: str
    name: str
    description: str
    equipment: str = ""
    families: tuple[tuple[str, ...], ...] = ()

    def sells(self, item: Item) -> bool:
        if any(item.id in family for family in self.families):
            return True
        return {
            "weapon": item.is_weapon,
            "armor": item.is_armor,
            "trinket": item.is_equipment and item.slot == "trinket",
            "tool": item.is_tool,
        }.get(self.equipment, False)
