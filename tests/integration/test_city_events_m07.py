"""Настоящий SQL: общая запись, отмена, повтор и отсутствие Redis."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.city_event import CityEvents
from mmorpg.domain.entities.city_event import CityEventState
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from tests.integration.test_durable_battle_m03 import battle_env as battle_env
from tests.integration.test_durable_battle_m03 import press
from tests.integration.test_economic_operations import economy as economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def town_sql(economy, content):
    e = economy
    await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'city-event:%'")
    e.events = CityEvents(
        content, PostgresGameplayState(e.pool, InMemoryStateCache()), e.characters, e.inventory
    )
    e.event = content.city_events[0]
    try:
        yield e
    finally:
        await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'city-event:%'")


@atomic_action
async def act(service, author_id, command, *, operation_id=None) -> str:
    if command.startswith("craft"):
        result = await service.craft(author_id, "farhold", 0, command)
    else:
        result = await service.claim(author_id, "farhold")
    if command.endswith("break"):
        raise RuntimeError("after event, inventory and character")
    return result


async def close_stage(e, stage):
    for index, hero in enumerate(e.heroes):
        await e.events.record(hero.id, "farhold", 1, "battle", f"{stage}:battle:{index}")
    await e.events.record(e.heroes[0].id, "farhold", 1, "scout", f"{stage}:scout")


async def test_world_work_and_cost_cancel_together_then_replay_new_adapter(town_sql):
    e = town_sql
    hero = e.heroes[0]
    await e.inventory.add(hero.id, "iron_scrap", 8)
    key = "m07:" + uuid4().hex
    with pytest.raises(RuntimeError):
        await act(e.events, hero.id, "craft-break", operation_id=key)
    assert await e.inventory.count(hero.id, "iron_scrap") == 8
    assert (await e.characters.get(hero.id)).crafts == hero.crafts
    assert await e.events.load(e.event) == CityEventState()
    assert not await e.pool.fetchval("SELECT 1 FROM economic_operations WHERE id=$1", key)
    key = "m07:" + uuid4().hex
    answer = await act(e.events, hero.id, "craft", operation_id=key)
    before = await e.inventory.count(hero.id, "iron_scrap")
    await e.redis.flushdb()
    fresh = CityEvents(
        e.events.content,
        PostgresGameplayState(e.pool, InMemoryStateCache()),
        e.characters,
        e.inventory,
    )
    assert await act(fresh, hero.id, "craft", operation_id=key) == answer
    assert await e.inventory.count(hero.id, "iron_scrap") == before
    assert len((await fresh.load(e.event)).contributions) == 1
    assert "уже" in await fresh.craft(hero.id, "farhold", 0, "craft")


async def test_concurrent_last_contributions_never_spill_to_next_stage(town_sql):
    e = town_sql
    await e.events.record(e.heroes[0].id, "farhold", 1, "battle", "one")
    await e.events.record(e.heroes[0].id, "farhold", 1, "battle", "two")
    await asyncio.gather(
        *(
            e.events.record(hero.id, "farhold", 1, "scout", f"last:{index}", expected_stage=0)
            for index, hero in enumerate(e.heroes)
        )
    )
    state = await e.events.load(e.event)
    assert state.stage == 1 and len(state.contributions) == 4
    assert not await e.events.record(e.heroes[0].id, "farhold", 1, "battle", "one")
    assert not await e.events.record(
        e.heroes[0].id, "farhold", 1, "battle", "old-stage", expected_stage=0
    )
    await close_stage(e, 1)
    assert (await e.events.load(e.event)).stage == 2
    assert await e.events.travel_discount("farhold") == 10


async def test_reward_failure_and_competing_claims_keep_one_payment(town_sql):
    e = town_sql
    await close_stage(e, 0)
    hero = e.heroes[0]
    with pytest.raises(RuntimeError):
        await act(e.events, hero.id, "claim-break", operation_id="m07:" + uuid4().hex)
    assert (await e.characters.get(hero.id)).gold == 500
    assert not (await e.events.load(e.event)).claimed
    await asyncio.gather(*(e.events.claim(hero.id, "farhold") for _ in range(4)))
    assert (await e.characters.get(hero.id)).gold == 520
    assert (await e.events.load(e.event)).claimed == ((0, hero.id),)
    await e.redis.flushdb()
    fresh = CityEvents(
        e.events.content,
        PostgresGameplayState(e.pool, InMemoryStateCache()),
        e.characters,
        e.inventory,
    )
    assert "нет" in await fresh.claim(hero.id, "farhold")
    await close_stage(e, 1)
    assert "20 золота" in await fresh.claim(hero.id, "farhold")
    assert (await e.characters.get(hero.id)).gold == 540


async def test_lost_commit_reply_reconciles_entitlement_before_repeat(town_sql, monkeypatch):
    from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool

    e = town_sql
    await close_stage(e, 0)
    original = ReconnectingPool.acquire
    injected = False

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        @asynccontextmanager
        async def transaction(self):
            nonlocal injected
            async with self.connection.transaction():
                yield
            if not injected:
                injected = True
                raise ConnectionResetError("controlled lost COMMIT reply")

    @asynccontextmanager
    async def acquire(pool):
        async with original(pool) as connection:
            yield Connection(connection)

    key = "m07:" + uuid4().hex
    with monkeypatch.context() as patch:
        patch.setattr(ReconnectingPool, "acquire", acquire)
        answer = await act(e.events, e.heroes[0].id, "claim", operation_id=key)
    assert injected
    assert "20 золота" in answer
    assert await act(e.events, e.heroes[0].id, "claim", operation_id=key) == answer
    assert (await e.characters.get(e.heroes[0].id)).gold == 520


async def test_remote_contribution_and_deleted_character_never_receive_values(town_sql):
    e = town_sql
    remote = await e.characters.save(replace(e.heroes[0], city_id=e.events.content.cities[1].id))
    assert not await e.events.record(remote.id, "farhold", 1, "battle", "remote")
    assert "приезжайте" in await e.events.craft(remote.id, "farhold", 0, "remote")
    assert "недоступно" in await e.events.claim(99999999, "farhold")


@pytest_asyncio.fixture(loop_scope="session")
async def city_battle(battle_env):
    e = battle_env
    await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'city-event:%'")
    e.events = CityEvents(e.content, e.cache, e.characters, e.inventory)
    e.event = e.content.city_events[0]
    try:
        yield e
    finally:
        await e.pool.execute("DELETE FROM gameplay_state WHERE key LIKE 'city-event:%'")


async def test_actual_battle_result_credits_active_winner_and_rolls_back_city_with_rewards(
    city_battle,
):
    e = city_battle
    combatants = tuple(
        replace(one, actions=1 if one.character_id == e.heroes[0].id else 0)
        if one.is_hero
        else replace(one, health=0)
        for one in e.session.state.combatants
    )
    e.session = await e.store.save(
        replace(
            e.session, participation_rule=1, state=replace(e.session.state, combatants=combatants)
        )
    )

    async def fail(point):
        if point == "after_world":
            raise RuntimeError("after rewards and city event")

    with pytest.raises(RuntimeError, match="after rewards"):
        await press(e, "итог города", hook=fail)
    assert await e.events.load(e.event) == CityEventState()
    assert (await e.characters.get(e.heroes[0].id)).gold == 500

    async def accept(point):
        pass

    await press(e, "итог города", hook=accept)
    saved = await e.events.load(e.event)
    assert len(saved.contributions) == 1
    assert saved.contributions[0].character_id == e.heroes[0].id
    assert saved.contributions[0].receipt == e.session.id
    await press(e, "повтор итога города", hook=accept)
    assert await e.events.load(e.event) == saved


async def test_verified_full_backup_includes_completed_city_and_claimed_entitlement(
    town_sql, tmp_path
):
    import json

    from scripts import backup
    from scripts.test_stand import load_test_settings

    e = town_sql
    await close_stage(e, 0)
    await close_stage(e, 1)
    await e.events.claim(e.heroes[0].id, "farhold")
    before = await e.events.load(e.event)
    file = await backup.create_backup(load_test_settings().postgres_dsn, tmp_path, keep=1)
    proof = json.loads(file.with_suffix(".verified.json").read_text(encoding="utf-8"))
    assert "gameplay_state" in str(proof)
    assert await e.events.load(e.event) == before
