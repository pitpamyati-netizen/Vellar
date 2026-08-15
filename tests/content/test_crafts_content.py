"""``content/crafts.toml``: what the world can make, and out of what."""

from __future__ import annotations

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.content import ItemKind
from mmorpg.domain.entities.craft import CraftKind


def test_gathering_crafts_bring_back_materials_that_exist(content: GameContent) -> None:
    for craft in content.crafts_of_kind(CraftKind.GATHERING):
        assert craft.yields, craft.id
        for produced in craft.yields:
            item = content.item(produced.item_id)
            assert item.kind is ItemKind.MATERIAL, f"{craft.id} gathers {item.id}, not a material"
            assert produced.level >= 1


def test_every_gathering_craft_pays_from_the_first_level(content: GameContent) -> None:
    """Nobody arrives in Vellar too junior to work: rank one always has something."""
    for craft in content.crafts_of_kind(CraftKind.GATHERING):
        assert min(produced.level for produced in craft.yields) == 1, craft.id


def test_recipes_open_from_rank_one_upwards(content: GameContent) -> None:
    for craft in content.crafts_of_kind(CraftKind.MAKING):
        ranks = [recipe.rank for recipe in content.recipes_of(craft.id)]
        assert ranks, craft.id
        assert min(ranks) == 1, f"{craft.id} has nothing to make at rank one"
        assert ranks == sorted(ranks), f"{craft.id} lists recipes out of order"
        assert max(ranks) <= content.craft_rules.max_rank


def test_a_recipe_pays_for_itself(content: GameContent) -> None:
    """Making a thing must beat selling its materials, or the craft is a trap."""
    for recipe in content.recipes:
        materials = sum(content.item(need.item_id).price * need.count for need in recipe.inputs)
        made = content.item(recipe.output_id).price * recipe.output_count
        assert made > materials, f"{recipe.id} is worth less than what goes into it"
        assert recipe.experience > 0, recipe.id


def test_craft_names_are_read_out_loud(content: GameContent) -> None:
    for craft in content.crafts:
        assert craft.name == craft.name.strip()
        assert craft.description.endswith("."), craft.id
        assert len(craft.description) <= 120, craft.id


def test_quality_pays_more_the_better_it_is(content: GameContent) -> None:
    rules = content.craft_rules
    plain, good, fine = (rules.quality(name) for name in ("plain", "good", "fine"))
    assert plain.extra == 0
    assert plain.refund_percent == 0
    assert good.extra >= plain.extra
    assert fine.extra + fine.refund_percent > good.extra + good.refund_percent
    assert rules.fine_chance_base < rules.good_chance_base
