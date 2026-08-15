"""Что панель делает с хранилищами, и что при этом видит игра.

Реестр содержимого и уборка проверяются вместе с адаптерами в памяти: обе вещи
существуют ради одного — правка должна быть видна со следующего нажатия, а не с
перезапуска, и стирать должна ровно то, что обещала.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from mmorpg.application.services import keeper_panel
from mmorpg.application.services.content import ContentRegistry
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.ports.repositories import User
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryContentOverlayRepository,
    InMemoryUserRepository,
)

NOW = 1_700_000_000
DAY = 24 * 60 * 60

DOVEN = OverlayRecord(
    kind=OverlayKind.NPC,
    entity_id="keeper_npc_1",
    fields={"name": "Довен", "city": "farhold", "role": "писарь заставы"},
)


@pytest.fixture
def registry(content: GameContent) -> ContentRegistry:
    return ContentRegistry(content)


@pytest.fixture
def overlays() -> InMemoryContentOverlayRepository:
    return InMemoryContentOverlayRepository()


# --- реестр ------------------------------------------------------------


async def test_an_edit_is_visible_without_a_restart(
    registry: ContentRegistry, overlays: InMemoryContentOverlayRepository
) -> None:
    assert registry.current.npcs == ()

    await keeper_panel.save_edit(overlays, registry, DOVEN)

    assert registry.current.npcs_in("farhold")[0].name == "Довен"
    # Прочитанное из content не тронуто: правка лежит поверх, а не вместо.
    assert registry.base.npcs == ()


async def test_dropping_an_edit_gives_back_exactly_the_world_from_content(
    registry: ContentRegistry, overlays: InMemoryContentOverlayRepository
) -> None:
    await keeper_panel.save_edit(overlays, registry, DOVEN)

    assert await keeper_panel.drop_edit(overlays, registry, OverlayKind.NPC, DOVEN.entity_id)

    assert registry.current.npcs == ()
    assert registry.records == ()


async def test_dropping_an_edit_that_was_not_there_says_so(
    registry: ContentRegistry, overlays: InMemoryContentOverlayRepository
) -> None:
    assert await keeper_panel.drop_edit(overlays, registry, OverlayKind.NPC, "нет такого") is False


async def test_a_half_written_edit_is_stored_together_with_its_reason(
    registry: ContentRegistry, overlays: InMemoryContentOverlayRepository
) -> None:
    """Недописанная правка — работа на середине, а не ошибка."""
    half = OverlayRecord(kind=OverlayKind.NPC, entity_id="keeper_npc_2", fields={"city": "farhold"})

    why = await keeper_panel.save_edit(overlays, registry, half)

    assert why
    assert len(registry.records) == 1
    assert registry.current.npcs == ()
    assert [record for record, _ in registry.problems()] == [half]


async def test_reloading_reads_everything_that_was_written(
    registry: ContentRegistry, overlays: InMemoryContentOverlayRepository
) -> None:
    await overlays.put(DOVEN)

    assert await registry.reload(overlays) == 1
    assert registry.current.has_npc(DOVEN.entity_id)


# --- уборка ------------------------------------------------------------


def a_character(user_id: int, name: str, **fields: object) -> Character:
    return replace(
        Character(id=0, user_id=user_id, name=name, race_id="human", class_id="warrior"),
        **fields,  # type: ignore[arg-type]
    )


async def test_only_the_untouched_are_swept_away() -> None:
    characters = InMemoryCharacterRepository()
    played = await characters.create(a_character(1, "Игравший", level=4, experience=900))
    fresh = await characters.create(a_character(2, "Брошенный"))

    # Хранилище в памяти отмечает время само, поэтому «сейчас» здесь настоящее.
    now = int(time.time())
    swept = await keeper_panel.sweep_drafts(characters, now=now)

    # Только что созданный ещё не брошен: у него есть неделя.
    assert swept.removed == 0
    assert await characters.get(fresh.id) is not None

    later = now + (keeper_panel.ABANDONED_AFTER_DAYS + 1) * DAY
    swept = await keeper_panel.sweep_drafts(characters, now=later)

    assert swept.removed == 1
    assert await characters.get(fresh.id) is None
    assert await characters.get(played.id) is not None


async def test_a_sweep_asks_telegram_once_per_account_and_remembers_the_answer() -> None:
    users = InMemoryUserRepository()
    for telegram_id in (10, 11, 12):
        await users.upsert(User(telegram_id=telegram_id))
    asked: list[int] = []

    async def probe(telegram_id: int) -> bool:
        asked.append(telegram_id)
        return telegram_id != 11

    swept = await keeper_panel.sweep_blocked(users, probe, now=NOW)

    assert asked == [10, 11, 12]
    assert (swept.checked, swept.blocked) == (3, 1)
    assert await users.blocked_count() == 1
    # Второй проход тем же часом никого не спрашивает заново.
    assert (await keeper_panel.sweep_blocked(users, probe, now=NOW)).checked == 0


async def test_a_sweep_never_takes_more_than_a_batch() -> None:
    users = InMemoryUserRepository()
    for telegram_id in range(keeper_panel.SWEEP_BATCH + 10):
        await users.upsert(User(telegram_id=telegram_id))

    async def probe(telegram_id: int) -> bool:
        return True

    swept = await keeper_panel.sweep_blocked(users, probe, now=NOW)

    assert swept.checked == keeper_panel.SWEEP_BATCH


async def test_blocked_accounts_are_dropped_only_when_asked() -> None:
    users = InMemoryUserRepository()
    await users.upsert(User(telegram_id=10))
    await users.mark_checked(10, at=NOW, blocked=True)

    assert await users.blocked_count() == 1
    assert (await keeper_panel.drop_blocked(users)).removed == 1
    assert await users.get(10) is None
    assert await users.blocked_count() == 0
