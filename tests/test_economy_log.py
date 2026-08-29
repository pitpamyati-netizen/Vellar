"""Второй приёмник денежного журнала (ADR 0044).

Журнальную строку по-прежнему пишет ``logger`` и читает ``scripts/economy.py`` —
это не трогаем. Проверяем только приёмник: подключён — событие уходит и туда,
не подключён — не уходит, а его ошибка нажатие игрока не роняет.
"""

from __future__ import annotations

import asyncio

import pytest

from mmorpg import economy_log

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_sink() -> object:
    economy_log.use_sink(None)
    yield
    economy_log.use_sink(None)


async def test_a_connected_sink_gets_every_movement() -> None:
    seen: list[tuple[str, int, int, str]] = []

    async def sink(flow: str, amount: int, character_id: int, detail: str) -> None:
        seen.append((flow, amount, character_id, detail))

    economy_log.use_sink(sink)
    economy_log.record(economy_log.FIGHT, 124, character_id=17)
    economy_log.record(economy_log.SHOP, -50, character_id=17, detail="малое зелье")
    # Ноль движением не считается — ни в журнале, ни в приёмнике.
    economy_log.record(economy_log.SHOP, 0, character_id=17)
    await asyncio.sleep(0)

    assert seen == [("fight", 124, 17, ""), ("shop", -50, 17, "малое зелье")]


async def test_a_broken_sink_does_not_raise_into_the_caller() -> None:
    async def sink(flow: str, amount: int, character_id: int, detail: str) -> None:
        raise RuntimeError("база отвалилась")

    economy_log.use_sink(sink)
    economy_log.record(economy_log.FIGHT, 10, character_id=1)  # не бросает
    await asyncio.sleep(0)


async def test_without_a_sink_record_is_just_the_log_line() -> None:
    # Ничего не подключено — ничего и не должно случиться, кроме журнальной строки.
    economy_log.record(economy_log.FIGHT, 10, character_id=1)
    await asyncio.sleep(0)
