"""Каждый город продаёт свою полосу, у каждой лавки свой товар."""

from dataclasses import replace

import pytest

from mmorpg.domain.rules.economy import roll_assortment


def stock(content, city_id="farhold", shop_id="", level=150, **kwargs):
    return roll_assortment(
        content,
        world_seed="city-shops",
        city_id=city_id,
        rotation=kwargs.pop("rotation", 0),
        character_level=level,
        shop_id=shop_id,
        **kwargs,
    )


@pytest.mark.parametrize("rotation", [0, 1, 7, 1000])
@pytest.mark.parametrize("reputation,strain", [(0.0, 0.0), (0.0, 1.0), (400.0, 1.0)])
def test_every_city_has_only_its_equipment_range_and_no_duplicates(
    content, rotation, reputation, strain
):
    for city in content.cities:
        goods = stock(content, city.id, rotation=rotation, reputation=reputation, strain=strain)
        assert goods
        assert len({i.id for i in goods}) == len(goods)
        assert all(city.level_min <= i.level <= city.level_max for i in goods if i.is_equipment)
        assert all(i.level <= city.level_max for i in goods)
        assert all(content.rarity(i.rarity).weight > 0 for i in goods)
        assert not any(content.rarity(i.rarity).boss_only for i in goods)


def test_every_shop_is_populated_and_has_only_its_own_goods(content):
    for city in content.cities:
        combined = set()
        for shop in content.shops:
            goods = stock(content, city.id, shop.id)
            assert goods, (city.id, shop.id)
            assert all(shop.sells(i) for i in goods)
            assert not combined.intersection(i.id for i in goods)
            combined.update(i.id for i in goods)
        assert combined == {i.id for i in stock(content, city.id)}


def test_level_and_reputation_cannot_move_a_city_to_another_equipment_tier(content):
    for city in content.cities:
        baseline = stock(content, city.id, level=1)
        assert stock(content, city.id, level=150) == baseline
        assert stock(content, city.id, level=75) == baseline
        trusted = stock(content, city.id, reputation=10000)
        assert all(city.level_min <= i.level <= city.level_max for i in trusted if i.is_equipment)
    assert stock(content, "farhold") != stock(content, "last_beacon")


def test_every_class_equipment_type_and_every_tool_have_a_permanent_common_option(content):
    for city in content.cities:
        goods = stock(content, city.id, strain=1.0)
        for archetype in content.gear_archetypes:
            assert any(i.id.startswith(f"{archetype.id}@") and i.rarity == "common" for i in goods)
        for tool in content.tool_types:
            assert {i.rarity for i in goods if i.tool_type == tool.id} >= {"common", "uncommon"}


def test_consumable_and_material_families_keep_the_city_tiers_in_every_rotation(content):
    for city in content.cities:
        for shop in content.shops:
            expected = set()
            for family in shop.families:
                items = [content.item(i) for i in family]
                lower = max((i.level for i in items if i.level <= city.level_min), default=0)
                expected.update(i.id for i in items if lower <= i.level <= city.level_max)
            for rotation in (0, 1, 11):
                goods = stock(content, city.id, shop.id, rotation=rotation, strain=1.0)
                assert {i.id for i in goods if not i.is_equipment} == expected


def test_sparse_catalogue_never_falls_back_to_equipment_from_another_city(content):
    item = content.item("sword@140#common")
    assert not stock(replace(content, items=(item,)), "farhold")


def test_only_permanent_goods_do_not_duplicate(content):
    potion = content.item("small_healing_potion")
    assert stock(replace(content, items=(potion,))) == (potion,)


def test_unsellable_and_future_family_goods_stay_off_the_shelf(content):
    potion = replace(content.item("small_healing_potion"), rarity="relic")
    future = content.item("full_healing_potion")
    assert not stock(replace(content, items=(potion, future)))


def test_unknown_shop_or_city_without_trade_cannot_offer_goods(content):
    assert not stock(content, shop_id="missing")
    closed = replace(content.city("farhold"), services=())
    changed = replace(content, cities=(closed,), _cities_by_id={closed.id: closed})
    assert not stock(changed)
