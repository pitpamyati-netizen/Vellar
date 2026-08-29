"""Журнал смотрителя в памяти: те же страницы и тот же фильтр, что у Postgres.

Постранично журнал читают и с главной панели, и с карточки игрока — второе
сужено до одной цели. Обе дороги проверяет ``tests/integration`` против
настоящей базы; здесь то же самое против памяти, без базы.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities.moderation import KeeperAction, KeeperEntry
from mmorpg.infrastructure.persistence.memory import InMemoryKeeperLogRepository

pytestmark = pytest.mark.asyncio


async def _seeded() -> InMemoryKeeperLogRepository:
    log = InMemoryKeeperLogRepository()
    for step, (action, who) in enumerate(
        (
            (KeeperAction.GOLD, "Мерла"),
            (KeeperAction.BAN, "Мерла"),
            (KeeperAction.HEAL, "Аргус"),
        ),
        start=1,
    ):
        await log.record(
            KeeperEntry(at=step, keeper_id=1, action=action, target=who, detail=f"шаг {step}")
        )
    return log


async def test_the_journal_reads_from_the_end_and_pages_with_offset() -> None:
    log = await _seeded()

    assert [entry.detail for entry in await log.latest(limit=2)] == ["шаг 3", "шаг 2"]
    assert [entry.detail for entry in await log.latest(limit=2, offset=2)] == ["шаг 1"]
    assert await log.count() == 3


async def test_the_journal_filters_by_target_without_regard_to_case() -> None:
    log = await _seeded()

    about_merla = await log.latest(limit=50, target="мЕрЛа")

    assert [entry.detail for entry in about_merla] == ["шаг 2", "шаг 1"]
    assert await log.count(target="Мерла") == 2
    assert await log.count(target="Довен") == 0
