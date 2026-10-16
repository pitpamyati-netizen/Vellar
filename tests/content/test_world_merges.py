"""Ошибочная связь областей отвергается до открытия игры."""

from dataclasses import replace

import pytest

from mmorpg.infrastructure.content.loader import _validate_world


@pytest.mark.parametrize("target", [2, 99])
def test_self_merge_and_missing_destination_are_rejected(content, target):
    city = content.city("farhold")
    places = tuple(
        replace(one, merged_into=target) if one.slot == 2 else one for one in city.locations
    )
    cities = tuple(
        replace(one, locations=places) if one.id == city.id else one for one in content.cities
    )
    problems = []
    _validate_world(cities, content.rules, problems)
    assert any("invalid merged_into" in one for one in problems)


def test_merge_cannot_change_safety_or_remove_the_original_habitat(content):
    city = content.city("farhold")
    for changed in (replace(city.location(1), pvp=True), replace(city.location(1), habitats=())):
        places = tuple(changed if one.slot == 1 else one for one in city.locations)
        cities = tuple(
            replace(one, locations=places) if one.id == city.id else one for one in content.cities
        )
        problems = []
        _validate_world(cities, content.rules, problems)
        assert any("unsafe merge" in one for one in problems)


def test_a_gap_between_peaceful_locations_cannot_be_hidden_by_pvp(content):
    city = content.city("farhold")
    places = tuple(replace(one, level_max=8) if one.slot == 3 else one for one in city.locations)
    cities = tuple(
        replace(one, locations=places) if one.id == city.id else one for one in content.cities
    )
    problems = []
    _validate_world(cities, content.rules, problems)
    assert any("requires PvP" in one for one in problems)
