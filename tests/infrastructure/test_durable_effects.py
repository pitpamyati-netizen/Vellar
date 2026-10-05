"""Период меняет ключ, очистка временного состояния не возвращает ценности."""

from __future__ import annotations

import pytest

from mmorpg.application.operations import Operation
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.effects import EFFECT_SCOPES, MemoryEffectState


@pytest.mark.parametrize("scope", sorted(EFFECT_SCOPES))
async def test_period_records_ignore_ttl_but_invitations_expire(scope):
    now = [100.0]
    temporary = InMemoryStateCache(clock=lambda: now[0])
    state = MemoryEffectState(temporary)
    await state.set(f"{scope}:1:5", "17", ttl=1)
    await state.set("guild-call:1", "2", ttl=1)
    now[0] += 400
    assert await state.get(f"{scope}:1:5") == "17"
    assert await state.get(f"{scope}:1:6") is None
    assert await state.get("guild-call:1") is None


async def test_aborted_operation_does_not_leave_a_paid_mark():
    from pydantic import TypeAdapter

    state = MemoryEffectState(InMemoryStateCache())

    async def broken():
        await state.set("digest:1:5", "1", 10)
        raise RuntimeError("before commit")

    with pytest.raises(RuntimeError, match="before commit"):
        await state.operations.run(
            broken,
            operation=Operation(id="broken", kind="test"),
            fingerprint="test",
            codec=TypeAdapter(type(None)),
            participants=(state,),
        )
    assert await state.get("digest:1:5") is None


async def test_live_legacy_mark_is_preserved_and_economic_cleanup_is_rejected():
    temporary = InMemoryStateCache()
    await temporary.set("guild-taken:1:2:5", "60", 100)
    state = MemoryEffectState(temporary)
    assert await state.get("guild-taken:1:2:5") == "60"
    await temporary.delete("guild-taken:1:2:5")
    assert await state.get("guild-taken:1:2:5") == "60"
    with pytest.raises(ValueError, match="cannot be deleted"):
        await state.delete("guild-taken:1:2:5")


@pytest.mark.parametrize("value", ["", "-1", "wrong", "١"])
async def test_invalid_counter_is_not_silently_reset(value):
    state = MemoryEffectState(InMemoryStateCache())
    with pytest.raises(ValueError, match="non-negative integer"):
        await state.set("guild-items:1:2:5", value, 10)
