"""Общее городское дело: правила содержимого и постоянный результат."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CityEventStage:
    id: str
    name: str
    text: str
    target: int
    cap: int
    reward_gold: int
    location_slot: int
    recipe_id: str


@dataclass(frozen=True, slots=True)
class CityEventOutcome:
    kind: str
    text: str
    travel_discount: int


@dataclass(frozen=True, slots=True)
class CityEvent:
    id: str
    city_id: str
    name: str
    stages: tuple[CityEventStage, ...]
    outcomes: tuple[CityEventOutcome, ...]


@dataclass(frozen=True, slots=True)
class CityContribution:
    stage: int
    character_id: int
    kind: str
    receipt: str


@dataclass(frozen=True, slots=True)
class CityEventState:
    stage: int = 0
    contributions: tuple[CityContribution, ...] = ()
    claimed: tuple[tuple[int, int], ...] = ()
    outcomes: tuple[str, ...] = ()
