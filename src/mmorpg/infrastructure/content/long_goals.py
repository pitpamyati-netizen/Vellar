"""Связи долгих целей проверяются вместе с остальным содержимым."""

from typing import Any

from mmorpg.domain.entities.content import City
from mmorpg.domain.entities.craft import Recipe
from mmorpg.domain.entities.long_goal import ChronicleFragment, LongGoalRules, StoryOutcome
from mmorpg.domain.entities.quest import Quest


def parse_long_goals(
    raw: dict[str, Any],
    cities: tuple[City, ...],
    quests: tuple[Quest, ...],
    recipes: tuple[Recipe, ...],
    problems: list[str],
) -> LongGoalRules:
    try:
        data = dict(raw["rules"])
        data["story_quests"] = tuple(data["story_quests"])
        data["outcomes"] = tuple(StoryOutcome(**one) for one in raw["outcome"])
        data["fragments"] = tuple(ChronicleFragment(**one) for one in raw["fragment"])
        rules = LongGoalRules(**data)
        city = next((one for one in cities if one.id == rules.city_id), None)
        known = {one.id: one for one in quests}
        if city is None or not city.has_dungeon(rules.dungeon_id):
            raise ValueError("unknown story city or trial dungeon")
        if not rules.story_quests or len(set(rules.story_quests)) != len(rules.story_quests):
            raise ValueError("empty or duplicate story requirements")
        if any(
            key not in known or known[key].city_id != rules.city_id for key in rules.story_quests
        ):
            raise ValueError("unknown story quest or wrong city")
        if len(rules.outcomes) != 2 or len({one.id for one in rules.outcomes}) != 2:
            raise ValueError("story needs two distinct outcomes")
        if sorted(one.quest_id for one in rules.fragments) != sorted(rules.story_quests):
            raise ValueError("chronicle must cover each existing story quest once")
        if any(not one.title or not one.text or len(one.text) > 600 for one in rules.fragments):
            raise ValueError("invalid chronicle fragment")
        if any(
            not one.id or not one.name or not one.text or not 0 <= one.travel_discount <= 20
            for one in rules.outcomes
        ):
            raise ValueError("invalid story outcome")
        if not rules.project_id or not rules.project_name or rules.level != 150:
            raise ValueError("invalid project or level cap")
        if (
            any(
                value <= 0
                for value in (
                    rules.project_battles,
                    rules.project_descents,
                    rules.project_gold,
                    rules.project_deeds,
                    rules.craft_target,
                    rules.war_account_seconds,
                )
            )
            or rules.reward_gold < 0
        ):
            raise ValueError("invalid goal quantities")
        if not any(one.rank == rules.craft_rank for one in recipes):
            raise ValueError("no recipes for crafting goal")
        return rules
    except (KeyError, TypeError, ValueError) as exc:
        problems.append(f"long_goals.toml: {exc}")
        return LongGoalRules()
