from dataclasses import replace

import pytest

from mmorpg.domain.entities.long_goal import GoalState, LongGoalRules, ProjectState
from mmorpg.domain.rules import long_goal as rules


def test_common_unit_and_personal_participation_are_different():
    state = ProjectState(LongGoalRules(project_battles=2))
    state = rules.contribute(state, "cull", 1, (1, 2, 2), "fight")
    assert rules.progress(state, "cull") == 1
    assert state.participation == ((1, "cull", 1), (2, "cull", 1))
    assert rules.contribute(state, "cull", 1, (1,), "fight") == state


@pytest.mark.parametrize(
    "kind,amount,members,receipt",
    [
        ("unknown", 1, (1,), "x"),
        ("cull", 0, (1,), "x"),
        ("cull", -1, (1,), "x"),
        ("cull", 1, (), "x"),
        ("cull", 1, (1,), ""),
    ],
)
def test_invalid_activity_changes_nothing(kind, amount, members, receipt):
    state = ProjectState(LongGoalRules())
    assert rules.contribute(state, kind, amount, members, receipt) == state


def test_targets_are_bounded_and_all_three_are_required():
    state = ProjectState(LongGoalRules(project_battles=1, project_descents=1, project_gold=5))
    state = rules.contribute(state, "cull", 999, (1,), "a")
    assert not state.complete and rules.progress(state, "cull") == 1
    assert rules.contribute(state, "cull", 1, (1,), "b") == state
    state = rules.contribute(state, "delve", 1, (2,), "c")
    state = rules.contribute(state, "tithe", 20, (1,), "d")
    assert state.complete and rules.progress(state, "tithe") == 5
    assert rules.contribute(state, "tithe", 1, (1,), "e") == state


@pytest.mark.parametrize("track", rules.TRACKS)
def test_goal_requirements(track):
    config = LongGoalRules()
    assert not rules.ready(config, GoalState(), track, True)
    state = GoalState(trial=True, crafted=3, studied=True)
    assert rules.ready(config, state, track, True)
    if track == "chronicle":
        assert not rules.ready(config, state, track, False)
    assert not rules.ready(config, replace(state, crafted=2), "craft", True)
    assert not rules.ready(config, state, "pvp", True)
