"""Умный лут: чем находка отличается от случайной вещи (ADR 0071).

Проверяется то, ради чего слой заведён: находка тянется к классу победителя, но
не целиком; порог рода стоит денег и не наказывает за своё; именной аффикс лежит
на вещи и работает у своего.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import milestones as milestone_rules
from mmorpg.domain.rules import modifiers as mods

TRIES = 3000


def hero(class_id: str, *, level: int = 76, **allocated: int) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        allocated=StatBlock(**allocated),
    )


def _share(content: GameContent, class_id: str, seed: int = 7) -> float:
    """Какая доля находок оказалась своей для этого класса."""
    source = random.Random(seed)
    counted: Counter[bool] = Counter()
    for _ in range(TRIES):
        found = gear_procgen.roll_drop(
            content, source, level=76, rank=EnemyRank.BOSS, class_ids=(class_id,)
        )
        if found is None:
            continue
        parsed = gear_procgen.parse_gear_id(found)
        assert parsed is not None
        archetype = content.gear_archetype(parsed[0])
        counted[gear_procgen.suits(content, archetype, (class_id,))] += 1
    return counted[True] / max(1, sum(counted.values()))


# --- находка ----------------------------------------------------------


@pytest.mark.parametrize("class_id", ["warrior", "mage", "rogue", "cleric"])
def test_most_of_what_falls_is_yours(content: GameContent, class_id: str) -> None:
    """Объявленная доля — это обещание, и оно проверяется делом."""
    promised = content.loot_rules.class_share
    assert _share(content, class_id) == pytest.approx(promised, abs=0.05)


@pytest.mark.parametrize("class_id", ["warrior", "mage"])
def test_but_not_all_of_it(content: GameContent, class_id: str) -> None:
    """Чужое держится нарочно: его перековывают или отдают соратнику.

    Находка, которую некуда деть, — мусор; находка, которую есть куда деть, —
    выбор. Полная доля своего убила бы и торговлю, и кузницу.
    """
    assert _share(content, class_id) < 1.0


def test_a_party_widens_what_counts_as_its_own(content: GameContent) -> None:
    """В отряде своим считается то, что подходит хоть кому-то из победивших."""
    source = random.Random(11)
    alone = {
        gear_procgen.parse_gear_id(found)[0]  # type: ignore[index]
        for _ in range(TRIES)
        if (
            found := gear_procgen.roll_drop(
                content, source, level=76, rank=EnemyRank.BOSS, class_ids=("mage",)
            )
        )
        is not None
    }
    source = random.Random(11)
    together = {
        gear_procgen.parse_gear_id(found)[0]  # type: ignore[index]
        for _ in range(TRIES)
        if (
            found := gear_procgen.roll_drop(
                content, source, level=76, rank=EnemyRank.BOSS, class_ids=("mage", "warrior")
            )
        )
        is not None
    }
    warrior_kinds = {
        one
        for one in together
        if gear_procgen.suits(content, content.gear_archetype(one), ("warrior",))
    }
    assert warrior_kinds
    assert len(together) >= len(alone)


def test_nobody_named_means_nothing_is_pulled(content: GameContent) -> None:
    """Без названных классов находка падает ровно как падала."""
    assert _share(content, "") == pytest.approx(_share(content, "", seed=9), abs=0.2)


# --- порог рода -------------------------------------------------------


def test_a_class_always_reaches_the_threshold_of_its_own(content: GameContent) -> None:
    """Порог не наказывает за своё: иначе он наказывал бы за класс.

    Булаву носят и воин, и жрец, но силу растит первый, а мудрость — второй,
    поэтому порог берётся любой из названных характеристик.
    """
    for klass in content.classes:
        who = hero(klass.id, level=76)
        points = content.rules.free_points_at_creation + content.rules.stat_points_per_level * 75
        keys = klass.key_stats or (StatCode.STR,)
        block = StatBlock()
        for index in range(points):
            block = block.with_change(keys[index % len(keys)], 1)
        who = replace(who, allocated=block)
        invested = milestone_rules.invested_stats(content, who)
        for kind in (*klass.weapon_types, *klass.armor_types):
            asked = content.requirement_for(kind)
            if asked is None:
                continue
            assert asked.shortfall(invested, 76) == 0, f"{klass.id} cannot carry its own {kind}"


def test_foreign_gear_costs_on_top_of_being_foreign(content: GameContent) -> None:
    """Разбойник в латах платит и за чужой род, и за недобранную силу."""
    thief = hero("rogue", level=76, AGI=300)
    plated = replace(thief, equipment=thief.equipment.equip("body", "heavy_body@76#common"))
    cost = gear.proficiency_penalty(content, plated)
    assert cost["accuracy_percent"] < gear.FOREIGN_ARMOR_ACCURACY
    assert cost["initiative_percent"] < gear.FOREIGN_ARMOR_INITIATIVE


def test_the_deeper_the_shortfall_the_dearer(content: GameContent) -> None:
    """«Не хватает одного» и «не хватает сорока» — разные вещи."""
    barely = hero("rogue", level=76, AGI=300, STR=88)
    hardly = hero("rogue", level=76, AGI=300)
    slot = "body"
    barely = replace(barely, equipment=barely.equipment.equip(slot, "heavy_body@76#common"))
    hardly = replace(hardly, equipment=hardly.equipment.equip(slot, "heavy_body@76#common"))
    assert (
        gear.proficiency_penalty(content, hardly)["accuracy_percent"]
        < gear.proficiency_penalty(content, barely)["accuracy_percent"]
    )


def test_a_requirement_is_a_price_and_not_a_ban(content: GameContent) -> None:
    """Вещь надевается всегда: цена названа, отказа нет."""
    thief = hero("rogue", level=76, AGI=300)
    item = content.item("heavy_body@76#common")
    assert gear.requirement_shortfall(content, item, milestone_rules.invested_stats(content, thief))
    assert gear.equip_warning(content, thief, item)


# --- именной аффикс ---------------------------------------------------


def test_a_named_affix_lies_on_the_item_not_on_the_finder(content: GameContent) -> None:
    """Вещь выводится из своего имени и ни от кого не зависит (ADR 0059)."""
    once = content.item("sword@76#legendary")
    twice = content.item("sword@76#legendary")
    assert once.class_affix_id == twice.class_affix_id


def test_a_named_affix_only_fits_a_class_that_wears_the_kind(content: GameContent) -> None:
    """Меч не вправе нести именной аффикс мага: маг меча не носит вовсе."""
    for level in (46, 76, 140):
        for named in content.class_affixes_for(("sword",), level):
            klass = content.character_class(named.class_id)
            assert klass.can_wield("sword"), named.id


def test_an_ordinary_item_carries_no_named_affix(content: GameContent) -> None:
    """Именное — у особой вещи, и только с объявленной ступени."""
    assert content.item("sword@76#common").class_affix_id == ""
    assert content.item("sword@1#legendary").class_affix_id == ""


def test_a_named_affix_works_for_its_own_and_is_silent_for_others(content: GameContent) -> None:
    """Разбойник, поднявший воинскую легендарку, не получает от неё ничего."""
    named = next(
        (one for one in content.class_affixes if one.class_id == "warrior"),
        None,
    )
    assert named is not None
    item_id = next(
        (
            one.id
            for level in (46, 76, 140)
            for kind in ("sword", "greatsword", "heavy_body")
            for one in (content.item(f"{kind}@{level}#legendary"),)
            if one.class_affix_id == named.id
        ),
        "",
    )
    if not item_id:
        pytest.skip("на этих ступенях этот аффикс не выпал")
    slot = content.item(item_id).slot
    fighter = replace(
        hero("warrior", level=140, STR=400),
        equipment=hero("warrior").equipment.equip(slot, item_id),
    )
    thief = replace(
        hero("rogue", level=140, AGI=400),
        equipment=hero("rogue").equipment.equip(slot, item_id),
    )
    assert mods.collect_modifiers(content, fighter).get(named.key, 0.0) != 0.0
    theirs = mods.equipment_modifiers(content, (item_id,), 140, "rogue")
    assert theirs.get(named.key, 0.0) == 0.0
    assert thief.class_id == "rogue"


def test_every_class_has_a_named_affix_of_its_own(content: GameContent) -> None:
    """Класс без именного — класс, чья легендарка ничем не отличается от чужой."""
    named = {one.class_id for one in content.class_affixes}
    assert named == {klass.id for klass in content.classes}
