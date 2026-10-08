from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character
from mmorpg.domain.rules.recruitment import goals, level_refusal


@pytest.mark.parametrize(
    "level,leader,low,high,accepted",
    [
        (6, 6, 1, 11, True),
        (11, 6, 1, 11, True),
        (12, 6, 1, 12, False),
        (150, 150, 140, 150, True),
        (6, 6, 0, 16, False),
        (6, 6, 1, 151, False),
        (6, 6, 7, 16, False),
        (6, 6, 1, 25, False),
        (1, 6, 2, 16, False),
        (6, 6, 16, 1, False),
    ],
)
def test_level_range_is_real_and_holds_existing_party_window(level, leader, low, high, accepted):
    assert bool(level_refusal(level, leader, low, high)) != accepted


def test_every_goal_comes_from_current_content_and_respects_access(content):
    for city in content.cities:
        hero = Character(
            id=1,
            user_id=1,
            name="Герой",
            race_id="human",
            class_id="warrior",
            level=city.level_max,
            city_id=city.id,
        )
        listed = goals(content, hero)
        assert len(listed) == len(city.locations) + len(city.dungeons)
        assert len({one.key for one in listed}) == len(listed)
        assert all(one.city_id == city.id for one in listed)
        for low in (1, city.unlock_level):
            assert all(one.level <= hero.level for one in goals(content, replace(hero, level=low)))
    assert goals(content, replace(hero, city_id="missing")) == ()
