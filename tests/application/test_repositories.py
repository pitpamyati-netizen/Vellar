"""Repository behaviour, verified against the in-memory adapters.

The PostgreSQL adapters implement the same protocols; their SQL is exercised by
integration tests marked ``integration`` and skipped when no database is running.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, StatBlock
from mmorpg.domain.ports import (
    AccessibilitySettings,
    CharacterRepository,
    IdempotencyStore,
    InventoryRepository,
    LocationDeltaCache,
    StateCache,
    User,
    UserRepository,
)
from mmorpg.infrastructure.cache import (
    InMemoryIdempotencyStore,
    InMemoryLocationDeltaCache,
    InMemoryStateCache,
)
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
    InMemoryUserRepository,
)


class FakeClock:
    """A clock the tests move by hand, so no test ever sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def a_character(user_id: int = 42, name: str = "Тест") -> Character:
    return Character(id=0, user_id=user_id, name=name, race_id="human", class_id="warrior")


# --- the adapters satisfy the ports ---------------------------------


def test_in_memory_adapters_implement_the_ports() -> None:
    assert isinstance(InMemoryUserRepository(), UserRepository)
    assert isinstance(InMemoryCharacterRepository(), CharacterRepository)
    assert isinstance(InMemoryInventoryRepository(), InventoryRepository)
    assert isinstance(InMemoryStateCache(), StateCache)
    assert isinstance(InMemoryLocationDeltaCache(), LocationDeltaCache)
    assert isinstance(InMemoryIdempotencyStore(), IdempotencyStore)


# --- users -----------------------------------------------------------


async def test_user_upsert_and_settings() -> None:
    users = InMemoryUserRepository()
    assert await users.get(1) is None

    stored = await users.upsert(User(telegram_id=1, username="player"))
    assert stored.username == "player"
    assert stored.settings.emoji is False, "emoji are off by default"

    await users.save_settings(1, AccessibilitySettings(emoji=True, verbose=False, page_size=8))
    reloaded = await users.get(1)
    assert reloaded is not None
    assert reloaded.settings.emoji is True
    assert reloaded.username == "player", "settings must not wipe the profile"


async def test_settings_can_be_saved_for_an_unknown_user() -> None:
    users = InMemoryUserRepository()
    await users.save_settings(7, AccessibilitySettings(emoji=True))
    stored = await users.get(7)
    assert stored is not None
    assert stored.settings.emoji is True


# --- characters ------------------------------------------------------


async def test_create_assigns_an_id() -> None:
    characters = InMemoryCharacterRepository()
    created = await characters.create(a_character())
    assert created.id > 0
    assert await characters.get(created.id) == created


async def test_save_round_trips_every_field() -> None:
    characters = InMemoryCharacterRepository()
    created = await characters.create(a_character())
    updated = replace(
        created,
        level=17,
        experience=99_000,
        gold=250,
        allocated=StatBlock(STR=4, LCK=2),
        trait_ids=("berserker", "born_lucky"),
        loadout=created.loadout.with_rank("warrior_cleave", 3).with_edge(
            "warrior_cleave", "warrior_cleave_a"
        ),
        equipment=created.equipment.equip("weapon", "rusty_sword"),
    )
    await characters.save(updated)

    reloaded = await characters.get(created.id)
    assert reloaded == updated
    assert reloaded is not None
    assert reloaded.loadout.rank_of("warrior_cleave") == 3
    assert reloaded.loadout.edge_of("warrior_cleave") == "warrior_cleave_a"
    assert reloaded.equipment.item_in("weapon") == "rusty_sword"


async def test_get_active_and_list_for_user() -> None:
    characters = InMemoryCharacterRepository()
    await characters.create(a_character(user_id=1, name="Первый"))
    await characters.create(a_character(user_id=2, name="Второй"))

    active = await characters.get_active(1)
    assert active is not None
    assert active.name == "Первый"
    assert len(await characters.list_for_user(1)) == 1
    assert await characters.get_active(999) is None


