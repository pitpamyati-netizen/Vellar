"""Адаптеры Redis против настоящего Redis.

Их близнецы в памяти - это словари Python, и разойтись сами с собой они не
могут. Здесь проверяется то, что решает только настоящий сервер: что возвращает
`SET NX`, что срок и правда проставлен и что целое число, записанное одним
вызовом, читается целым числом в следующем.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from mmorpg.domain.entities.location import LocationState, NodeState, Presence, Roamer
from mmorpg.domain.rules.nodes import RESPAWN_SECONDS
from mmorpg.infrastructure.cache.redis_cache import (
    RedisIdempotencyStore,
    RedisLocationStateCache,
    RedisStateCache,
)

# Один цикл событий на весь пакет: соединения открываются раз на прогон
# (``conftest.py``), а привязаны они к тому циклу, в котором созданы.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# Идентификатор города, которого нет ни в одном файле содержимого, чтобы эти ключи не
# столкнулись с работающей игрой.
TEST_CITY = "__test_city"
TEST_CHARACTER = -999_002


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean_keys(redis):
    """Убрать ключи этого теста до и после, не тронув ни одного чужого."""

    async def purge() -> None:
        for pattern in (f"loc:{TEST_CITY}:*", "upd:-9990*", "__test_state:*"):
            keys = [key async for key in redis.scan_iter(match=pattern)]
            if keys:
                await redis.delete(*keys)

    await purge()
    yield
    await purge()


# --- кэш состояния ---------------------------------------------------------


async def test_a_value_round_trips_as_text(redis) -> None:
    """Redis отдаёт байты; порт обещает str."""
    cache = RedisStateCache(redis)
    await cache.set("__test_state:screen", "город", ttl=60)

    value = await cache.get("__test_state:screen")
    assert value == "город"
    assert isinstance(value, str)


async def test_a_missing_key_is_none_not_an_error(redis) -> None:
    assert await RedisStateCache(redis).get("__test_state:never-written") is None


async def test_a_stored_value_carries_its_ttl(redis) -> None:
    """Без срока эти ключи копились бы на каждого игрока вечно."""
    await RedisStateCache(redis).set("__test_state:ttl", "x", ttl=60)
    assert 0 < await redis.ttl("__test_state:ttl") <= 60


async def test_deleting_removes_the_key(redis) -> None:
    cache = RedisStateCache(redis)
    await cache.set("__test_state:gone", "x", ttl=60)
    await cache.delete("__test_state:gone")
    assert await cache.get("__test_state:gone") is None


# --- общее состояние локации -----------------------------------------------


async def test_what_is_taken_out_of_a_node_is_counted(redis) -> None:
    locations = RedisLocationStateCache(redis)
    assert await locations.state(TEST_CITY, 1, now=1_000) == LocationState()

    await locations.take(TEST_CITY, 1, 0, wave=0, size=3, now=1_000, ttl=60)
    state = await locations.take(TEST_CITY, 1, 3, wave=0, size=3, now=1_000, ttl=60)

    assert state.node(0).taken == 1
    assert state.node(3).taken == 1
    # Записано текстом, прочитано числами, и всё это через текстовый протокол.
    assert await locations.state(TEST_CITY, 1, now=1_000) == state


async def test_an_emptied_node_fills_up_again_three_minutes_later(redis) -> None:
    locations = RedisLocationStateCache(redis)
    for _ in range(2):
        state = await locations.take(TEST_CITY, 1, 2, wave=0, size=2, now=1_000, ttl=60)
    assert state.node(2).empty

    waiting = await locations.state(TEST_CITY, 1, now=1_000 + RESPAWN_SECONDS - 1)
    assert waiting.node(2).empty

    filled = await locations.state(TEST_CITY, 1, now=1_000 + RESPAWN_SECONDS)
    assert not filled.node(2).empty
    assert filled.node(2).wave == 1


async def test_a_press_from_an_older_wave_takes_nothing(redis) -> None:
    """Двое, вычищающие последнюю стаю разом, не должны вычистить её дважды."""
    locations = RedisLocationStateCache(redis)
    await locations.take(TEST_CITY, 1, 1, wave=0, size=1, now=1_000, ttl=60)
    late = await locations.take(
        TEST_CITY, 1, 1, wave=0, size=1, now=1_000 + RESPAWN_SECONDS, ttl=60
    )
    assert late.node(1) == NodeState(wave=1, taken_slots=0, emptied_at=0)


async def test_a_pack_is_held_by_the_first_fight_only(redis) -> None:
    """``SETNX`` в хеше: двое, нажавших на одного волка разом, дерутся за одного (ADR 0065)."""
    locations = RedisLocationStateCache(redis)
    taken = await locations.engage(
        TEST_CITY,
        1,
        5,
        wave=0,
        place=1,
        battle_id="1-1000",
        name="Алина",
        character_id=TEST_CHARACTER,
        now=1_000,
        ttl=60,
    )
    assert taken is None

    held = await locations.engage(
        TEST_CITY,
        1,
        5,
        wave=0,
        place=1,
        battle_id="2-1001",
        name="Мирна",
        character_id=TEST_CHARACTER + 1,
        now=1_001,
        ttl=60,
    )
    assert held is not None and held.battle_id == "1-1000"

    seen = await locations.engaged_at(TEST_CITY, 1, 5, wave=0, now=1_001, ttl=60)
    assert [(one.slot, one.name) for one in seen] == [(1, "Алина")]
    assert await locations.engaged_at(TEST_CITY, 1, 5, wave=1, now=1_001, ttl=60) == ()

    await locations.disengage(TEST_CITY, 1, 5, wave=0, place=1)
    assert await locations.engaged_at(TEST_CITY, 1, 5, wave=0, now=1_001, ttl=60) == ()


async def test_a_stale_hold_is_forgotten(redis) -> None:
    locations = RedisLocationStateCache(redis)
    await locations.engage(
        TEST_CITY,
        1,
        6,
        wave=0,
        place=0,
        battle_id="1-1000",
        name="Алина",
        character_id=TEST_CHARACTER,
        now=1_000,
        ttl=60,
    )
    assert await locations.engaged_at(TEST_CITY, 1, 6, wave=0, now=1_100, ttl=60) == ()


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


async def test_a_roamer_spawns_once_and_only_one_party_holds_it(redis) -> None:
    locations = RedisLocationStateCache(redis)
    rift = Roamer(node=3, group=False, difficulty="delve", level=7, stamp=1)
    first = await locations.spawn_roamer(TEST_CITY, 1, rift, ttl=60)
    second = await locations.spawn_roamer(
        TEST_CITY, 1, Roamer(node=5, group=True, difficulty="grim", level=7, stamp=2), ttl=60
    )
    assert first.node == second.node == 3

    assert await locations.claim_roamer(TEST_CITY, 1, 7, ttl=60) is True
    assert await locations.claim_roamer(TEST_CITY, 1, 8, ttl=60) is False
    assert (await locations.roamer(TEST_CITY, 1, now=0)).holder == 7

    await locations.release_roamer(TEST_CITY, 1)
    assert await locations.claim_roamer(TEST_CITY, 1, 8, ttl=60) is True

    await locations.clear_roamer(TEST_CITY, 1)
    assert await locations.roamer(TEST_CITY, 1, now=0) is None


async def test_the_keeper_resets_a_location_wiping_waves_and_the_roamer(redis) -> None:
    locations = RedisLocationStateCache(redis)
    await locations.take(TEST_CITY, 1, 3, wave=0, size=1, now=0, ttl=60)
    await locations.spawn_roamer(
        TEST_CITY, 1, Roamer(node=2, group=False, difficulty="delve", level=7, stamp=9), ttl=60
    )
    await locations.claim_roamer(TEST_CITY, 1, 7, ttl=60)

    await locations.reset(TEST_CITY, 1)

    assert (await locations.state(TEST_CITY, 1, now=0)).nodes == {}
    assert await locations.roamer(TEST_CITY, 1, now=0) is None


# --- отсев повторов --------------------------------------------------------


async def test_the_first_writer_wins_and_the_rest_are_duplicates(redis) -> None:
    """Повторно доставленное обновление не должно сработать дважды."""
    store = RedisIdempotencyStore(redis)
    assert await store.seen(-999_001, ttl=60) is False
    assert await store.seen(-999_001, ttl=60) is True
    assert await store.seen(-999_002, ttl=60) is False
