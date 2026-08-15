"""Procedural generation: deterministic, connected, always finishable.

The property tests here are the specification of the generator. If any of them
fails, players can end up in a location they cannot leave.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mmorpg.domain.entities import GameContent, NodeKind
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.procgen import (
    DEFAULT_SHOP_ROTATION_SECONDS,
    MAX_NODES,
    MIN_NODES,
    cleared_mask,
    combat_nodes,
    generate_enemy,
    generate_group,
    generate_location,
    is_cleared,
    location_seed,
    rotation_ends_at,
    rotation_index,
    seconds_left_in_rotation,
)
from mmorpg.domain.procgen.seeds import enemy_seed, node_seed

WORLD_SEED = "vellar-test"


def build(city_id: str = "farhold", slot: int = 1, generation: int = 100, seed: str = WORLD_SEED):
    return generate_location(
        world_seed=seed,
        city_id=city_id,
        slot=slot,
        generation=generation,
        name="Луга у Заставы",
        biome="луга",
        level_min=1,
        level_max=4,
    )


# --- the one thing still on a clock ----------------------------------


def test_the_shop_rotates_every_half_hour() -> None:
    """The world no longer turns over on a watch; only the shelf does."""
    assert DEFAULT_SHOP_ROTATION_SECONDS == 1_800
    assert rotation_index(0) == 0
    assert rotation_index(1_799) == 0
    assert rotation_index(1_800) == 1
    assert rotation_index(86_400) == 48


def test_seconds_left_in_rotation_is_a_valid_ttl() -> None:
    for moment in (0, 500, 1_799, 43_200):
        left = seconds_left_in_rotation(moment)
        assert 0 < left <= DEFAULT_SHOP_ROTATION_SECONDS
        assert moment + left == rotation_ends_at(rotation_index(moment))


# --- determinism -----------------------------------------------------


def test_same_seed_gives_an_identical_location() -> None:
    assert build() == build()


def test_ten_thousand_runs_are_byte_identical() -> None:
    """Determinism is the whole contract: no global random, ever."""
    reference = location_seed(WORLD_SEED, "farhold", 1, 100)
    assert all(location_seed(WORLD_SEED, "farhold", 1, 100) == reference for _ in range(10_000))


def test_a_new_generation_regenerates_the_location() -> None:
    """The map changes when the place is cleared out, and not before."""
    assert build(generation=100) != build(generation=101)


def test_different_slots_and_cities_differ() -> None:
    assert build(slot=1) != build(slot=2)
    assert build(city_id="farhold") != build(city_id="stonedale")


def test_a_different_world_seed_changes_everything() -> None:
    assert build(seed="one") != build(seed="another")


# --- structure -------------------------------------------------------


@given(
    generation=st.integers(min_value=0, max_value=200_000),
    slot=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=400, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_structure_invariants(generation: int, slot: int) -> None:
    location = build(slot=slot, generation=generation)

    assert MIN_NODES <= len(location.nodes) <= MAX_NODES
    assert location.entrance.kind is NodeKind.ENTRANCE
    assert location.exit_node.index == len(location.nodes) - 1
    assert location.is_connected, "every node must be reachable from the entrance"
    assert location.exit_node.index in location.reachable_from(0), "the exit must be reachable"
    assert combat_nodes(location), "a location always has at least one fight"

    for node in location.nodes:
        assert node.links, f"node {node.index} is isolated"
        assert node.index not in node.links, "no self links"
        for link in node.links:
            assert node.index in location.node(link).links, "links must be symmetric"
        assert location.level_min <= node.level <= location.level_max


@given(
    city=st.sampled_from(["farhold", "dusk_harbor", "bone_marches", "last_beacon"]),
    generation=st.integers(min_value=0, max_value=50_000),
)
@settings(max_examples=200)
def test_exit_is_always_reachable_across_cities(city: str, generation: int) -> None:
    location = generate_location(
        world_seed=WORLD_SEED,
        city_id=city,
        slot=3,
        generation=generation,
        name="Локация",
        biome="лес",
        level_min=10,
        level_max=20,
    )
    assert location.exit_node.index in location.reachable_from(0)


def test_node_levels_increase_with_depth() -> None:
    location = generate_location(
        world_seed=WORLD_SEED,
        city_id="farhold",
        slot=5,
        generation=7,
        name="Выработки",
        biome="подземелье",
        level_min=22,
        level_max=30,
    )
    assert location.entrance.level == 22
    assert location.exit_node.level == 30
    levels = [node.level for node in location.nodes]
    assert levels == sorted(levels)


# --- cleared node bitmask -------------------------------------------


def test_cleared_mask_round_trip() -> None:
    mask = cleared_mask([0, 3, 13])
    assert is_cleared(mask, 0)
    assert is_cleared(mask, 3)
    assert is_cleared(mask, 13)
    assert not is_cleared(mask, 1)


def test_cleared_mask_fits_a_small_integer() -> None:
    """The whole delta log for one location is a single integer in Redis."""
    assert cleared_mask(range(MAX_NODES)) < 2**MAX_NODES


# --- enemies ---------------------------------------------------------


def test_enemy_generation_is_deterministic(content: GameContent) -> None:
    seed = enemy_seed(node_seed(location_seed(WORLD_SEED, "farhold", 1, 3), 4), 0)
    first = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    second = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    assert first == second


def test_enemy_fits_the_biome(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "winter_march", 2, 9), 0)
    enemy = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="снега", level=180)
    archetype = next(a for a in content.enemy_archetypes if a.id == enemy.archetype_id)
    assert archetype.fits("снега")


def test_enemies_scale_with_level(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1, 3), 1)
    low = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=2)
    high = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=200)
    assert high.max_health > low.max_health * 10
    assert high.damage > low.damage
    assert high.gold > low.gold


def test_elites_are_stronger_and_alone(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1, 3), 2)
    normal = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="лес", level=30)
    elite = generate_enemy(
        seed,
        archetypes=content.enemy_archetypes,
        biome="лес",
        level=30,
        rank=EnemyRank.ELITE,
        elite_titles=content.elite_titles,
    )
    assert elite.is_elite
    assert elite.max_health > normal.max_health
    assert elite.gold > normal.gold
    group = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="лес",
        level=30,
        rank=EnemyRank.ELITE,
        elite_titles=content.elite_titles,
    )
    assert len(group) == 1


def test_groups_hold_between_one_and_three_enemies(content: GameContent) -> None:
    sizes = set()
    for attempt in range(200):
        seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1, attempt), 0)
        group = generate_group(seed, archetypes=content.enemy_archetypes, biome="лес", level=12)
        assert 1 <= len(group) <= 3
        sizes.add(len(group))
    assert sizes == {1, 2, 3}, "all group sizes should occur across many seeds"


def test_generation_never_touches_the_global_random(content: GameContent) -> None:
    """A stray ``random.random()`` would make the world irreproducible."""
    import random

    random.seed(1)
    build()
    first = random.random()
    random.seed(1)
    build(generation=999)
    assert random.random() == first
