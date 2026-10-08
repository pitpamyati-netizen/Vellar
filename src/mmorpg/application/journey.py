"""Ход первой сессии: только игровые этапы и счётчики, без переписки."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Visit:
    bot_id: int
    user_id: int
    screen: str
    at: int
    character_id: int = 0
    tutorial: int = 0


@dataclass(frozen=True, slots=True)
class Journey:
    first_at: int
    last: Visit
    reached: tuple[str, ...] = ()
    completed_at: int = 0
    sessions: int = 1


@dataclass(frozen=True, slots=True)
class Activity:
    at: int
    gold: int = 0
    bank: int = 0
    experience: int = 0
    levels: int = 0
    tutorial_steps: int = 0
    quests: int = 0
    items: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class JourneyStats:
    started: int = 0
    created: int = 0
    completed: int = 0
    returned: int = 0
    reached: tuple[tuple[str, int], ...] = ()
    inactive: tuple[tuple[str, int], ...] = ()


class JourneyRepository(Protocol):
    async def get(self, bot_id: int, user_id: int) -> Journey | None: ...
    async def observe(self, visit: Visit, activity: Activity | None, operation_id: str) -> None: ...
    async def history(
        self, bot_id: int, user_id: int, *, limit: int = 20, offset: int = 0
    ) -> tuple[Activity, ...]: ...
    async def stats(self, bot_id: int, *, inactive_before: int) -> JourneyStats: ...
