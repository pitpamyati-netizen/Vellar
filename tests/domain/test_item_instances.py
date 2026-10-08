"""Два одинаковых экземпляра различаются в правилах, ремонте и кнопках."""

from dataclasses import replace
from random import Random

import pytest

from mmorpg.domain.entities.character import Character, Equipment, ItemWear
from mmorpg.domain.entities.item_instance import (
    instance_ids,
    references,
    template_id,
    with_template,
)
from mmorpg.domain.procgen.items import worn
from mmorpg.domain.rules import repair, salvage, tools
from mmorpg.infrastructure.persistence.instances import MemoryItemInstances
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)


@pytest.mark.parametrize("ref", ["sword!0", "sword!a", "sword!1,1", "sword!", "sword!-1"])
def test_invalid_identity_is_rejected(ref):
    with pytest.raises(ValueError):
        instance_ids(ref)


def test_old_offer_bundle_keeps_every_identity():
    ref = "sword@1#rare~2!1,2"
    assert references(ref) == ("sword@1#rare~2!1", "sword@1#rare~2!2")
    assert template_id(ref) == "sword@1#rare~2"
    assert with_template(ref, "sword@1#rare~3") == "sword@1#rare~3!1,2"


def test_repair_and_reforge_do_not_affect_another_copy(content):
    a, b = "sword@1#rare!1", "sword@1#rare!2"
    hero = Character(
        1,
        1,
        "Герой",
        "human",
        "warrior",
        equipment=Equipment({"weapon": a}),
        wear=ItemWear({a: 20, b: 3}),
    )
    changed, _ = repair.wear(content, hero)
    assert changed.wear.spent(a) == 21
    assert changed.wear.spent(b) == 3
    fixed = repair.repaired(changed, [content.item(a)])
    assert fixed.wear.spent(a) == 0 and fixed.wear.spent(b) == 3
    forged = salvage.reforged(content, a, source=Random(1))
    assert instance_ids(forged) == (1,)
    assert template_id(forged) != template_id(a)
    assert content.item(a).name != content.item(b).name
    relic = content.item("sword@1#relic!3")
    assert worn(content, relic, 150).id == relic.id


async def test_memory_transfer_and_new_purchase_preserve_independent_wear(content):
    ledger = MemoryItemInstances(content)
    characters = InMemoryCharacterRepository(ledger)
    inventory = InMemoryInventoryRepository(ledger)
    first = await characters.create(Character(0, 1, "Первый", "human", "warrior"))
    second = await characters.create(Character(0, 2, "Второй", "human", "warrior"))
    await inventory.add(first.id, "sword@1#common", 2)
    a, b = [entry.item_id for entry in await inventory.list_items(first.id)]
    first = await characters.save(replace(first, wear=ItemWear({a: 40, b: 2})))
    assert await inventory.remove(first.id, a)
    await inventory.add(second.id, a)
    second = await characters.save(second.with_gold(1))
    assert (await characters.get(second.id)).wear.spent(a) == 40
    await inventory.add(second.id, "sword@1#common")
    newer = next(
        entry.item_id for entry in await inventory.list_items(second.id) if entry.item_id != a
    )
    assert (await characters.get(second.id)).wear.spent(newer) == 0
    assert (await characters.get(first.id)).wear.spent(b) == 2

    second = await characters.get(second.id)
    await characters.save(replace(second, wear=ItemWear({a: 50})))
    assert await inventory.remove(second.id, a)
    await inventory.add(first.id, a)
    await characters.save(first.with_gold(1))
    assert (await characters.get(first.id)).wear.spent(a) == 50


def test_tool_breaks_only_its_own_identity(content):
    item = next(one for one in content.items if one.is_tool)
    ref = item.id + "!1"
    hero = Character(
        1,
        1,
        "Герой",
        "human",
        "warrior",
        equipment=Equipment({"tool": ref}),
        wear=ItemWear({ref: item.durability - 1, item.id + "!2": 4}),
    )
    changed, broken = tools.wear(content, hero, content.item(ref))
    assert broken and changed.equipment.item_in("tool") is None
    assert changed.wear.spent(item.id + "!2") == 4
