"""Общий износ физических вещей для адаптеров в памяти."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character, Equipment, ItemWear
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.item_instance import instance_ids
from mmorpg.infrastructure.persistence.operations import MemoryOperations


class MemoryItemInstances:
    def __init__(self, content: GameContent) -> None:
        self.operations = MemoryOperations()
        self.content = content
        self.used: dict[int, int] = {}
        self.next_id = 1
        self.held: dict[int, set[str]] = {}

    def mint(self, item_id: str, wear: int = 0) -> str:
        number = self.next_id
        self.next_id += 1
        self.used[number] = max(0, wear)
        return f"{item_id}!{number}"

    def physical(self, item_id: str) -> bool:
        return self.content.has_item(item_id) and self.content.item(item_id).is_equipment

    def prepare(self, character: Character) -> Character:
        equipment = {}
        used = dict(character.wear.used)
        for slot, item in character.equipment.items.items():
            if not instance_ids(item) and self.physical(item):
                reference = self.mint(item, character.wear.spent(item))
                equipment[slot] = reference
                used[reference] = character.wear.spent(item)
                used.pop(item, None)
            else:
                equipment[slot] = item
        return replace(character, equipment=Equipment(equipment), wear=ItemWear(used))

    def hydrate(self, character: Character | None) -> Character | None:
        if character is None:
            return None
        used = {key: value for key, value in character.wear.used.items() if not instance_ids(key)}
        for ref in (*character.equipment.item_ids(), *self.held.get(character.id, set())):
            for number in instance_ids(ref):
                if spent := self.used.get(number, 0):
                    used[ref] = spent
        return replace(character, wear=ItemWear(used))

    def save_wear(self, character: Character, previous: Character) -> None:
        for ref in (*character.equipment.item_ids(), *self.held.get(character.id, set())):
            for number in instance_ids(ref):
                if character.wear.used.get(ref) != previous.wear.used.get(ref):
                    self.used[number] = character.wear.spent(ref)
