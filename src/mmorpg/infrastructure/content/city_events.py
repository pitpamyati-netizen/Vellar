"""Проверка ссылок городского события отдельно от общего загрузчика."""

from typing import Any

from mmorpg.domain.entities.city_event import CityEvent, CityEventOutcome, CityEventStage
from mmorpg.domain.entities.content import City
from mmorpg.domain.entities.craft import Recipe
from mmorpg.domain.rules.city_event import KINDS


def parse_city_events(
    raw: dict[str, Any], cities: tuple[City, ...], recipes: tuple[Recipe, ...], problems: list[str]
) -> tuple[CityEvent, ...]:
    events: list[CityEvent] = []
    for data in raw.get("event", ()):
        try:
            event = CityEvent(
                id=data["id"],
                city_id=data["city_id"],
                name=data["name"],
                stages=tuple(CityEventStage(**one) for one in data["stage"]),
                outcomes=tuple(CityEventOutcome(**one) for one in data["outcome"]),
            )
            city = next((one for one in cities if one.id == event.city_id), None)
            if city is None or not event.stages:
                raise ValueError("unknown city or empty stages")
            if len({one.id for one in event.stages}) != len(event.stages):
                raise ValueError("duplicate stage")
            if sorted(one.kind for one in event.outcomes) != sorted((*KINDS, "balanced")):
                raise ValueError("outcomes must cover all contribution kinds and ties")
            if any(not 0 <= one.travel_discount <= 20 or not one.text for one in event.outcomes):
                raise ValueError("invalid outcome or discount")
            for stage in event.stages:
                recipe = next((one for one in recipes if one.id == stage.recipe_id), None)
                # Бой и разведка доступны одиночке без обязательного ремесла.
                if (
                    not stage.id
                    or not stage.name
                    or not stage.text
                    or not 1 <= stage.target <= 2 * stage.cap
                    or stage.cap < 1
                    or stage.reward_gold < 0
                ):
                    raise ValueError("invalid stage or solo recovery path")
                if stage.location_slot not in {one.slot for one in city.locations}:
                    raise ValueError("unknown location")
                if recipe is None or recipe.rank != 1:
                    raise ValueError("supply must use an existing rank-one recipe")
            if any(one.id == event.id or one.city_id == event.city_id for one in events):
                raise ValueError("duplicate event or city")
            events.append(event)
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"city_events.toml: {exc}")
    return tuple(events)
