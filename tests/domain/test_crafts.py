"""Ремёсла: ранги, заработанные работой, что лежит в земле и что даёт партия."""

from __future__ import annotations

from dataclasses import replace
from random import Random
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.craft import CraftKind, CraftLog, CraftProgress
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import crafts as craft_rules


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


# --- сбор -------------------------------------------------------------


def test_a_higher_rank_gathers_more(content: GameContent, miner: Character) -> None:
    """Место решает, что лежит; ремесло - сколько его вынесли."""
    novice = replace(miner, crafts=CraftLog())
    master = replace(
        miner, crafts=CraftLog(MappingProxyType({"mining": CraftProgress(experience=10_000)}))
    )
    small = craft_rules.gather_amount(content, novice, "mining")
    large = craft_rules.gather_amount(content, master, "mining")
    assert small >= content.craft_rules.gather_base
    assert large > small


def test_what_lies_in_the_ground_depends_on_where_it_is(content: GameContent) -> None:
    """Одна и та же жила в горах и на болоте отдаёт разное."""
    stony = craft_rules.yields_here(content, level=20, biomes=frozenset({"горы"}))
    boggy = craft_rules.yields_here(content, level=20, biomes=frozenset({"болото"}))
    assert stony and boggy
    assert set(stony) != set(boggy)
    assert "mountain_ore" in stony
    assert "bog_iron" in boggy


def test_a_tool_narrows_the_ground_to_what_it_takes(content: GameContent) -> None:
    """Киркой не срезают траву: сырьё сужается тем, чем его берут."""
    ore = craft_rules.yields_here(content, level=20, sources=("руда", "обломки"))
    herbs = craft_rules.yields_here(content, level=20, sources=("травы",))
    assert ore and herbs
    assert not set(ore) & set(herbs)
    assert all(content.item(item_id).source in {"руда", "обломки"} for item_id in ore)


def test_the_ground_holds_only_what_the_level_allows(content: GameContent) -> None:
    early = craft_rules.yields_here(content, level=1)
    late = craft_rules.yields_here(content, level=300)
    assert set(early) < set(late)
    assert all(content.item(item_id).level <= 1 for item_id in early)


def test_every_gathered_material_names_its_craft(content: GameContent) -> None:
    """Работа записывается в то ремесло, которому находка принадлежит."""
    for item_id in craft_rules.yields_here(content, level=300):
        assert craft_rules.craft_of_source(content, item_id)
    assert craft_rules.craft_of_source(content, "whetstone") == ""


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
    """На многих партиях всплывает каждая ступень, и лучшая стоит больше."""
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


def test_good_work_on_gear_comes_out_a_grade_rarer(content: GameContent, miner: Character) -> None:
    """Качество платит самой вещью: ладная кольчуга выходит редкостью выше рецепта.

    Лишней штукой качество платит только тому, у чего редкости нет вовсе - зельям
    и точильным камням (ADR 0060).
    """
    recipe = next(
        one
        for one in content.recipes_of("smithing")
        if gear_procgen.parse_gear_id(one.output_id) is not None
    )
    written = gear_procgen.parse_gear_id(recipe.output_id)
    assert written is not None
    ladder = [rarity.id for rarity in content.rarities]
    owned = {need.item_id: need.count * 3 for need in recipe.inputs}

    seen: dict[str, str] = {}
    for index in range(400):
        _, result = craft_rules.make(content, miner, recipe, owned, seed=seed("craft", index))
        assert result.quality is not None
        made = gear_procgen.parse_gear_id(result.item_id)
        assert made is not None
        assert made[:2] == written[:2], "вид и ступень берутся из рецепта"
        assert result.count == recipe.output_count, "снаряжению лишней штуки не дают"
        step = ladder.index(made[2]) - ladder.index(written[2])
        assert step == result.quality.rarity_step
        seen[result.quality.id] = result.item_id
    assert set(seen) == {"plain", "good", "fine"}


def test_a_relic_is_never_forged(content: GameContent, miner: Character) -> None:
    """Реликтовое берут с хозяина логова, а не куют: лестница качества туда не ведёт."""
    relics = {rarity.id for rarity in content.rarities if rarity.scaling}
    top = content.craft_rules.quality("fine")
    for rarity in content.rarities:
        made = craft_rules.upgraded(content, f"sword@24#{rarity.id}", top, source=Random(1))
        parsed = gear_procgen.parse_gear_id(made)
        assert parsed is not None
        if rarity.id not in relics:
            assert parsed[2] not in relics


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
