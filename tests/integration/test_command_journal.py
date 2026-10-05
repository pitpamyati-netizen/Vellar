"""Постоянный результат команды на PostgreSQL после потери Redis и рестарта."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
import pytest_asyncio

from mmorpg.application.operations import OperationCommittedError, OperationKeyConflictError
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.postgres import PostgresCharacterRepository
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_command_journal import RecordingSession, dependencies, update

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def command_env(economy):  # noqa: F811
    from aiogram import Bot

    from mmorpg.presentation.telegram.cleanup import MessageReaper
    from mmorpg.presentation.telegram.middlewares.operations import EconomicSendMiddleware

    e = economy
    e.session = RecordingSession()
    e.bot = Bot("654321:ABCDEF-test", session=e.session)
    e.bot.session.middleware(EconomicSendMiddleware())
    e.reaper = MessageReaper()
    e.journal = CommandMiddleware(dependencies(e.characters), e.reaper)
    e.event = update(e.bot, number=e.heroes[0].id, chat_id=e.ids[0], text="передать 50 золота")
    e.key = f"command:telegram:{e.bot.id}:{e.ids[0]}:{e.heroes[0].id}"
    e.cache = RedisStateCache(e.redis)
    e.cache_key = f"m02-command:{e.heroes[0].id}"
    e.calls = 0

    async def action(event, data):
        e.calls += 1
        hero = await e.characters.get(e.heroes[0].id)
        await e.characters.save(replace(hero, gold=hero.gold + 50))
        await e.cache.set(e.cache_key, "settled", 60)
        await event.message.answer("Получено 50 золота.", parse_mode=None)

    e.action = action
    try:
        yield e
    finally:
        await e.cache.delete(e.cache_key)
        await e.reaper.aclose()
        await e.bot.session.close()


async def test_restart_and_total_redis_loss_do_not_repeat_command(command_env):
    e = command_env
    await e.journal(e.action, e.event, {"bot": e.bot})
    await e.redis.flushdb()  # Фикстура разрешает только отдельный сервер испытаний.
    restarted = CommandMiddleware(dependencies(PostgresCharacterRepository(e.pool)), e.reaper)
    await restarted(e.action, e.event, {"bot": e.bot})
    assert e.calls == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    row = await e.pool.fetchrow("SELECT * FROM economic_operations WHERE id=$1", e.key)
    assert row["completed"] and row["cache_applied"]
    assert json.loads(row["result"])["replies"][0]["text"] == "Получено 50 золота."
    assert e.event.message.text not in row["result"]
    assert await e.cache.get(e.cache_key) is None


async def test_failure_before_commit_rolls_back_journal_effect_and_cache(command_env):
    e = command_env

    async def failing(event, data):
        await e.action(event, data)
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError):
        await e.journal(failing, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert not await e.pool.fetchval("SELECT completed FROM economic_operations WHERE id=$1", e.key)
    assert await e.cache.get(e.cache_key) is None
    assert not e.session.sent
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 550


async def test_concurrent_same_message_with_new_adapters_only_applies_once(command_env):
    e = command_env
    other = CommandMiddleware(dependencies(PostgresCharacterRepository(e.pool)), e.reaper)
    await asyncio.gather(
        e.journal(e.action, e.event, {"bot": e.bot}),
        other(e.action, e.event, {"bot": e.bot}),
    )
    assert e.calls == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 550


async def test_missing_reply_after_commit_is_replayed_without_effect(command_env):
    e = command_env
    e.session.fail = True
    with pytest.raises(OperationCommittedError):
        await e.journal(e.action, e.event, {"bot": e.bot})
    e.session.fail = False
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert e.calls == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 550


async def test_changed_input_cannot_replace_saved_result(command_env):
    e = command_env
    await e.journal(e.action, e.event, {"bot": e.bot})
    changed = update(e.bot, number=e.heroes[0].id, chat_id=e.ids[0], text="передать 100 золота")
    with pytest.raises(OperationKeyConflictError):
        await e.journal(e.action, changed, {"bot": e.bot})
    assert e.calls == 1


async def test_pre_m02_economic_message_is_not_executed_again(command_env):
    e = command_env
    legacy_id = e.key.removeprefix("command:")
    await e.pool.execute(
        "INSERT INTO economic_operations(id, kind, completed) VALUES($1, 'old.handler', true)",
        legacy_id,
    )
    await e.journal(e.action, e.event, {"bot": e.bot})
    assert e.calls == 0
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert "уже сохранено" in e.session.sent[0].text


async def test_command_recovery_finishes_cached_screen_before_current_route(
    command_env, monkeypatch
):
    import mmorpg.infrastructure.persistence.operations as operations

    e = command_env
    original = operations.apply_changes

    async def fail(*args):
        raise ConnectionError("controlled failure after commit")

    monkeypatch.setattr(operations, "apply_changes", fail)
    with pytest.raises(OperationCommittedError):
        await e.journal(e.action, e.event, {"bot": e.bot})
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    assert await e.cache.get(e.cache_key) is None
    monkeypatch.setattr(operations, "apply_changes", original)
    restarted = CommandMiddleware(dependencies(PostgresCharacterRepository(e.pool)), e.reaper)

    async def wrong_route(*args):
        raise AssertionError("current handler must not run for a saved command")

    await restarted(wrong_route, e.event, {"bot": e.bot})
    assert await e.cache.get(e.cache_key) == "settled"
    assert e.calls == 1


async def test_result_older_than_cache_retention_remains_replayable(command_env):
    e = command_env
    await e.journal(e.action, e.event, {"bot": e.bot})
    await e.pool.execute(
        "UPDATE economic_operations SET created_at=now()-interval '400 days' WHERE id=$1", e.key
    )
    restarted = CommandMiddleware(dependencies(PostgresCharacterRepository(e.pool)), e.reaper)
    await restarted(e.action, e.event, {"bot": e.bot})
    assert e.calls == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 550


async def test_saved_keeper_edit_recovers_for_next_command(command_env, content, monkeypatch):
    import mmorpg.infrastructure.persistence.operations as operations
    from mmorpg.application.services.content import ContentRegistry
    from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
    from mmorpg.infrastructure.persistence.postgres import PostgresContentOverlayRepository

    e = command_env
    overlays = PostgresContentOverlayRepository(e.pool)
    registry = ContentRegistry(content)
    journal = CommandMiddleware(
        dependencies(e.characters, registry=registry, overlays=overlays), e.reaper
    )
    edit = OverlayRecord(
        kind=OverlayKind.NPC,
        entity_id=f"m02_npc_{e.heroes[0].id}",
        fields={"name": "Довен", "city": "farhold", "role": "писарь заставы"},
    )
    original = operations.apply_changes

    async def fail(*args):
        raise ConnectionError("controlled failure after commit")

    async def action(event, data):
        await overlays.put(edit)
        await registry.reload(overlays)
        await e.cache.set(e.cache_key, "settled", 60)

    try:
        monkeypatch.setattr(operations, "apply_changes", fail)
        with pytest.raises(OperationCommittedError):
            await journal(action, e.event, {"bot": e.bot})
        assert not registry.current.has_npc(edit.entity_id)
        monkeypatch.setattr(operations, "apply_changes", original)

        async def new_action(event, data):
            assert registry.current.has_npc(edit.entity_id)

        other = update(
            e.bot, number=e.heroes[0].id + 100_000_000, chat_id=e.ids[0], text="передать 10 золота"
        )
        await journal(new_action, other, {"bot": e.bot})
        assert registry.current.has_npc(edit.entity_id)
    finally:
        monkeypatch.setattr(operations, "apply_changes", original)
        await overlays.forget(edit.kind, edit.entity_id)
