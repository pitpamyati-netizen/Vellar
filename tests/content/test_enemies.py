"""Породы противников обязаны покрыть каждый биом, который мир и правда использует."""

from __future__ import annotations

from mmorpg.domain.entities import GameContent
from mmorpg.domain.procgen.enemies import candidates

MINIMUM_CANDIDATES_PER_BIOME = 3


def test_archetypes_exist(content: GameContent) -> None:
    assert content.enemy_archetypes
    assert content.elite_titles


def test_every_biome_has_enough_candidates(content: GameContent) -> None:
    """Иначе целая локация выставляла бы одного и того же противника снова и снова."""
    biomes = {location.biome for city in content.cities for location in city.locations}
    for biome in sorted(biomes):
        pool = candidates(content.enemy_archetypes, biome)
        assert len(pool) >= MINIMUM_CANDIDATES_PER_BIOME, f"{biome}: only {len(pool)} candidates"


def test_wildcard_archetypes_exist(content: GameContent) -> None:
    """Запасной набор оставляет незнакомый биом играбельным, а не роняет игру."""
    assert any("*" in archetype.biomes for archetype in content.enemy_archetypes)
    assert candidates(content.enemy_archetypes, "невиданный биом")


def test_multipliers_are_in_a_sane_range(content: GameContent) -> None:
    for archetype in content.enemy_archetypes:
        assert 0.5 <= archetype.health <= 2.5, archetype.id
        assert 0.5 <= archetype.damage <= 2.0, archetype.id
        assert 0.0 <= archetype.armor <= 2.0, archetype.id


def test_loot_points_at_real_items(content: GameContent) -> None:
    for archetype in content.enemy_archetypes:
        for item_id in archetype.loot:
            assert content.item(item_id)
