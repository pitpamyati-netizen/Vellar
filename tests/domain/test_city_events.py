"""Развилки, предел повторов и проходимость без ремесла и сроков."""

from dataclasses import replace

import pytest

from mmorpg.domain.entities.city_event import CityEventState
from mmorpg.domain.rules import city_event as rules
from mmorpg.infrastructure.content.city_events import parse_city_events


@pytest.mark.parametrize(
    "kind,discount", [("battle", 10), ("scout", 15), ("craft", 20), ("balanced", 10)]
)
def test_all_outcomes_open_next_situation_and_safe_final_city(content, kind, discount):
    event = content.city_events[0]
    state = CityEventState()
    for stage in range(2):
        for index in range(4):
            method = kind if kind != "balanced" else ("battle" if index < 2 else "scout")
            state, notice = rules.contribute(event, state, index + 1, method, f"{stage}:{index}")
        assert state.stage == stage + 1
        assert state.outcomes[-1] == kind
        assert "завершено" in notice
    assert rules.discount(event, state) == discount
    assert rules.reward_due(event, state, 1) == (40, (0, 1))
    assert rules.reward_due(event, replace(state, claimed=((0, 1),)), 1) == (20, (1,))
    assert rules.reward_due(event, state, 50) == (0, ())
    assert rules.contribute(event, state, 10, "scout", "extra")[0] == state


def test_single_person_can_finish_with_two_kinds_and_no_deadline(content):
    event = content.city_events[0]
    state = CityEventState()
    assert rules.discount(event, state) == 0
    assert rules.reward_due(event, state, 1) == (0, ())
    assert rules.refusal(event, state, 1, "fake", "1")
    assert rules.refusal(event, state, 1, "battle", "")
    for stage in range(2):
        for index in range(2):
            state, _ = rules.contribute(event, state, 1, "battle", f"battle:{stage}:{index}")
        unchanged, message = rules.contribute(event, state, 1, "battle", f"third:{stage}")
        assert unchanged == state and "предел" in message
        for index in range(2):
            state, _ = rules.contribute(event, state, 1, "scout", f"scout:{stage}:{index}")
        duplicate, message = rules.contribute(event, state, 1, "battle", "battle:0:0")
        assert duplicate == state and "уже" in message
    assert state.stage == 2


@pytest.mark.parametrize(
    "broken",
    [
        "city",
        "recipe",
        "slot",
        "cap",
        "reward",
        "outcome",
        "discount",
        "duplicate",
        "type",
        "missing",
    ],
)
def test_content_rejects_unreachable_or_invalid_events(content, broken):
    from dataclasses import asdict

    data = asdict(content.city_events[0])
    data["stage"] = [dict(one) for one in data.pop("stages")]
    data["outcome"] = [dict(one) for one in data.pop("outcomes")]
    if broken == "city":
        data["city_id"] = "absent"
    elif broken == "recipe":
        data["stage"][0]["recipe_id"] = "absent"
    elif broken == "slot":
        data["stage"][0]["location_slot"] = 90
    elif broken == "cap":
        data["stage"][0]["cap"] = 1
    elif broken == "reward":
        data["stage"][0]["reward_gold"] = -1
    elif broken == "outcome":
        data["outcome"].pop()
    elif broken == "discount":
        data["outcome"][0]["travel_discount"] = 100
    elif broken == "duplicate":
        data["stage"][1]["id"] = data["stage"][0]["id"]
    elif broken == "type":
        data["stage"][0]["target"] = "bad"
    elif broken == "missing":
        del data["name"]
    problems = []
    assert not parse_city_events({"event": [data]}, content.cities, content.recipes, problems)
    assert problems


def test_event_survives_content_rebuild_and_duplicate_is_rejected(content):
    from dataclasses import asdict

    assert content.rebuilt().city_events == content.city_events
    data = asdict(content.city_events[0])
    data["stage"] = data.pop("stages")
    data["outcome"] = data.pop("outcomes")
    problems = []
    events = parse_city_events({"event": [data, data]}, content.cities, content.recipes, problems)
    assert len(events) == 1 and problems
