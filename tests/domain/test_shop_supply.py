"""Постоянные зелья доступны на всей полосе уровней и в любом состоянии лавки."""

from dataclasses import replace

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.rules.economy import permanent_stock, roll_assortment

POTION_TIERS = (
    (1, "small_healing_potion", "small_resource_draught"),
    (24, "healing_potion", "resource_draught"),
    (46, "strong_healing_potion", "strong_resource_draught"),
    (76, "great_healing_potion", "great_resource_draught"),
    (113, "full_healing_potion", "full_resource_draught"),
)


def test_every_level_keeps_all_unlocked_potions(content: GameContent) -> None:
    for level in range(1, 151):
        expected = {item_id for tier, *items in POTION_TIERS if tier <= level for item_id in items}
        assert {item.id for item in permanent_stock(content, character_level=level)} == expected


@pytest.mark.parametrize("rotation", [0, 1, 7, 1000])
@pytest.mark.parametrize("reputation,strain", [(0.0, 0.0), (0.0, 1.0), (400.0, 1.0)])
def test_every_city_guarantees_potions_without_duplicates(
    content: GameContent, rotation: int, reputation: float, strain: float
) -> None:
    for city in content.cities:
        for level in (1, 20, 23, 24, 45, 46, 75, 76, 112, 113, 150):
            stock = roll_assortment(
                content,
                world_seed="guaranteed-potions",
                city_id=city.id,
                rotation=rotation,
                character_level=level,
                reputation=reputation,
                strain=strain,
            )
            ids = [item.id for item in stock]
            assert len(ids) == len(set(ids))
            assert {item.id for item in stock if item.shop_always} == {
                item.id for item in permanent_stock(content, character_level=level)
            }
            for tier, healing, resource in POTION_TIERS:
                assert (healing in ids) == (tier <= level)
                assert (resource in ids) == (tier <= level)


def test_only_permanent_goods_do_not_duplicate_in_fallback(content: GameContent) -> None:
    potion = content.item("small_healing_potion")
    sparse = replace(content, items=(potion,), gear_archetypes=())
    stock = roll_assortment(
        sparse, world_seed="empty-shop", city_id="farhold", rotation=0, character_level=150
    )
    assert stock == (potion,)


def test_future_and_unsellable_permanent_goods_stay_off_the_shelf(content: GameContent) -> None:
    potion = content.item("small_healing_potion")
    unsellable = replace(potion, id="unsellable", rarity="relic")
    future = content.item("full_healing_potion")
    sparse = replace(content, items=(unsellable, future), gear_archetypes=())
    assert not roll_assortment(
        sparse, world_seed="locked-shop", city_id="farhold", rotation=0, character_level=1
    )
