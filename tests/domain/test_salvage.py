"""Разбор и перековка: что кузница делает с вещью, кроме починки (ADR 0060)."""

from __future__ import annotations

from random import Random
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import Equipment
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import economy, salvage


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=24,
        gold=5000,
        equipment=Equipment(MappingProxyType({"weapon": "sword@24#common"})),
    )


# --- разбор ----------------------------------------------------------


def test_a_thing_falls_apart_into_what_it_was_made_of(content: GameContent) -> None:
    """Железо в железо, кожа в кожу: род вещи решает, что из неё выйдет."""
    steel = salvage.yield_of(content, content.item("heavy_body@24#common"))
    hide = salvage.yield_of(content, content.item("light_body@24#common"))
    assert steel and hide
    assert content.item(steel[0][0]).source == "руда"
    assert content.item(hide[0][0]).source == "шкуры"


def test_the_deeper_the_thing_the_better_the_material(content: GameContent) -> None:
    """Ступень вещи решает, какое сырьё из неё выйдет: у мелочи оно простое."""
    shallow = salvage.yield_of(content, content.item("sword@1#common"))
    deep = salvage.yield_of(content, content.item("sword@24#common"))
    assert shallow and deep
    assert content.item(deep[0][0]).price >= content.item(shallow[0][0]).price


def test_taking_a_thing_apart_pays_less_than_selling_it(content: GameContent) -> None:
    """Разбирают ради сырья, а не ради золота: скупка платит больше."""
    for rarity in ("common", "uncommon", "rare", "legendary"):
        item = content.item(f"sword@24#{rarity}")
        made = salvage.yield_of(content, item)
        assert made
        worth = sum(content.item(item_id).price * count for item_id, count in made)
        assert worth < economy.sell_price(content, item), rarity


def test_a_rarer_thing_gives_more_material(content: GameContent) -> None:
    plain = salvage.yield_of(content, content.item("sword@24#common"))[0][1]
    rare = salvage.yield_of(content, content.item("sword@24#rare"))[0][1]
    assert rare > plain
    assert rare <= salvage.SALVAGE_MAX


def test_what_is_worn_or_not_gear_is_refused_by_name(content: GameContent, hero: Character) -> None:
    assert "надета" in salvage.can_salvage(content, hero, content.item("sword@24#common"))
    assert not salvage.can_salvage(content, hero, content.item("sword@24#rare"))
    tool = next(item for item in content.items if item.is_tool and item.level == 24)
    assert "нструмент" in salvage.can_salvage(content, hero, tool)
    assert "снаряжение" in salvage.can_salvage(content, hero, content.item("wolf_pelt"))


def test_the_salvage_key_is_finally_read(content: GameContent) -> None:
    """``salvage_yield_percent`` обещали давно; здесь его наконец считают."""
    item = content.item("sword@24#rare")
    plain = salvage.yield_of(content, item)[0][1]
    lucky = salvage.yield_of(content, item, modifiers={salvage.SALVAGE_YIELD_KEY: 50.0})[0][1]
    assert lucky > plain


# --- перековка -------------------------------------------------------


def test_reforging_keeps_the_thing_and_changes_its_stamp(content: GameContent) -> None:
    """Тот же вид, та же ступень, та же редкость - другой ведущий аффикс."""
    source = Random(4)
    for _ in range(20):
        made = salvage.reforged(content, "sword@24#rare~3", source=source)
        parsed = gear_procgen.parse_gear_id(made)
        assert parsed is not None
        assert parsed[:3] == ("sword", 24, "rare")
        assert parsed[3] != 3, "оттиск обязан смениться: иначе это пошлина, а не работа"
        assert content.item(made).name != content.item("sword@24#rare~3").name


def test_only_a_thing_with_bonuses_is_worth_reforging(content: GameContent) -> None:
    assert not salvage.can_reforge(content, content.item("sword@24#common"))
    assert salvage.can_reforge(content, content.item("sword@24#uncommon"))
    assert salvage.reforge_price(content, content.item("sword@24#common")) == 0
    assert salvage.reforge_price(content, content.item("sword@24#rare")) > 0


def test_reforging_costs_less_than_the_thing_itself(content: GameContent) -> None:
    """Перековка дешевле новой вещи и дороже скупки: иначе её незачем делать."""
    item = content.item("sword@24#rare")
    assert salvage.reforge_price(content, item) < item.price
