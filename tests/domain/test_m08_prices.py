"""M08.4: редкость один раз, составные обмены и цена сохранённой вещи."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from random import Random

import pytest
from scripts.m08_prices import audit

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import ItemWear
from mmorpg.domain.entities.craft import Recipe, RecipeInput
from mmorpg.domain.rules import crafts, economy, repair, salvage


@pytest.mark.parametrize(
    ("rarity", "price"),
    [("common", 46), ("uncommon", 110), ("rare", 276), ("legendary", 736), ("relic", 2070)],
)
def test_rarity_is_included_once_in_every_service(
    content: GameContent, rarity: str, price: int
) -> None:
    item = content.item(f"sword@1#{rarity}")
    assert item.price == price
    assert economy.buy_price(content, item) == price
    assert economy.repair_price(item.price, item.durability, item.durability) == round(price * 0.15)
    if salvage.can_reforge(content, item):
        assert salvage.reforge_price(content, item) == round(price * 0.35)


def test_tool_price_and_cost_per_gather_are_preserved(content: GameContent) -> None:
    common = content.item("pick@1#common")
    uncommon = content.item("pick@1#uncommon")
    assert (common.price, uncommon.price) == (46, 110)
    assert economy.buy_price(content, common) == 46
    assert economy.buy_price(content, uncommon) == 110
    assert common.price / common.durability == pytest.approx(
        uncommon.price / uncommon.durability, abs=0.02
    )


def test_materials_use_the_explicit_catalogue_cost(content: GameContent) -> None:
    item = content.item("sky_iron")
    assert item.rarity != "common"
    assert economy.buy_price(content, item) == item.price


def test_one_coin_item_cannot_be_bought_and_sold_for_profit(content: GameContent) -> None:
    cheap = replace(content.item("iron_scrap"), price=1)
    # Защита не округляет нулевую безопасную выплату вверх до монеты.
    assert economy.buy_price(content, cheap, modifiers={"shop_price_percent": -1000}) == 1
    assert economy.sell_price(content, cheap, modifiers={"sell_price_percent": 1000}) == 0


def test_fine_refining_and_smithing_cannot_print_gold(content: GameContent) -> None:
    fine = content.craft_rules.quality("fine")
    ore = content.item("iron_scrap")
    hide = content.item("wolf_pelt")
    ingot_recipe = next(recipe for recipe in content.recipes if recipe.id == "smith_ingot@1")
    body_recipe = next(recipe for recipe in content.recipes if recipe.id == "smith_medium_body@1")
    # Самая благоприятная партия: одна руда -> два слитка; один слиток и
    # одна шкура -> редкая кольчуга; лишний слиток остаётся у мастера.
    assert ingot_recipe.output_count + fine.extra == 2
    made = crafts.upgraded(content, body_recipe.output_id, fine, source=Random(96))
    income = economy.sell_price(content, content.item(made), modifiers={"sell_price_percent": 1000})
    income += economy.sell_price(
        content, content.item(ingot_recipe.output_id), modifiers={"sell_price_percent": 1000}
    )
    expense = sum(
        economy.buy_price(content, item, modifiers={"shop_price_percent": -1000})
        for item in (ore, hide)
    )
    assert income <= expense


def test_refining_smithing_salvage_loses_material_value(content: GameContent) -> None:
    # Прежний маршрут выдавал 12 единиц руды из одной руды и одной шкуры.
    item = content.item("medium_body@5#rare")
    materials = salvage.yield_of(content, item, modifiers={"salvage_yield_percent": 1000})
    assert materials == (("iron_scrap", 1),)
    assert economy.recovery_value(content, item) == 3
    assert economy.recovery_value(content, content.item("iron_scrap")) == 2


def test_cheap_work_returns_simpler_material_instead_of_recreating_inputs(
    content: GameContent,
) -> None:
    item = content.item("cloth_hands@24#rare")
    made = salvage.yield_of(content, item, modifiers={"salvage_yield_percent": 1000})
    assert made
    assert content.item(made[0][0]).level < item.level
    assert sum(
        economy.recovery_value(content, content.item(key)) * count for key, count in made
    ) < economy.recovery_value(content, item)


def test_empty_salvage_is_refused_before_consuming_the_thing(content: GameContent) -> None:
    hero = Character(id=1, user_id=1, name="Проба", race_id="human", class_id="warrior")
    item = content.item("cloth_hands@5#rare")
    assert salvage.yield_of(content, item) == ()
    assert "разбирать её незачем" in salvage.can_salvage(content, hero, item)
    assert "Продайте вещь в лавке" in salvage.can_salvage(content, hero, item)


def test_stamp_changes_do_not_change_npc_value(content: GameContent) -> None:
    item = content.item("sword@24#rare~3")
    made = content.item(salvage.reforged(content, item.id, source=Random(96)))
    assert made.id != item.id
    assert made.price == item.price
    assert economy.sell_price(
        content, made, modifiers={"sell_price_percent": 1000}
    ) <= economy.sell_price(content, item, modifiers={"sell_price_percent": 1000})
    assert salvage.reforge_price(content, item) > 0


@pytest.mark.parametrize("level", [1, 24, 140])
@pytest.mark.parametrize("rarity", ["rare", "legendary", "relic"])
def test_instances_and_stamps_share_prices_and_cannot_bypass_bonus_limits(
    content: GameContent, level: int, rarity: str
) -> None:
    template = content.item(f"sword@{level}#{rarity}~17")
    instance = content.item(f"{template.id}!501")
    grouped = content.item(f"{template.id}!501,502")
    minimum = economy.buy_price(
        content, template, charisma=1000, modifiers={"shop_price_percent": -1000}
    )
    maximum = economy.sell_price(content, template, modifiers={"sell_price_percent": 1000})
    for copy in (instance, grouped):
        assert copy.price == template.price
        assert economy.recovery_value(content, copy) == economy.recovery_value(content, template)
        assert economy.sell_price(content, copy, modifiers={"sell_price_percent": 1000}) == maximum
        assert (
            economy.buy_price(content, copy, charisma=1000, modifiers={"shop_price_percent": -1000})
            == minimum
        )
        assert maximum < minimum
        assert salvage.yield_of(
            content, copy, modifiers={"salvage_yield_percent": 1000}
        ) == salvage.yield_of(content, template, modifiers={"salvage_yield_percent": 1000})
        forged_id = salvage.reforged(content, copy.id, source=Random(96))
        assert forged_id.partition("!")[2] == copy.id.partition("!")[2]
        forged = content.item(forged_id)
        assert (
            economy.sell_price(content, forged, modifiers={"sell_price_percent": 1000})
            - salvage.reforge_price(content, copy)
            < maximum
        )
        hero = Character(
            id=1,
            user_id=1,
            name="Проба",
            race_id="human",
            class_id="warrior",
            wear=ItemWear({copy.id: copy.durability}),
        )
        repair_cost = repair.price_of(hero, copy)
        assert 0 < repair_cost < minimum
        assert (
            economy.sell_price(content, copy, modifiers={"sell_price_percent": 1000}) - repair_cost
            < maximum
        )


def test_repair_pays_for_wear_without_increasing_resale(content: GameContent) -> None:
    item = content.item("sword@24#rare")
    hero = Character(id=1, user_id=1, name="Проба", race_id="human", class_id="warrior")
    hero = replace(hero, wear=ItemWear({item.id: item.durability}))
    repair_cost = repair.price_of(hero, item)
    assert repair_cost > 0
    assert repair_cost < economy.buy_price(content, item, modifiers={"shop_price_percent": -1000})
    restored = repair.repaired(hero, (item,))
    assert repair.left(restored, item) == item.durability
    assert economy.sell_price(content, item) - repair_cost < economy.sell_price(content, item)


def test_fractional_output_value_does_not_round_into_free_gold(content: GameContent) -> None:
    recipe = Recipe(
        "test_refine", "smithing", 1, (RecipeInput("iron_scrap", 2),), "crude_ingot", 3, 1
    )
    expanded = replace(content, recipes=(recipe,))
    item = expanded.item("crude_ingot")
    assert economy.recovery_value(expanded, item) == Fraction(1, 2)
    assert economy.sell_price(expanded, item, modifiers={"sell_price_percent": 1000}) == 0


def test_cyclic_recipe_catalogue_cannot_bootstrap_a_payout(content: GameContent) -> None:
    first = Recipe(
        "cycle_a",
        "smithing",
        1,
        (RecipeInput("wolf_pelt", 1), RecipeInput("dry_branch", 1)),
        "iron_scrap",
        1,
        1,
    )
    second = Recipe("cycle_b", "smithing", 1, (RecipeInput("iron_scrap", 1),), "wolf_pelt", 1, 1)
    expanded = replace(content, recipes=(first, second))
    assert economy.recovery_value(expanded, expanded.item("iron_scrap")) == 0
    assert economy.recovery_value(expanded, expanded.item("wolf_pelt")) == 0


def test_entire_exchange_graph_has_no_profitable_composite_cycle(content: GameContent) -> None:
    result = audit(content)
    assert result.direct_checks == len(content.items)
    assert result.craft_checks == len(content.recipes) * len(content.craft_rules.qualities)
    assert result.salvage_checks > 4000
    assert result.reforge_checks > 3000
    assert result.violations == ()
