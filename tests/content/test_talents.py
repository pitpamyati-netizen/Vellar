"""Повреждённый талант отвергается до запуска игры."""

import copy
import tomllib

import pytest

from mmorpg.infrastructure.content.talents import parse_talents
from tests.conftest import CONTENT_ROOT


@pytest.mark.parametrize(
    "change",
    [
        {"class": "missing"},
        {"level": 11},
        {"effect": "missing"},
        {"amount": float("nan")},
        {"amount": 26},
        {"category": "missing"},
        {"category": "buff"},
        {"condition": "missing"},
        {"condition": "actor_status"},
        {"statuses": ["missing"]},
        {"condition": "target_health_below", "threshold": 0},
        {"modifier": "armor_percent"},
        {"effect": "modifier", "modifier": "unused_key"},
        {"effect": "duration", "amount": 0.5, "category": "heal"},
        {"effect": "cost", "condition": "actor_barrier"},
        {"name": ""},
    ],
)
def test_bad_effect_never_becomes_a_silent_promise(content, change):
    raw = tomllib.loads((CONTENT_ROOT / "talents.toml").read_text(encoding="utf-8"))
    raw["talent"][0].update(change)
    problems = []
    parse_talents(raw, content.classes, content.skills, problems)
    assert any("warrior_1_1" in problem for problem in problems)


def test_repeated_or_missing_row_is_rejected(content):
    raw = tomllib.loads((CONTENT_ROOT / "talents.toml").read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(raw)
    duplicate["talent"].append(duplicate["talent"][0])
    problems = []
    parse_talents(duplicate, content.classes, content.skills, problems)
    assert any("repeated" in problem for problem in problems)
    raw["talent"].pop(0)
    problems = []
    parse_talents(raw, content.classes, content.skills, problems)
    assert any("must have three choices" in problem for problem in problems)
