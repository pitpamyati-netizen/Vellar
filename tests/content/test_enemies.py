"""Породы противников обязаны покрыть каждый биом, который мир и правда использует."""

from __future__ import annotations

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.procgen.enemies import candidates
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS

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


def test_every_dungeon_biome_has_enough_dungeon_candidates(content: GameContent) -> None:
    """Заход в подземелье тянет только dungeon-породы (ADR 0042)."""
    biomes = {one.biome for city in content.cities for one in city.dungeons if one.biome}
    for biome in sorted(biomes):
        pool = candidates(content.enemy_archetypes, biome, dungeon=True)
        assert len(pool) >= MINIMUM_CANDIDATES_PER_BIOME, f"{biome}: only {len(pool)} candidates"


def test_the_two_enemy_pools_are_disjoint(content: GameContent) -> None:
    """Дорожная стая и подземная тварь не встречаются в одном пуле."""
    location_biomes = {loc.biome for city in content.cities for loc in city.locations}
    for biome in sorted(location_biomes):
        assert all(not a.dungeon for a in candidates(content.enemy_archetypes, biome))
    assert any(a.dungeon and "*" in a.biomes for a in content.enemy_archetypes)
    assert candidates(content.enemy_archetypes, "невиданный биом", dungeon=True)


def test_affixes_declare_only_effective_modifier_keys(content: GameContent) -> None:
    assert len(content.affixes) >= 6
    known = {one.value for one in StatusKind}
    for affix in content.affixes:
        assert affix.adjective
        assert set(affix.modifiers) <= EFFECTIVE_KEYS, affix.id
        if affix.on_hit_status is not None:
            assert affix.on_hit_status.value in known, affix.id
        for mult in (affix.health, affix.damage, affix.armor, affix.initiative, affix.gold):
            assert 0.5 <= mult <= 2.5, affix.id
        assert affix.weight >= 1, affix.id
        assert 0.0 <= affix.on_hit_chance <= 100.0, affix.id
