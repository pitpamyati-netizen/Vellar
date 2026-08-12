"""The Redis adapters against a real Redis.

The in-memory equivalents are Python dicts and cannot disagree with themselves.
These check the parts that only a real server decides: what `SET NX` returns, that
a TTL is actually attached, and that an integer written by one call reads back as
an integer in the next.
"""

from __future__ import annotations

import pytest

from mmorpg.infrastructure.cache.redis_cache import (
    RedisIdempotencyStore,
    RedisLocationDeltaCache,
    RedisStateCache,
)

pytestmark = pytest.mark.integration

# A city id no content file uses, so these keys cannot collide with a running game.
TEST_CITY = "__test_city"
TEST_CHARACTER = -999_002


@pytest.fixture(autouse=True)
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


# --- location deltas ---------------------------------------------------------


async def test_cleared_nodes_accumulate_in_the_mask(redis) -> None:
    deltas = RedisLocationDeltaCache(redis)
    assert await deltas.get_mask(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5) == 0

    await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=0, ttl=60)
    mask = await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=3, ttl=60)

    assert mask == 0b1001
    # Written as an integer, read back as an integer, through a text protocol.
    assert await deltas.get_mask(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5) == 0b1001


async def test_marking_the_same_node_twice_changes_nothing(redis) -> None:
    deltas = RedisLocationDeltaCache(redis)
    await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=2, ttl=60)
    mask = await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=2, ttl=60)
    assert mask == 0b100


async def test_each_cycle_starts_from_a_clean_location(redis) -> None:
    """The world regenerates every cycle, so last cycle's progress must not leak in."""
    deltas = RedisLocationDeltaCache(redis)
    await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=1, ttl=60)
    assert await deltas.get_mask(TEST_CHARACTER, TEST_CITY, slot=1, cycle=6) == 0


async def test_resetting_clears_the_mask(redis) -> None:
    deltas = RedisLocationDeltaCache(redis)
    await deltas.mark_cleared(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5, node=1, ttl=60)
    await deltas.reset(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5)
    assert await deltas.get_mask(TEST_CHARACTER, TEST_CITY, slot=1, cycle=5) == 0


# --- idempotency -------------------------------------------------------------


async def test_the_first_writer_wins_and_the_rest_are_duplicates(redis) -> None:
    """A redelivered update must never apply an effect twice."""
    store = RedisIdempotencyStore(redis)
    assert await store.seen(-999_001, ttl=60) is False
    assert await store.seen(-999_001, ttl=60) is True
    assert await store.seen(-999_002, ttl=60) is False
