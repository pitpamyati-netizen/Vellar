"""Crafts: ranks earned by work, one gathering per watch, and what a batch gives."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.craft import CraftKind, CraftLog, CraftProgress
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import crafts as craft_rules

NOW = 1_700_000_000
COOLDOWN = 900


@pytest.fixture
def miner(content: GameContent) -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        level=10,
        crafts=CraftLog(
            MappingProxyType(
                {
                    "mining": CraftProgress(experience=250),
                    "smithing": CraftProgress(experience=140),
                }
            )
        ),
    )


def seed(*parts: str | int) -> bytes:
    return derive("vellar-test", *parts)


# --- ranks ------------------------------------------------------------


def test_a_rank_is_counted_back_from_the_work_done(content: GameContent) -> None:
    rules = content.craft_rules
    assert craft_rules.rank_of(rules, 0) == 1
    assert craft_rules.rank_of(rules, rules.experience_per_rank - 1) == 1
    assert craft_rules.rank_of(rules, rules.experience_per_rank) == 2
    assert craft_rules.rank_of(rules, rules.experience_per_rank * 99) == rules.max_rank


def test_the_last_rank_has_nothing_left_to_fill(content: GameContent) -> None:
    rules = content.craft_rules
    assert craft_rules.into_rank(rules, 10) == (10, rules.experience_per_rank)
    assert craft_rules.into_rank(rules, rules.experience_per_rank * 99) == (0, 0)


def test_every_rank_has_a_name(content: GameContent) -> None:
    rules = content.craft_rules
    names = {craft_rules.rank_name(rules, rank) for rank in range(1, rules.max_rank + 1)}
    assert len(names) == rules.max_rank


# --- gathering --------------------------------------------------------


def test_gathering_brings_back_a_material_and_records_the_work(
    content: GameContent, miner: Character
) -> None:
    craft = content.craft("mining")
    worked, result = craft_rules.gather(
        content, miner, craft, now=NOW, cooldown=COOLDOWN, seed=seed("gather", 1)
    )
    assert result.ok
    assert result.item_id in {entry.item_id for entry in craft.yields}
    assert result.count >= content.craft_rules.gather_base
    assert worked.crafts.progress("mining").experience > miner.crafts.progress("mining").experience
    assert worked.crafts.progress("mining").gathered_at == NOW


def test_a_second_gathering_inside_the_cooldown_is_refused(
    content: GameContent, miner: Character
) -> None:
    craft = content.craft("mining")
    worked, first = craft_rules.gather(
        content, miner, craft, now=NOW, cooldown=COOLDOWN, seed=seed("gather", 1)
    )
    _, second = craft_rules.gather(
        content, worked, craft, now=NOW, cooldown=COOLDOWN, seed=seed("gather", 2)
    )
    assert first.ok
    assert not second.ok
    assert "Следующий сбор через" in second.refused

    _, later = craft_rules.gather(
        content, worked, craft, now=NOW + COOLDOWN + 1, cooldown=COOLDOWN, seed=seed("gather", 3)
    )
    assert later.ok, "the road refills once the cooldown is out"


def test_a_higher_rank_gathers_more(content: GameContent, miner: Character) -> None:
    craft = content.craft("mining")
    novice = replace(miner, crafts=CraftLog())
    _, small = craft_rules.gather(
        content, novice, craft, now=NOW, cooldown=COOLDOWN, seed=seed("g", 1)
    )
    master = replace(
        miner,
        crafts=CraftLog(
            MappingProxyType({"mining": CraftProgress(experience=10_000)}),
        ),
    )
    _, large = craft_rules.gather(
        content, master, craft, now=NOW, cooldown=COOLDOWN, seed=seed("g", 1)
    )
    assert large.count > small.count


def test_a_making_craft_gathers_nothing(content: GameContent, miner: Character) -> None:
    _, result = craft_rules.gather(
        content, miner, content.craft("smithing"), now=NOW, cooldown=COOLDOWN, seed=seed("g", 1)
    )
    assert not result.ok


def test_a_level_below_every_yield_is_told_so(content: GameContent, miner: Character) -> None:
    """A craft whose materials all start higher up says so instead of paying out."""
    deep = replace(
        content.craft("mining"),
        yields=tuple(entry for entry in content.craft("mining").yields if entry.level > 1),
    )
    assert deep.yields, "mining is expected to have a material above level one"
    novice = replace(miner, level=1, crafts=CraftLog())
    _, result = craft_rules.gather(
        content, novice, deep, now=NOW, cooldown=COOLDOWN, seed=seed("g", 1)
    )
    assert not result.ok
    assert "уровня" in result.refused


# --- making -----------------------------------------------------------


def test_a_batch_spends_the_materials_and_pays_in_items(
    content: GameContent, miner: Character
) -> None:
    recipe = content.recipes_of("smithing")[0]
    owned = {need.item_id: need.count * 3 for need in recipe.inputs}
    worked, result = craft_rules.make(content, miner, recipe, owned, seed=seed("craft", 1))
    assert result.ok
    assert result.item_id == recipe.output_id
    assert result.count >= recipe.output_count
    assert all(count < 0 for _, count in result.spent)
    assert worked.crafts.progress("smithing").experience == 140 + recipe.experience


def test_a_batch_without_materials_is_refused_by_name(
    content: GameContent, miner: Character
) -> None:
    recipe = content.recipes_of("smithing")[0]
    _, result = craft_rules.make(content, miner, recipe, {}, seed=seed("craft", 1))
    assert not result.ok
    assert content.item(recipe.inputs[0].item_id).name in result.refused


def test_a_recipe_above_the_rank_is_refused(content: GameContent, miner: Character) -> None:
    hard = max(content.recipes_of("smithing"), key=lambda recipe: recipe.rank)
    novice = replace(miner, crafts=CraftLog())
    owned = {need.item_id: need.count * 3 for need in hard.inputs}
    _, result = craft_rules.make(content, novice, hard, owned, seed=seed("craft", 1))
    assert not result.ok
    assert "ранг" in result.refused


def test_quality_decides_what_comes_out(content: GameContent, miner: Character) -> None:
    """Over many batches every tier shows up, and a better one is worth more."""
    recipe = content.recipes_of("smithing")[0]
    owned = {need.item_id: need.count * 3 for need in recipe.inputs}
    seen = {
        craft_rules.make(content, miner, recipe, owned, seed=seed("craft", index))[1]
        for index in range(400)
    }
    tiers = {result.quality.id for result in seen if result.quality is not None}
    assert tiers == {"plain", "good", "fine"}
    for result in seen:
        assert result.quality is not None
        assert result.count == recipe.output_count + result.quality.extra
        if result.quality.refund_percent:
            spent = dict(result.spent)
            assert any(abs(spent[need.item_id]) < need.count for need in recipe.inputs)


def test_the_same_seed_makes_the_same_batch(content: GameContent, miner: Character) -> None:
    recipe = content.recipes_of("alchemy")[0]
    owned = {need.item_id: 10 for need in recipe.inputs}
    first = craft_rules.make(content, miner, recipe, owned, seed=seed("craft", 7))[1]
    second = craft_rules.make(content, miner, recipe, owned, seed=seed("craft", 7))[1]
    assert first == second


def test_crafts_split_into_gathering_and_making(content: GameContent) -> None:
    gathering = content.crafts_of_kind(CraftKind.GATHERING)
    making = content.crafts_of_kind(CraftKind.MAKING)
    assert gathering and making
    assert len(gathering) + len(making) == len(content.crafts)
