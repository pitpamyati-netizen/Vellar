"""Очки, реальные пути по дереву, развилки и сохранность прежней сборки."""

import json
from dataclasses import replace

import pytest

from mmorpg.application.battle_snapshots import content_snapshot, decode, encode, restore_content
from mmorpg.domain.entities.character import SkillLoadout
from mmorpg.domain.rules import talents
from tests.domain.test_talents import arena, damage, hero

CLASSES = ("warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid")


def invest(content, character, code, count=1):
    for _ in range(count):
        updated = talents.choose(content, character, code)
        assert updated is not None, code
        character = updated
    return character


def branch_build(content, class_id="warrior", branch=1, through=5, route="a"):
    character = hero(class_id)
    for row in range(1, through + 1):
        sides = ("a", "b") if row < 5 else (route,)
        for side in sides:
            character = invest(content, character, f"{class_id}_path_{branch}_{row}_{side}", 2)
        character = invest(content, character, f"{class_id}_{row}_{branch}")
    return character


@pytest.mark.parametrize(
    "level,total", [(1, 0), (9, 0), (10, 1), (14, 1), (15, 2), (60, 11), (150, 29), (200, 29)]
)
def test_points_are_separate_and_follow_level(level, total):
    assert talents.points(hero(level=level)) == total


def test_rank_cost_and_stale_confirmation_cannot_spend_twice(content):
    original = hero(level=15)
    code = "warrior_path_1_1_a"
    first = talents.choose(content, original, code, expected_rank=0)
    assert first.loadout.talent_ranks == {code: 1}
    assert talents.choose(content, first, code, expected_rank=0) is None
    second = talents.choose(content, first, code, expected_rank=1)
    assert talents.rank_of(content, second, code) == 2
    assert talents.choose(content, second, code) is None
    assert talents.choose(content, second, "warrior_path_2_1_a") is None
    assert second.gold == original.gold
    assert second.unspent_skill_points == original.unspent_skill_points
    assert second.loadout.ranks == original.loadout.ranks


def test_full_parent_point_gate_and_level_are_independent(content):
    original = hero()
    assert talents.choose(content, original, "warrior_1_1") is None
    assert talents.choose(content, original, "warrior_path_1_2_a") is None
    one_rank = invest(content, original, "warrior_path_1_1_a")
    assert talents.choose(content, one_rank, "warrior_1_1") is None
    full_parent = invest(content, one_rank, "warrior_path_1_1_a")
    key = invest(content, full_parent, "warrior_1_1")
    assert talents.spent(content, key) == 3
    assert talents.choose(content, key, "warrior_path_1_2_a") is None
    ready = invest(content, key, "warrior_path_1_1_b")
    assert talents.choose(content, ready, "warrior_path_1_2_a") is not None
    assert talents.choose(content, replace(ready, level=29), "warrior_path_1_2_a") is None


@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("branch", (1, 2, 3))
@pytest.mark.parametrize("route", ("a", "b"))
def test_every_node_of_all_24_branches_is_reachable_and_final_choice_is_exclusive(
    content, class_id, branch, route
):
    built = branch_build(content, class_id, branch, route=route)
    assert talents.spent(content, built) == 23
    assert len(talents.selected(content, built)) == 14
    other = "b" if route == "a" else "a"
    assert talents.choose(content, built, f"{class_id}_path_{branch}_5_{other}") is None
    # Очки общей школы можно направить в другую ветку.
    hybrid = invest(content, built, f"{class_id}_path_{branch % 3 + 1}_1_a", 2)
    assert talents.spent(content, hybrid) == 25
    assert len(talents.pool(content, built)) == 45


@pytest.mark.parametrize(
    "saved",
    [
        {"warrior_5_1": 1},
        {"warrior_path_1_1_a": 3},
        {"cleric_path_1_1_a": 2},
        {"gone": 1},
        {"warrior_path_1_1_a": -1},
        {"warrior_path_1_1_a": True},
    ],
)
def test_invalid_saved_graph_does_not_grant_effect(content, saved):
    character = replace(hero(), loadout=SkillLoadout(talent_tree_version=1, talent_ranks=saved))
    assert talents.selected(content, character) == ()


def test_over_budget_and_missing_parent_cannot_be_loaded_as_free_power(content):
    built = branch_build(content)
    assert talents.selected(content, replace(built, level=10)) == ()
    corrupted = dict(built.loadout.talent_ranks)
    del corrupted["warrior_1_1"]
    restored = replace(built, loadout=replace(built.loadout, talent_ranks=corrupted))
    assert all(one.row == 1 for one in talents.selected(content, restored))
    corrupted = dict(built.loadout.talent_ranks)
    corrupted["warrior_path_1_5_b"] = 2
    restored = replace(built, loadout=replace(built.loadout, talent_ranks=corrupted))
    assert not any(one.row == 5 for one in talents.selected(content, restored))


def test_two_ranks_increase_actual_damage_and_passive_stats(content):
    original = hero()
    first = invest(content, original, "warrior_path_1_1_a")
    second = invest(content, first, "warrior_path_1_1_a")
    assert damage(content, second) > damage(content, first) > damage(content, original)
    first = invest(content, original, "warrior_path_3_1_a")
    second = invest(content, first, "warrior_path_3_1_a")
    assert arena(content, second).by_id(1).max_health > arena(content, first).by_id(1).max_health


def test_old_build_remains_active_until_confirmed_reset_without_mixing_systems(content):
    old = replace(hero(), loadout=replace(hero().loadout, talents=("warrior_1_1", "warrior_2_1")))
    assert [one.code for one in talents.selected(content, old)] == ["warrior_1_1", "warrior_2_1"]
    assert damage(content, old) > damage(content, hero())
    assert talents.choose(content, old, "warrior_path_1_1_a") is None
    reset = talents.reset(old)
    assert reset.loadout.talents == () and talents.spent(content, reset) == 0
    assert reset.loadout.ranks == old.loadout.ranks and reset.gold == old.gold
    assert talents.choose(content, reset, "warrior_path_1_1_a") is not None


def test_new_snapshot_and_snapshot_without_new_fields_both_restore(content):
    character = branch_build(content)
    restored = decode(json.loads(json.dumps(encode(character))))
    assert restored.loadout.talent_ranks == character.loadout.talent_ranks
    _, raw = content_snapshot(content)
    assert restore_content(raw).talents == content.talents
    old = encode(replace(hero().loadout, talents=("warrior_1_1",)))
    for field in ("talent_ranks", "talent_tree_version"):
        del old["fields"][field]
    saved = decode(old)
    assert saved.talent_tree_version == 0 and saved.talents == ("warrior_1_1",)
    assert saved.talent_ranks == {}


def test_talent_snapshot_without_tree_fields_keeps_old_battle_effect(content):
    modern = next(t for t in content.talents if t.code == "warrior_1_1")
    raw = encode(modern)
    for name in (
        "branch",
        "branch_name",
        "row",
        "max_rank",
        "points_required",
        "requires_all",
        "requires_any",
        "choice_group",
        "legacy",
    ):
        del raw["fields"][name]
    old = decode(raw)
    assert old.branch == "" and old.max_rank == 1
    old_content = replace(content, talents=(old,))
    original = hero()
    stronger = talents.choose(old_content, original, old.code)
    assert stronger is not None
    assert damage(old_content, stronger) == damage(
        content, replace(original, loadout=replace(original.loadout, talents=(modern.code,)))
    )
