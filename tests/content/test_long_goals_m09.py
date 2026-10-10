import copy
import tomllib
from pathlib import Path

import pytest

from mmorpg.infrastructure.content.long_goals import parse_long_goals


def test_references_cover_existing_story_and_level_cap(content):
    rules = content.long_goals
    assert rules.level == 150
    assert all(content.has_quest(key) for key in rules.story_quests)
    assert content.city(rules.city_id).dungeon(rules.dungeon_id).level == 150
    assert len(rules.outcomes) == 2
    assert {one.quest_id for one in rules.fragments} == set(rules.story_quests)


@pytest.mark.parametrize(
    "field,value",
    [
        ("city_id", "missing"),
        ("dungeon_id", "missing"),
        ("story_quests", ["missing"]),
        ("story_quests", []),
        ("story_quests", ["last_beacon_far_claim", "last_beacon_far_claim"]),
        ("project_id", ""),
        ("project_battles", 0),
        ("craft_rank", 999),
        ("reward_gold", -1),
        ("level", 151),
        ("war_account_seconds", 0),
    ],
)
def test_bad_catalogue_rejected_with_location(content, field, value):
    raw = copy.deepcopy(tomllib.loads(Path("content/long_goals.toml").read_text(encoding="utf-8")))
    raw["rules"][field] = value
    errors = []
    parse_long_goals(raw, content.cities, content.quests, content.recipes, errors)
    assert errors and errors[0].startswith("long_goals.toml:")
