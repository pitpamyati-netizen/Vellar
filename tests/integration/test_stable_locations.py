"""Карта сохраняется при пополнении, чтении старого мира и сбое записи."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.domain.entities.location import NodeState
from mmorpg.domain.rules.nodes import RESPAWN_SECONDS, location_epoch
from mmorpg.infrastructure.cache.redis_cache import RedisLocationStateCache, _encode
from mmorpg.infrastructure.persistence.world import PostgresLocationState
from mmorpg.presentation.telegram.flows.play import LocationSession, build_location, node_pack_seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
CITY = "farhold"
SLOT = 1
KEY = f"loc:{CITY}:{SLOT}"
NOW = 10_000
SEED = "stable-locations-test"


@pytest_asyncio.fixture(loop_scope="session", params=["postgres", "redis"])
async def world(request, pool, redis):
    pool.economic_cache = redis

    async def clean():
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key=$1 OR key LIKE $2", KEY, KEY + ":%"
        )
        keys = [key async for key in redis.scan_iter(match=KEY + "*")]
        if keys:
            await redis.delete(*keys)

    await clean()
    try:
        yield (
            PostgresLocationState(pool)
            if request.param == "postgres"
            else RedisLocationStateCache(redis)
        )
    finally:
        await clean()


async def test_old_nonzero_layout_is_pinned_and_survives_refills(world, content):
    # Старый мир: две выработанные раскладки и смещение после сброса смотрителем.
    for index in range(1, 5):
        await world._client.hset(KEY, str(index), _encode(NodeState(wave=6)))
    await world._client.set(KEY + ":generation", "3")
    old_session = LocationSession(city_id=CITY, slot=SLOT, node=1, epoch=5)
    old_map = build_location(content, SEED, old_session, epoch=5)

    pinned = await world.state(CITY, SLOT, now=NOW)
    assert location_epoch(pinned) == 5
    assert pinned.nodes == {index: NodeState(wave=6) for index in range(1, 5)}
    assert build_location(content, SEED, old_session) == old_map

    for wave in range(6, 21):
        tick = NOW + (wave - 6) * (RESPAWN_SECONDS + 1)
        for index in range(1, 5):
            taken = await world.take(CITY, SLOT, index, wave=wave, size=1, now=tick, ttl=3600)
            assert location_epoch(taken) == 5
        pinned = await world.state(CITY, SLOT, now=tick + RESPAWN_SECONDS)
        assert all(node.wave == wave + 1 for node in pinned.nodes.values())
        assert location_epoch(pinned) == 5
        assert build_location(content, SEED, old_session, epoch=location_epoch(pinned)) == old_map

    # Новый экземпляр хранилища, в том числе после исчезновения только данных узлов.
    reopened = (
        type(world)(world.operations.pool.raw)
        if isinstance(world, PostgresLocationState)
        else RedisLocationStateCache(world._client.raw)
    )
    assert location_epoch(await reopened.state(CITY, SLOT, now=NOW)) == 5
    await world._client.delete(KEY)
    assert location_epoch(await reopened.state(CITY, SLOT, now=NOW)) == 5


async def test_read_at_refill_boundary_pins_the_legacy_visible_layout_once(world):
    for index in range(1, 13):
        await world._client.hset(KEY, str(index), _encode(NodeState(emptied_at=NOW)))
    pinned = await world.state(CITY, SLOT, now=NOW + RESPAWN_SECONDS)
    assert location_epoch(pinned) == 1
    assert len(pinned.nodes) == 12
    # Более раннее время не возвращает нулевую карту; смена волн её не двигает.
    assert location_epoch(await world.state(CITY, SLOT, now=NOW)) == 1


async def test_first_take_preserves_a_legacy_layout_without_a_prior_read(world):
    await world._client.hset(KEY, "1", _encode(NodeState(wave=24)))
    taken = await world.take(CITY, SLOT, 2, wave=0, size=3, now=NOW, ttl=3600)
    assert location_epoch(taken) == 2
    assert taken.node(1).wave == 24
    assert taken.node(2).taken == 1
    assert location_epoch(await world.state(CITY, SLOT, now=NOW)) == 2


async def test_other_nodes_refilling_do_not_change_a_waiting_pack(world, content):
    original = await world.state(CITY, SLOT, now=NOW)
    session = LocationSession(city_id=CITY, slot=SLOT, node=1, epoch=location_epoch(original))
    seed = node_pack_seed(SEED, session, index=1, wave=0, place=0)
    location = build_location(content, SEED, session)
    await world.engage(
        CITY,
        SLOT,
        1,
        wave=0,
        place=0,
        epoch=session.epoch,
        battle_id="waiting",
        name="Аргус",
        character_id=1,
        now=NOW,
        ttl=3600,
    )
    for index in range(2, 15):
        await world.take(CITY, SLOT, index, wave=0, size=1, now=NOW, ttl=3600)
    refilled = await world.state(CITY, SLOT, now=NOW + RESPAWN_SECONDS)
    assert sum(node.wave for node in refilled.nodes.values()) == 13
    current_session = replace(session, epoch=location_epoch(refilled))
    assert build_location(content, SEED, current_session) == location
    assert node_pack_seed(SEED, current_session, index=1, wave=0, place=0) == seed
    held = await world.engaged_at(
        CITY, SLOT, 1, wave=0, epoch=location_epoch(refilled), now=NOW, ttl=3600
    )
    assert len(held) == 1 and held[0].battle_id == "waiting"


async def test_postgres_pinning_rolls_back_with_the_command_and_retries(pool, redis):
    world = PostgresLocationState(pool)
    pool.economic_cache = redis
    await pool.execute("DELETE FROM gameplay_state WHERE key=$1 OR key LIKE $2", KEY, KEY + ":%")
    await world._client.hset(KEY, "1", _encode(NodeState(wave=24)))

    @atomic_action
    async def interrupted(world: PostgresLocationState) -> None:
        assert location_epoch(await world.state(CITY, SLOT, now=NOW)) == 2
        raise RuntimeError("before commit")

    try:
        with pytest.raises(RuntimeError, match="before commit"):
            await interrupted(world)
        assert await world._client.get(KEY + ":layout-generation") is None
        # Повторные и одновременные первые чтения фиксируют один прежний мир.
        states = await asyncio.gather(*(world.state(CITY, SLOT, now=NOW) for _ in range(8)))
        assert all(location_epoch(state) == 2 for state in states)
        await redis.flushdb()
        assert location_epoch(await PostgresLocationState(pool).state(CITY, SLOT, now=NOW)) == 2
    finally:
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key=$1 OR key LIKE $2", KEY, KEY + ":%"
        )
