"""Вступление в дом и его техника (ADR 0049)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import houses, modifiers


@pytest.fixture
def wanderer() -> Character:
    """Безродный двадцатого уровня с деньгами на взнос."""
    return Character(
        id=1,
        user_id=1,
        name="Тьен",
        race_id="human",
        class_id="warrior",
        level=houses.JOIN_LEVEL,
        gold=houses.JOIN_FEE + 50,
        city_id="farhold",
    )


def test_seven_houses_split_the_road_and_not_the_throne(content: GameContent) -> None:
    assert len(content.houses) == 7
    held = [seat for house in content.houses for seat in house.seats]
    assert len(held) == len(set(held)) == 14
    assert "obsidian_throne" not in held
    assert content.house_of_city("obsidian_throne") is None


def test_you_join_in_a_house_city_for_a_fee(content: GameContent, wanderer: Character) -> None:
    assert houses.join_refusal(content, wanderer, "farhold") == ""
    joined = houses.join(content, wanderer, "farhold")
    assert joined is not None
    assert joined.house_id == "borderland"
    assert joined.gold == wanderer.gold - houses.JOIN_FEE


def test_no_house_where_the_throne_stands(content: GameContent, wanderer: Character) -> None:
    assert "нет двора" in houses.join_refusal(content, wanderer, "obsidian_throne")
    assert houses.join(content, wanderer, "obsidian_throne") is None


def test_the_gate_is_level_and_a_fee(content: GameContent, wanderer: Character) -> None:
    young = replace(wanderer, level=houses.JOIN_LEVEL - 1)
    assert f"{houses.JOIN_LEVEL} уровня" in houses.join_refusal(content, young, "farhold")
    broke = replace(wanderer, gold=houses.JOIN_FEE - 1)
    assert "Взнос" in houses.join_refusal(content, broke, "farhold")


def test_you_belong_to_one_house_and_leaving_is_free(
    content: GameContent, wanderer: Character
) -> None:
    inside = replace(wanderer, house_id="borderland")
    assert "уже в доме" in houses.join_refusal(content, inside, "farhold")
    # Другой дом — там же, где вступают: сперва уйти.
    left = houses.leave(inside)
    assert left is not None and left.house_id == ""
    assert left.gold == wanderer.gold, "уход из дома денег не стоит"
    assert houses.leave(left) is None


def test_the_technique_feeds_the_modifier_stack(content: GameContent, wanderer: Character) -> None:
    outside = modifiers.collect_modifiers(content, wanderer)
    inside = modifiers.collect_modifiers(content, replace(wanderer, house_id="borderland"))
    tech = content.house("borderland").technique.modifiers
    assert tech, "у техники есть прибавки"
    for key, value in tech.items():
        assert inside.get(key, 0) == outside.get(key, 0) + value


def test_a_vanished_house_gives_nothing(content: GameContent, wanderer: Character) -> None:
    """Сохранённому не верят: дом, которого больше нет, техники не даёт."""
    ghost = replace(wanderer, house_id="house_that_never_was")
    assert houses.current_house(content, ghost) is None
    assert houses.technique_modifiers(content, ghost) == {}
