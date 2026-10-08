"""Одна текущая отметка на игрока и числовые исходы успешных действий."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from mmorpg.application.journey import Activity, Journey, JourneyStats, Visit
from mmorpg.infrastructure.cache.memory_operations import memory_cache_action
from mmorpg.infrastructure.persistence.operations import (
    MemoryOperations,
    OperationPool,
    PostgresOperations,
)

SESSION_GAP = 30 * 60
ALL_TUTORIAL = (1 << 6) - 1


def observed(previous: Journey | None, visit: Visit) -> Journey:
    from mmorpg.domain.rules.tutorial import ORDER

    stages = (
        visit.screen,
        *(
            f"tutorial:{task.value}"
            for index, task in enumerate(ORDER)
            if visit.tutorial & (1 << index)
        ),
    )
    return Journey(
        first_at=previous.first_at if previous else visit.at,
        last=visit,
        reached=tuple(dict.fromkeys((*previous.reached, *stages))) if previous else stages,
        completed_at=(previous.completed_at if previous else 0)
        or (visit.at if visit.tutorial & ALL_TUTORIAL == ALL_TUTORIAL else 0),
        sessions=(previous.sessions + int(visit.at - previous.last.at >= SESSION_GAP))
        if previous
        else 1,
    )


def totals(journeys: list[Journey], inactive_before: int) -> JourneyStats:
    journeys = [
        j for j in journeys if any(s.startswith("create_") or s == "name" for s in j.reached)
    ]
    stages: dict[str, int] = {}
    inactive: dict[str, int] = {}
    for journey in journeys:
        for stage in journey.reached:
            stages[stage] = stages.get(stage, 0) + 1
        if journey.last.at <= inactive_before:
            stage = journey.last.screen
            inactive[stage] = inactive.get(stage, 0) + 1
    return JourneyStats(
        len(journeys),
        sum(j.last.character_id > 0 for j in journeys),
        sum(j.completed_at > 0 for j in journeys),
        sum(j.sessions > 1 for j in journeys),
        tuple(sorted(stages.items())),
        tuple(sorted(inactive.items())),
    )


class MemoryJourneyRepository:
    def __init__(self) -> None:
        self.operations = MemoryOperations()
        self.journeys: dict[tuple[int, int], Journey] = {}
        self.activities: dict[tuple[str, int, int], Activity] = {}

    @memory_cache_action
    async def get(self, bot_id: int, user_id: int) -> Journey | None:
        return self.journeys.get((bot_id, user_id))

    @memory_cache_action
    async def observe(self, visit: Visit, activity: Activity | None, operation_id: str) -> None:
        key = (visit.bot_id, visit.user_id)
        self.journeys[key] = observed(self.journeys.get(key), visit)
        if activity is not None:
            self.activities.setdefault((operation_id, *key), activity)

    @memory_cache_action
    async def history(
        self, bot_id: int, user_id: int, *, limit: int = 20, offset: int = 0
    ) -> tuple[Activity, ...]:
        found = [
            a for (_, bot, user), a in self.activities.items() if (bot, user) == (bot_id, user_id)
        ]
        # На одной секунде последняя вставка должна идти первой, как SQL id.
        found.reverse()
        return tuple(sorted(found, key=lambda a: a.at, reverse=True)[offset : offset + limit])

    @memory_cache_action
    async def stats(self, bot_id: int, *, inactive_before: int) -> JourneyStats:
        return totals(
            [j for (bot, _), j in self.journeys.items() if bot == bot_id], inactive_before
        )


class PostgresJourneyRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = OperationPool(pool)
        self.operations = PostgresOperations(self._pool)

    @staticmethod
    def decode(row: Any) -> Journey:
        return Journey(
            row["first_at"],
            Visit(
                row["bot_id"],
                row["user_id"],
                row["screen"],
                row["last_at"],
                row["character_id"],
                row["tutorial"],
            ),
            tuple(json.loads(row["reached"])),
            row["completed_at"],
            row["sessions"],
        )

    async def get(self, bot_id: int, user_id: int) -> Journey | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM player_journey WHERE bot_id=$1 AND user_id=$2", bot_id, user_id
        )
        return self.decode(row) if row else None

    async def observe(self, visit: Visit, activity: Activity | None, operation_id: str) -> None:
        journey = observed(await self.get(visit.bot_id, visit.user_id), visit)
        await self._pool.execute(
            """
            INSERT INTO player_journey(bot_id,user_id,first_at,last_at,character_id,screen,tutorial,
                                       reached,completed_at,sessions)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT(bot_id,user_id) DO UPDATE SET last_at=EXCLUDED.last_at,
                character_id=EXCLUDED.character_id, screen=EXCLUDED.screen,
                tutorial=EXCLUDED.tutorial,
                reached=EXCLUDED.reached,completed_at=EXCLUDED.completed_at,sessions=EXCLUDED.sessions
        """,
            visit.bot_id,
            visit.user_id,
            journey.first_at,
            visit.at,
            visit.character_id,
            visit.screen,
            visit.tutorial,
            json.dumps(journey.reached),
            journey.completed_at,
            journey.sessions,
        )
        if activity is not None:
            await self._pool.execute(
                """
                INSERT INTO player_activity(operation_id,bot_id,user_id,at,gold,bank,experience,
                                            levels,tutorial_steps,quests,items)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) ON CONFLICT DO NOTHING
            """,
                operation_id,
                visit.bot_id,
                visit.user_id,
                *(value for name, value in asdict(activity).items() if name != "items"),
                json.dumps(activity.items),
            )

    async def history(
        self, bot_id: int, user_id: int, *, limit: int = 20, offset: int = 0
    ) -> tuple[Activity, ...]:
        rows = await self._pool.fetch(
            """
            SELECT at,gold,bank,experience,levels,tutorial_steps,quests,items FROM player_activity
            WHERE bot_id=$1 AND user_id=$2 ORDER BY at DESC,id DESC LIMIT $3 OFFSET $4
        """,
            bot_id,
            user_id,
            limit,
            offset,
        )
        return tuple(
            Activity(
                **{**dict(row), "items": tuple(tuple(one) for one in json.loads(row["items"]))}
            )
            for row in rows
        )

    async def stats(self, bot_id: int, *, inactive_before: int) -> JourneyStats:
        # SQL агрегирует без вывода идентификаторов и частных сообщений.
        row = await self._pool.fetchrow(
            """
            SELECT count(*) AS started,count(*) FILTER(WHERE character_id>0) AS created,
                count(*) FILTER(WHERE completed_at>0) AS completed,
                count(*) FILTER(WHERE sessions>1) AS returned FROM player_journey
                WHERE bot_id=$1 AND (reached ? 'name' OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(reached) s WHERE s LIKE 'create_%'))
        """,
            bot_id,
        )
        stages = await self._pool.fetch(
            """
            SELECT stage,count(*) AS amount FROM player_journey,
                jsonb_array_elements_text(reached) AS stage WHERE bot_id=$1
                AND (reached ? 'name' OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(reached) s WHERE s LIKE 'create_%'))
                GROUP BY stage ORDER BY stage
        """,
            bot_id,
        )
        inactive = await self._pool.fetch(
            """
            SELECT screen,count(*) AS amount FROM player_journey
            WHERE bot_id=$1 AND last_at<=$2 AND (reached ? 'name' OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(reached) s WHERE s LIKE 'create_%'))
            GROUP BY screen ORDER BY screen
        """,
            bot_id,
            inactive_before,
        )
        return JourneyStats(
            **dict(row),
            reached=tuple((r["stage"], r["amount"]) for r in stages),
            inactive=tuple((r["screen"], r["amount"]) for r in inactive),
        )