async def test_names_are_unique_case_insensitively() -> None:
    characters = InMemoryCharacterRepository()
    await characters.create(a_character(name="Аргус"))
    assert await characters.name_taken("аргус") is True
    assert await characters.name_taken("Аргуса") is False


# --- inventory -------------------------------------------------------


async def test_inventory_stacks_and_removes() -> None:
    inventory = InMemoryInventoryRepository()
    await inventory.add(1, "healing_potion", 3)
    await inventory.add(1, "healing_potion", 2)
    assert await inventory.count(1, "healing_potion") == 5

    assert await inventory.remove(1, "healing_potion", 4) is True
    assert await inventory.count(1, "healing_potion") == 1


async def test_removing_more_than_held_fails_without_changing_anything() -> None:
    inventory = InMemoryInventoryRepository()
    await inventory.add(1, "antidote", 1)
    assert await inventory.remove(1, "antidote", 5) is False
    assert await inventory.count(1, "antidote") == 1


async def test_empty_stacks_disappear_from_the_listing() -> None:
    inventory = InMemoryInventoryRepository()
    await inventory.add(1, "antidote", 1)
    await inventory.remove(1, "antidote", 1)
    assert await inventory.list_items(1) == ()


# --- caches ----------------------------------------------------------


async def test_state_cache_expires() -> None:
    clock = FakeClock()
    cache = InMemoryStateCache(clock=clock)
    await cache.set("combat:1", "{}", ttl=60)
    assert await cache.get("combat:1") == "{}"

    clock.advance(61)
    assert await cache.get("combat:1") is None


async def test_state_cache_delete() -> None:
    cache = InMemoryStateCache()
    await cache.set("screen:1", "city", ttl=60)
    await cache.delete("screen:1")
    assert await cache.get("screen:1") is None


async def test_location_deltas_accumulate_in_one_bitmask() -> None:
    cache = InMemoryLocationDeltaCache()
    assert await cache.get_mask(1, "farhold", 1, 100) == 0

    await cache.mark_cleared(1, "farhold", 1, 100, node=2, ttl=600)
    mask = await cache.mark_cleared(1, "farhold", 1, 100, node=5, ttl=600)
    assert mask == (1 << 2) | (1 << 5)
    assert await cache.get_mask(1, "farhold", 1, 100) == mask


async def test_location_deltas_are_scoped_to_the_cycle() -> None:
    """A new cycle regenerates the world, so old progress must not leak into it."""
    cache = InMemoryLocationDeltaCache()
    await cache.mark_cleared(1, "farhold", 1, 100, node=2, ttl=600)
    assert await cache.get_mask(1, "farhold", 1, 101) == 0
    assert await cache.get_mask(2, "farhold", 1, 100) == 0
    assert await cache.get_mask(1, "farhold", 2, 100) == 0


async def test_location_deltas_expire_with_the_cycle() -> None:
    clock = FakeClock()
    cache = InMemoryLocationDeltaCache(clock=clock)
    await cache.mark_cleared(1, "farhold", 1, 100, node=1, ttl=600)
    clock.advance(601)
    assert await cache.get_mask(1, "farhold", 1, 100) == 0


async def test_duplicate_updates_are_dropped() -> None:
    """A redelivered Telegram update must not apply its effect twice."""
    store = InMemoryIdempotencyStore()
    assert await store.seen(555) is False
    assert await store.seen(555) is True
    assert await store.seen(556) is False


async def test_idempotency_entries_expire() -> None:
    clock = FakeClock()
    store = InMemoryIdempotencyStore(clock=clock)
    await store.seen(1, ttl=300)
    clock.advance(301)
    assert await store.seen(1, ttl=300) is False


@pytest.mark.integration
async def test_postgres_adapters_are_importable() -> None:
    """The SQL adapters must at least import without a database present."""
    from mmorpg.infrastructure.persistence.postgres import (
        PostgresCharacterRepository,
        PostgresInventoryRepository,
        PostgresUserRepository,
    )

    assert PostgresCharacterRepository is not None
    assert PostgresInventoryRepository is not None
    assert PostgresUserRepository is not None
