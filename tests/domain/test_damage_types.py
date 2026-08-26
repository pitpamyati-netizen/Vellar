"""Род урона: четыре физических, одиннадцать магических и ни одного пустого.

До этого у удара было два состояния - железо и чары, - а между ними стоял тег
``elemental``, который не значил ни того, ни другого; рядом с ним жил ``chaos``,
у которого нет ни оружия, ни породы, ни источника. Убраны оба. Здесь
проверяется, что род урона есть у каждого оружия и у каждой породы, что
сопротивление считается по нему и что ни «стихийного», ни «хаотического» урона
в игре больше нет.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.damage import (
    DAMAGE_TYPE_NAMES,
    MAGIC_TYPES,
    PHYSICAL_TYPES,
    RESIST_KEYS,
    DamageType,
)
from mmorpg.domain.entities.location import DEFAULT_DAMAGE_TYPES, EnemyKind
from mmorpg.domain.procgen.enemies import element_of
from mmorpg.domain.rules.combat import incoming_damage_factor
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS


def test_the_two_halves_cover_every_kind() -> None:
    assert set(DamageType) == PHYSICAL_TYPES | MAGIC_TYPES
    assert not PHYSICAL_TYPES & MAGIC_TYPES
    assert len(PHYSICAL_TYPES) == 4
    assert len(MAGIC_TYPES) == 11
    for one in PHYSICAL_TYPES:
        assert one.is_physical and not one.is_magic
    for one in MAGIC_TYPES:
        assert one.is_magic and not one.is_physical


def test_no_kind_is_called_elemental_or_chaos() -> None:
    """«Стихия» без стихии и «хаос» без источника - слова, а не рода урона."""
    values = {one.value for one in DamageType}
    assert "elemental" not in values
    assert "chaos" not in values


def test_every_kind_is_named_in_russian() -> None:
    for one in DamageType:
        assert DAMAGE_TYPE_NAMES[one], one


def test_every_kind_has_a_resistance_the_engine_reads() -> None:
    for one in DamageType:
        assert one.resist_key in RESIST_KEYS
        assert one.resist_key in EFFECTIVE_KEYS
        assert one.half_resist_key in EFFECTIVE_KEYS


def test_resistance_of_the_kind_and_of_its_half_add_up() -> None:
    plated = {"resist_physical_percent": 20.0, "resist_slashing_percent": 30.0}
    assert incoming_damage_factor(plated, DamageType.SLASHING) == pytest.approx(0.5)
    assert incoming_damage_factor(plated, DamageType.PIERCING) == pytest.approx(0.8)
    assert incoming_damage_factor(plated, DamageType.FIRE) == pytest.approx(1.0)


def test_resistance_never_turns_a_blow_into_healing() -> None:
    absurd = {"resist_fire_percent": 400.0}
    assert incoming_damage_factor(absurd, DamageType.FIRE) == 0.0


def test_every_weapon_kind_says_what_it_deals(content: GameContent) -> None:
    """Оружие, о котором не сказано, чем оно бьёт, - это удар без рода."""
    for weapon in content.weapon_types:
        assert isinstance(weapon.damage_type, DamageType), weapon.id


def test_the_hand_weapons_deal_physical_damage(content: GameContent) -> None:
    """Железо бьёт железом: колет, рубит или дробит. Жезл и символ - не железо."""
    by_id = {weapon.id: weapon for weapon in content.weapon_types}
    assert by_id["spear"].damage_type is DamageType.PIERCING
    assert by_id["dagger"].damage_type is DamageType.PIERCING
    assert by_id["sword"].damage_type is DamageType.SLASHING
    assert by_id["mace"].damage_type is DamageType.BLUDGEONING
    assert by_id["bow"].damage_type is DamageType.PIERCING
    assert by_id["wand"].damage_type.is_magic


def test_every_breed_strikes_with_something(content: GameContent) -> None:
    for kind in EnemyKind:
        assert kind in DEFAULT_DAMAGE_TYPES, kind
    for archetype in content.enemy_archetypes:
        assert isinstance(element_of(archetype), DamageType), archetype.id


def test_a_declared_kind_beats_the_breed(content: GameContent) -> None:
    """Каменный истукан бьёт камнем, а не чарами: содержимое сильнее породы."""
    by_id = {archetype.id: archetype for archetype in content.enemy_archetypes}
    assert element_of(by_id["stone_golem"]) is DamageType.BLUDGEONING
    assert element_of(by_id["grey_wolf"]) is DEFAULT_DAMAGE_TYPES[EnemyKind.BEAST]
