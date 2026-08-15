"""The Redis adapters against a real Redis.

The in-memory equivalents are Python dicts and cannot disagree with themselves.
These check the parts that only a real server decides: what `SET NX` returns, that
a TTL is actually attached, and that an integer written by one call reads back as
an integer in the next.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from mmorpg.domain.entities.location import LocationState, Presence
from mmorpg.infrastructure.cache.redis_cache import (
    RedisIdempotencyStore,
    RedisLocationStateCache,
    RedisStateCache,
)

# Один цикл событий на весь пакет: соединения открываются раз на прогон
# (``conftest.py``), а привязаны они к тому циклу, в котором созданы.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# A city id no content file uses, so these keys cannot collide with a running game.
TEST_CITY = "__test_city"
TEST_CHARACTER = -999_002


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean_keys(redis):
    """Remove this test's keys before and after, and leave every other key alone."""

    async def purge() -> None:
        for pattern in (f"loc:{TEST_CITY}:*", "upd:-9990*", "__test_state:*"):
            keys = [key async for key in redis.scan_iter(match=pattern)]
            if keys:
                await redis.delete(*keys)

    await purge()
    yield
    await purge()


# --- state cache -------------------------------------------------------------


async def test_a_value_round_trips_as_text(redis) -> None:
    """Redis hands back bytes; the port promises str."""
    cache = RedisStateCache(redis)
    await cache.set("__test_state:screen", "город", ttl=60)

    value = await cache.get("__test_state:screen")
    assert value == "город"
    assert isinstance(value, str)


async def test_a_missing_key_is_none_not_an_error(redis) -> None:
    assert await RedisStateCache(redis).get("__test_state:never-written") is None


async def test_a_stored_value_carries_its_ttl(redis) -> None:
    """Without the TTL these keys would accumulate for every player, forever."""
    await RedisStateCache(redis).set("__test_state:ttl", "x", ttl=60)
    assert 0 < await redis.ttl("__test_state:ttl") <= 60


async def test_deleting_removes_the_key(redis) -> None:
    cache = RedisStateCache(redis)
    await cache.set("__test_state:gone", "x", ttl=60)
    await cache.delete("__test_state:gone")
    assert await cache.get("__test_state:gone") is None


# --- the shared state of a location -----------------------------------------


async def test_cleared_nodes_accumulate_in_the_mask(redis) -> None:
    locations = RedisLocationStateCache(redis)
    assert await locations.state(TEST_CITY, 1) == LocationState()

    await locations.mark_cleared(TEST_CITY, 1, generation=0, node=0, ttl=60)
    state = await locations.mark_cleared(TEST_CITY, 1, generation=0, node=3, ttl=60)

    assert state.cleared == 0b1001
    # Written as integers, read back as integers, through a text protocol.
    assert await locations.state(TEST_CITY, 1) == state


async def test_marking_the_same_node_twice_changes_nothing(redis) -> None:
    locations = RedisLocationStateCache(redis)
    await locations.mark_cleared(TEST_CITY, 1, generation=0, node=2, ttl=60)
    state = await locations.mark_cleared(TEST_CITY, 1, generation=0, node=2, ttl=60)
    assert state.cleared == 0b100


async def test_a_cleared_location_rolls_over_exactly_once(redis) -> None:
    """Two players finishing the last node together must not roll it twice."""
    locations = RedisLocationStateCache(redis)
    await locations.mark_cleared(TEST_CITY, 1, generation=0, node=1, ttl=60)

    rolled = await locations.rotate(TEST_CITY, 1, generation=0, ttl=60)
    assert rolled == LocationState(generation=1, cleared=0)
    assert await locations.rotate(TEST_CITY, 1, generation=0, ttl=60) == rolled


async def test_people_in_a_location_are_seen_by_node(redis) -> None:
    locations = RedisLocationStateCache(redis)
    await locations.arrive(
        TEST_CITY, 1, Presence(TEST_CHARACTER, "Мерла", 12, node=2), now=1000, ttl=600
    )
    here = await locations.others_at(TEST_CITY, 1, 2, exclude=0, now=1000, ttl=600)
    assert [presence.name for presence in here] == ["Мерла"]
    assert await locations.others_at(TEST_CITY, 1, 3, exclude=0, now=1000, ttl=600) == ()

    await locations.leave(TEST_CITY, 1, TEST_CHARACTER)
    assert await locations.others_at(TEST_CITY, 1, 2, exclude=0, now=1000, ttl=600) == ()


async def test_a_stale_presence_is_forgotten(redis) -> None:
    locations = RedisLocationStateCache(redis)
    await locations.arrive(
        TEST_CITY, 1, Presence(TEST_CHARACTER, "Мерла", 12, node=2), now=1000, ttl=600
    )
    assert await locations.others_at(TEST_CITY, 1, 2, exclude=0, now=1601, ttl=600) == ()


# --- idempotency -------------------------------------------------------------


async def test_the_first_writer_wins_and_the_rest_are_duplicates(redis) -> None:
    """A redelivered update must never apply an effect twice."""
    store = RedisIdempotencyStore(redis)
    assert await store.seen(-999_001, ttl=60) is False
    assert await store.seen(-999_001, ttl=60) is True
    assert await store.seen(-999_002, ttl=60) is False
