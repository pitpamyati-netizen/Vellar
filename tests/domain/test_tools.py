"""Инструмент: без него сырьё не берут, и берут его не всё (ADR 0056)."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import ItemWear
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.location import LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules import economy as shop_rules
from mmorpg.domain.rules import tools as tool_rules

PICK = "pick@1#common"
SICKLE = "sickle@1#common"
STOUT_PICK = "pick@1#rare"


@pytest.fixture
def miner(content: GameContent) -> Character:
    """Тот, у кого в слоте инструмента кирка, а в горном деле кое-что записано."""
    bare = Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        level=10,
        crafts=CraftLog(MappingProxyType({"mining": CraftProgress(experience=250)})),
    )
    return replace(bare, equipment=bare.equipment.equip(tool_rules.TOOL_SLOT, PICK))


def vein(name: str = "Жила", level: int = 3) -> LocationNode:
    return LocationNode(index=4, kind=NodeKind.GATHER, name=name, level=level, links=(1,))


def seed(*parts: str | int) -> bytes:
    return derive("vellar-test", *parts)


# --- отказы -----------------------------------------------------------


def test_an_empty_slot_refuses_and_says_where_to_buy_one(
    content: GameContent, miner: Character
) -> None:
    barehanded = replace(miner, equipment=miner.equipment.unequip(tool_rules.TOOL_SLOT))
    refused = tool_rules.refusal(content, barehanded)
    assert "Собирать нечем" in refused
    assert "лавке" in refused


def test_a_pick_does_not_take_herbs(content: GameContent, miner: Character) -> None:
    assert tool_rules.refusal(content, miner, "руда") == ""
    refused = tool_rules.refusal(content, miner, "травы")
    assert "серп" in refused.casefold()


def test_a_vein_that_names_nothing_is_worked_by_any_tool(
    content: GameContent, miner: Character
) -> None:
    assert tool_rules.refusal(content, miner, "") == ""


def test_a_tool_that_is_gone_from_the_content_is_an_empty_slot(
    content: GameContent, miner: Character
) -> None:
    """Сохранённому состоянию не верят: вещи может уже не быть."""
    stale = replace(miner, equipment=miner.equipment.equip(tool_rules.TOOL_SLOT, "basket@1#common"))
    assert tool_rules.tool_of(content, stale) is None
    assert "Собирать нечем" in tool_rules.refusal(content, stale)


def test_a_thing_that_is_not_a_tool_takes_nothing(content: GameContent) -> None:
    """Меч в слоте инструмента - не инструмент, и отвечает об этом молча."""
    sword = content.item("sword@1#common")
    assert tool_rules.limit(sword) == 0
    assert tool_rules.sources_of(content, sword) == ()
    assert tool_rules.craft_of(content, sword) == ""
    assert tool_rules.type_name(content, sword) == "инструмент"
    assert not tool_rules.takes(content, sword, "руда")


# --- прочность --------------------------------------------------------


def test_rarity_decides_how_long_a_tool_lasts(content: GameContent) -> None:
    plain = content.item(PICK)
    stout = content.item(STOUT_PICK)
    assert tool_rules.limit(plain) == content.rarity("common").durability
    assert tool_rules.limit(stout) > tool_rules.limit(plain)
    assert not plain.stat_bonuses and not plain.modifiers, "a tool is numbers-free but for wear"


def test_work_wears_the_tool_down_and_the_last_stroke_breaks_it(
    content: GameContent, miner: Character
) -> None:
    item = content.item(PICK)
    limit = tool_rules.limit(item)
    worn, broken = tool_rules.wear(content, miner, item)
    assert not broken
    assert tool_rules.left(content, worn, item) == limit - 1

    tired = replace(miner, wear=ItemWear(MappingProxyType({PICK: limit - 1})))
    spent, broken = tool_rules.wear(content, tired, item)
    assert broken
    assert spent.equipment.item_in(tool_rules.TOOL_SLOT) is None
    assert spent.wear.spent(PICK) == 0, "a broken tool takes its record with it"


def test_a_spent_tool_refuses_before_it_is_taken_off(
    content: GameContent, miner: Character
) -> None:
    limit = tool_rules.limit(content.item(PICK))
    spent = replace(miner, wear=ItemWear(MappingProxyType({PICK: limit})))
    assert "сточен" in tool_rules.refusal(content, spent)


# --- жила -------------------------------------------------------------


def test_a_vein_pays_in_material_records_the_craft_and_wears_the_tool(
    content: GameContent, miner: Character
) -> None:
    result = adventure.resolve_search(
        content,
        miner,
        vein(),
        seed("gather", 1),
        tool=content.item(PICK),
        biomes=frozenset({"горы"}),
    )
    assert result.item_id
    assert content.item(result.item_id).source in {"руда", "обломки"}
    assert result.count >= content.craft_rules.gather_base
    assert result.craft_id == "mining"
    assert result.character.crafts.progress("mining").experience > 250
    assert result.tool_left == tool_rules.limit(content.item(PICK)) - 1


def test_the_tool_decides_what_a_nameless_vein_gives(
    content: GameContent, miner: Character
) -> None:
    """Одна и та же жила отдаёт руду киркой и травы серпом."""
    with_pick = adventure.resolve_search(
        content, miner, vein(), seed("gather", 2), tool=content.item(PICK)
    )
    with_sickle = adventure.resolve_search(
        content, miner, vein(), seed("gather", 2), tool=content.item(SICKLE)
    )
    assert content.item(with_pick.item_id).source in {"руда", "обломки"}
    assert content.item(with_sickle.item_id).source in {"травы", "волокно"}
    assert with_sickle.craft_id == "herbalism"


def test_a_vein_that_names_its_source_keeps_it(content: GameContent, miner: Character) -> None:
    """«Заросли» отдают травы, чем бы игрок ни пришёл, - и потому просят серп."""
    assert adventure.GATHER_SOURCES["Заросли"] == "травы"
    result = adventure.resolve_search(
        content, miner, vein("Заросли"), seed("gather", 3), tool=content.item(SICKLE)
    )
    assert content.item(result.item_id).source == "травы"


def test_without_a_tool_a_vein_gives_nothing(content: GameContent, miner: Character) -> None:
    result = adventure.resolve_search(content, miner, vein(), seed("gather", 4))
    assert result.item_id == ""
    assert result.count == 0
    assert result.craft_experience == 0


def test_the_last_stroke_is_told_in_the_result(content: GameContent, miner: Character) -> None:
    limit = tool_rules.limit(content.item(PICK))
    tired = replace(miner, wear=ItemWear(MappingProxyType({PICK: limit - 1})))
    result = adventure.resolve_search(
        content, tired, vein(), seed("gather", 5), tool=content.item(PICK)
    )
    assert result.tool_broken
    assert result.tool_left == 0
    assert result.character.equipment.item_in(tool_rules.TOOL_SLOT) is None


def test_a_cache_needs_no_tool_and_pays_in_gold(content: GameContent, miner: Character) -> None:
    """Инструмент нужен жиле, а не тайнику: обыскать - не то же, что выломать."""
    node = replace(vein(), kind=NodeKind.CACHE, name="Тайник")
    result = adventure.resolve_search(content, miner, node, seed("cache", 1))
    assert result.gold > 0
    assert not result.tool_broken


# --- лавка ------------------------------------------------------------


def test_the_shop_always_has_a_tool_for_every_gathering_craft(content: GameContent) -> None:
    """Сточенную кирку меняют там, где стоят: полка не зависит от сида."""
    crafts = {kind.craft for kind in content.tool_types}
    for rotation in range(6):
        stock = shop_rules.roll_assortment(
            content,
            world_seed="vellar-test",
            city_id="farhold",
            rotation=rotation,
            character_level=8,
            strain=1.0,
        )
        offered = {content.tool_type(item.tool_type).craft for item in stock if item.is_tool}
        assert offered == crafts
        assert len(stock) == len({item.id for item in stock}), "no item is listed twice"
