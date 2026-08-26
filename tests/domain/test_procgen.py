"""Процедурная сборка: определённая, связная, всегда проходимая.

Тесты свойств здесь и есть спецификация сборщика. Стоит любому из них упасть, и
игроки окажутся в локации, из которой не выйти.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mmorpg.domain.entities import GameContent, NodeKind, NodeState
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.procgen import (
    DEFAULT_SHOP_ROTATION_SECONDS,
    MAX_NODES,
    MIN_NODES,
    combat_nodes,
    generate_enemy,
    generate_group,
    generate_location,
    location_seed,
    rotation_ends_at,
    rotation_index,
    seconds_left_in_rotation,
    wave_seed,
)
from mmorpg.domain.procgen.seeds import enemy_seed, node_seed
from mmorpg.domain.rules import nodes as node_rules

WORLD_SEED = "vellar-test"


def build(city_id: str = "farhold", slot: int = 1, seed: str = WORLD_SEED):
    return generate_location(
        world_seed=seed,
        city_id=city_id,
        slot=slot,
        name="Луга у Заставы",
        biome="луга",
        level_min=1,
        level_max=4,
    )


# --- то единственное, что ещё на часах -------------------------------


def test_the_shop_rotates_every_half_hour() -> None:
    """Мир больше не переворачивается по страже; переворачивается только прилавок."""
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


# --- определённость --------------------------------------------------


def test_ten_thousand_runs_are_byte_identical() -> None:
    """Определённость - весь договор: никакого глобального random, никогда."""
    reference = location_seed(WORLD_SEED, "farhold", 1)
    assert all(location_seed(WORLD_SEED, "farhold", 1) == reference for _ in range(10_000))


def test_the_map_is_permanent() -> None:
    """Локация - это место, а место не переворачивается: карта всегда одна и та же."""
    assert build() == build()


def test_different_slots_and_cities_differ() -> None:
    assert build(slot=1) != build(slot=2)
    assert build(city_id="farhold") != build(city_id="stonedale")


def test_a_different_world_seed_changes_everything() -> None:
    assert build(seed="one") != build(seed="another")


# --- строение --------------------------------------------------------


@given(slot=st.integers(min_value=1, max_value=5))
@settings(max_examples=400, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_structure_invariants(slot: int) -> None:
    location = build(slot=slot)

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


@given(city=st.sampled_from(["farhold", "dusk_harbor", "bone_marches", "last_beacon"]))
@settings(max_examples=200)
def test_exit_is_always_reachable_across_cities(city: str) -> None:
    location = generate_location(
        world_seed=WORLD_SEED,
        city_id=city,
        slot=3,
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
        name="Выработки",
        biome="подземелье",
        level_min=22,
        level_max=30,
    )
    assert location.entrance.level == 22
    assert location.exit_node.level == 30
    levels = [node.level for node in location.nodes]
    assert levels == sorted(levels)


# --- что стоит в узле и когда оно возвращается -----------------------


def test_every_node_holds_a_wave_of_its_own_size() -> None:
    """Прежняя модель была рубильником: одно нажатие, и узел кончился навсегда."""
    seed = location_seed(WORLD_SEED, "farhold", 1)
    location = build()
    for node in location.nodes:
        low, high = node_rules.WAVE_SIZE[node.kind]
        assert low <= node_rules.wave_size(seed, node.index, node.kind, 0) <= high


def test_a_battle_node_holds_more_than_one_pack() -> None:
    seed = location_seed(WORLD_SEED, "farhold", 1)
    assert node_rules.WAVE_SIZE[NodeKind.BATTLE][0] >= 2
    assert node_rules.wave_size(seed, 1, NodeKind.BATTLE, 0) >= 2


def test_taking_the_last_thing_empties_the_node_and_it_refills() -> None:
    state = NodeState()
    for _ in range(3):
        state = node_rules.taken_one(state, 3, now=1_000)
    assert node_rules.remaining(state, 3) == 0
    assert node_rules.seconds_until_refill(state, 1_000) == node_rules.RESPAWN_SECONDS

    waiting = node_rules.refreshed(state, 1_000 + node_rules.RESPAWN_SECONDS - 1)
    assert waiting == state

    filled = node_rules.refreshed(state, 1_000 + node_rules.RESPAWN_SECONDS)
    assert filled.wave == 1
    assert filled.taken == 0
    assert not filled.empty


def test_the_refill_waits_three_minutes() -> None:
    assert node_rules.RESPAWN_SECONDS == 180


def test_a_new_wave_is_seeded_differently() -> None:
    """Тот же узел, наполненный заново, - это не те же три волка снова."""
    seed = location_seed(WORLD_SEED, "farhold", 1)
    assert wave_seed(seed, 3, 0) != wave_seed(seed, 3, 1)
    assert wave_seed(seed, 3, 0) != wave_seed(seed, 4, 0)


# --- противники ------------------------------------------------------


def test_enemy_generation_is_deterministic(content: GameContent) -> None:
    seed = enemy_seed(node_seed(location_seed(WORLD_SEED, "farhold", 1), 4), 0)
    first = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    second = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=5)
    assert first == second


def test_enemy_fits_the_biome(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "winter_march", 2), 0)
    enemy = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="снега", level=180)
    archetype = next(a for a in content.enemy_archetypes if a.id == enemy.archetype_id)
    assert archetype.fits("снега")


def test_enemies_scale_with_level(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1), 1)
    low = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=2)
    high = generate_enemy(seed, archetypes=content.enemy_archetypes, biome="луга", level=200)
    assert high.max_health > low.max_health * 10
    assert high.damage > low.damage
    assert high.gold > low.gold


def test_elites_are_stronger_and_alone(content: GameContent) -> None:
    seed = enemy_seed(location_seed(WORLD_SEED, "farhold", 1), 2)
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
        seed = enemy_seed(location_seed(WORLD_SEED, f"farhold-{attempt}", 1), 0)
        group = generate_group(seed, archetypes=content.enemy_archetypes, biome="лес", level=12)
        assert 1 <= len(group) <= 3
        sizes.add(len(group))
    assert sizes == {1, 2, 3}, "all group sizes should occur across many seeds"


def test_generation_never_touches_the_global_random(content: GameContent) -> None:
    """Забредший ``random.random()`` сделал бы мир невоспроизводимым."""
    import random

    random.seed(1)
    build()
    first = random.random()
    random.seed(1)
    build(slot=4)
    assert random.random() == first
