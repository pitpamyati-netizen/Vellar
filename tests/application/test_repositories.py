"""Поведение хранилищ, проверенное на адаптерах в памяти.

Адаптеры на PostgreSQL отвечают тем же протоколам; их SQL прогоняют тесты с
пометкой ``integration``, и они пропускаются, когда база не поднята.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, StatBlock
from mmorpg.domain.entities.location import LocationState, NodeState, Presence, Roamer
from mmorpg.domain.ports import (
    AccessibilitySettings,
    CharacterRepository,
    GuildRepository,
    IdempotencyStore,
    InventoryRepository,
    LocationStateCache,
    PartyRepository,
    StateCache,
    User,
    UserRepository,
)
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.nodes import RESPAWN_SECONDS
from mmorpg.domain.rules.party import Party
from mmorpg.infrastructure.cache import (
    InMemoryIdempotencyStore,
    InMemoryLocationStateCache,
    InMemoryStateCache,
)
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryGuildRepository,
    InMemoryInventoryRepository,
    InMemoryPartyRepository,
    InMemoryUserRepository,
)


class FakeClock:
    """Часы, которые тесты двигают руками, чтобы ни один из них не засыпал."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def a_character(user_id: int = 42, name: str = "Тест") -> Character:
    return Character(id=0, user_id=user_id, name=name, race_id="human", class_id="warrior")


# --- адаптеры отвечают портам --------------------------------------


def test_in_memory_adapters_implement_the_ports() -> None:
    assert isinstance(InMemoryUserRepository(), UserRepository)
    assert isinstance(InMemoryCharacterRepository(), CharacterRepository)
    assert isinstance(InMemoryInventoryRepository(), InventoryRepository)
    assert isinstance(InMemoryPartyRepository(), PartyRepository)
    assert isinstance(InMemoryGuildRepository(), GuildRepository)
    assert isinstance(InMemoryStateCache(), StateCache)
    assert isinstance(InMemoryLocationStateCache(), LocationStateCache)
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


# --- персонажи -------------------------------------------------------


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
        loadout=created.loadout.with_rank("warrior_rassechenie", 3).with_edge(
            "warrior_rassechenie", "warrior_rassechenie_a"
        ),
        equipment=created.equipment.equip("weapon", "sword@1#common"),
    )
    await characters.save(updated)

    reloaded = await characters.get(created.id)
    assert reloaded == updated
    assert reloaded is not None
    assert reloaded.loadout.rank_of("warrior_rassechenie") == 3
    assert reloaded.loadout.edge_of("warrior_rassechenie") == "warrior_rassechenie_a"
    assert reloaded.equipment.item_in("weapon") == "sword@1#common"


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


# --- сумка -----------------------------------------------------------


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


# --- отряд ----------------------------------------------------------


async def test_a_party_roster_is_stored_and_read_back_by_any_member() -> None:
    roster = InMemoryPartyRepository()
    await roster.save(Party(leader_id=1, members=(1, 2, 3)))

    assert await roster.by_leader(1) == Party(leader_id=1, members=(1, 2, 3))
    for member in (1, 2, 3):
        found = await roster.of(member)
        assert found is not None and found.leader_id == 1


async def test_nobody_stands_in_two_parties_at_once() -> None:
    roster = InMemoryPartyRepository()
    await roster.save(Party(leader_id=1, members=(1, 2)))
    await roster.save(Party(leader_id=3, members=(3, 2)))

    first = await roster.by_leader(1)
    assert first is not None and 2 not in first.members
    second = await roster.of(2)
    assert second is not None and second.leader_id == 3


async def test_disbanding_clears_the_roster() -> None:
    roster = InMemoryPartyRepository()
    await roster.save(Party(leader_id=1, members=(1, 2)))
    await roster.disband(1)
    assert await roster.by_leader(1) is None
    assert await roster.of(2) is None


# --- гильдия --------------------------------------------------------


async def test_a_guild_is_created_with_its_founder_and_found_by_any_member() -> None:
    guilds = InMemoryGuildRepository()
    made = await guilds.create("Стая", 7)
    assert made.rank_of(7) is GuildRank.FOUNDER

    await guilds.save(made.with_member(8))
    for member in (7, 8):
        found = await guilds.of(member)
        assert found is not None and found.name == "Стая"
    assert (await guilds.by_name("стая")) is not None


async def test_the_guild_vault_never_goes_negative_and_deposit_always_lands() -> None:
    guilds = InMemoryGuildRepository()
    made = await guilds.create("Стая", 1)

    await guilds.deposit(made.id, 300)
    assert await guilds.withdraw(made.id, 500) is False
    assert await guilds.withdraw(made.id, 200) is True

    left = await guilds.by_id(made.id)
    assert left is not None and left.vault_gold == 100


async def test_saving_a_roster_does_not_touch_the_vault() -> None:
    guilds = InMemoryGuildRepository()
    made = await guilds.create("Стая", 1)
    await guilds.deposit(made.id, 400)

    await guilds.save((await guilds.by_id(made.id)).with_member(2))  # type: ignore[union-attr]

    kept = await guilds.by_id(made.id)
    assert kept is not None and kept.vault_gold == 400 and kept.has(2)


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


async def test_what_one_player_took_is_gone_for_the_next_one() -> None:
    """В узле стоит волна, и эта волна общая для всех, кто в месте."""
    cache = InMemoryLocationStateCache()
    assert await cache.state("farhold", 1, now=100) == LocationState()

    await cache.take("farhold", 1, 2, wave=0, size=3, now=100, ttl=600)
    state = await cache.take("farhold", 1, 2, wave=0, size=3, now=100, ttl=600)
    assert state.node(2).taken == 2
    assert not state.node(2).empty
    assert (await cache.state("farhold", 1, now=100)).node(2).taken == 2


async def test_the_last_thing_out_empties_the_node_and_three_minutes_refill_it() -> None:
    cache = InMemoryLocationStateCache()
    for _ in range(2):
        state = await cache.take("farhold", 1, 4, wave=0, size=2, now=1_000, ttl=600)
    assert state.node(4).empty

    waiting = await cache.state("farhold", 1, now=1_000 + RESPAWN_SECONDS - 1)
    assert waiting.node(4).empty

    filled = await cache.state("farhold", 1, now=1_000 + RESPAWN_SECONDS)
    assert not filled.node(4).empty
    assert filled.node(4).wave == 1


async def test_a_press_that_names_an_older_wave_takes_nothing() -> None:
    """Двое, вычищающие последнюю стаю разом, вычищают её один раз, а не два."""
    cache = InMemoryLocationStateCache()
    await cache.take("farhold", 1, 3, wave=0, size=1, now=1_000, ttl=600)
    late = await cache.take("farhold", 1, 3, wave=0, size=1, now=1_000 + RESPAWN_SECONDS, ttl=600)
    assert late.node(3) == NodeState(wave=1, taken=0, emptied_at=0)


async def test_an_untouched_location_fills_back_up_eventually() -> None:
    clock = FakeClock()
    cache = InMemoryLocationStateCache(clock=clock)
    await cache.take("farhold", 1, 1, wave=0, size=3, now=1_000, ttl=600)
    clock.advance(601)
    assert await cache.state("farhold", 1, now=1_000) == LocationState()


async def test_people_are_seen_on_their_own_node_only() -> None:
    cache = InMemoryLocationStateCache()
    await cache.arrive("farhold", 1, Presence(7, "Мерла", 12, node=3), now=1000, ttl=600)
    await cache.arrive("farhold", 1, Presence(8, "Довен", 9, node=4), now=1000, ttl=600)

    here = await cache.others_at("farhold", 1, 3, exclude=1, now=1000, ttl=600)
    assert [presence.name for presence in here] == ["Мерла"]
    assert await cache.others_at("farhold", 1, 3, exclude=7, now=1000, ttl=600) == ()


async def test_somebody_who_walked_off_stops_being_seen() -> None:
    cache = InMemoryLocationStateCache()
    await cache.arrive("farhold", 1, Presence(7, "Мерла", 12, node=3), now=1000, ttl=600)
    assert await cache.others_at("farhold", 1, 3, exclude=1, now=1601, ttl=600) == ()

    await cache.arrive("farhold", 1, Presence(7, "Мерла", 12, node=3), now=2000, ttl=600)
    await cache.leave("farhold", 1, 7)
    assert await cache.others_at("farhold", 1, 3, exclude=1, now=2000, ttl=600) == ()


def _roamer(node: int = 3) -> Roamer:
    return Roamer(node=node, group=False, difficulty="delve", level=7, stamp=1)


async def test_a_roamer_is_spawned_once_and_seen_by_everyone() -> None:
    cache = InMemoryLocationStateCache()
    first = await cache.spawn_roamer("farhold", 1, _roamer(node=3), ttl=600)
    # Второй, вошедший в ту же локацию, не заводит своё подземелье - видит то же.
    second = await cache.spawn_roamer("farhold", 1, _roamer(node=5), ttl=600)
    assert first.node == second.node == 3
    assert (await cache.roamer("farhold", 1, now=0)).node == 3


async def test_only_one_party_holds_the_roamer_at_a_time() -> None:
    cache = InMemoryLocationStateCache()
    await cache.spawn_roamer("farhold", 1, _roamer(), ttl=600)
    assert await cache.claim_roamer("farhold", 1, 7, ttl=600) is True
    # Тот же персонаж может «взять» замок снова - это не второй заход.
    assert await cache.claim_roamer("farhold", 1, 7, ttl=600) is True
    # Чужому вход закрыт, пока первый внутри.
    assert await cache.claim_roamer("farhold", 1, 8, ttl=600) is False
    assert (await cache.roamer("farhold", 1, now=0)).holder == 7


async def test_releasing_the_hold_leaves_the_roamer_for_the_next_one() -> None:
    cache = InMemoryLocationStateCache()
    await cache.spawn_roamer("farhold", 1, _roamer(), ttl=600)
    await cache.claim_roamer("farhold", 1, 7, ttl=600)
    await cache.release_roamer("farhold", 1)
    here = await cache.roamer("farhold", 1, now=0)
    assert here is not None and here.holder == 0
    assert await cache.claim_roamer("farhold", 1, 8, ttl=600) is True


async def test_clearing_the_roamer_removes_it_completely() -> None:
    cache = InMemoryLocationStateCache()
    await cache.spawn_roamer("farhold", 1, _roamer(), ttl=600)
    await cache.claim_roamer("farhold", 1, 7, ttl=600)
    await cache.clear_roamer("farhold", 1)
    assert await cache.roamer("farhold", 1, now=0) is None
    assert await cache.claim_roamer("farhold", 1, 8, ttl=600) is True


async def test_an_abandoned_hold_expires_on_its_own() -> None:
    clock = FakeClock()
    cache = InMemoryLocationStateCache(clock=clock)
    await cache.spawn_roamer("farhold", 1, _roamer(), ttl=6000)
    await cache.claim_roamer("farhold", 1, 7, ttl=600)
    clock.advance(601)
    assert (await cache.roamer("farhold", 1, now=0)).holder == 0


async def test_duplicate_updates_are_dropped() -> None:
    """Повторно доставленное обновление Telegram не должно сработать дважды."""
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
    """Адаптеры на SQL обязаны хотя бы импортироваться без поднятой базы."""
    from mmorpg.infrastructure.persistence.postgres import (
        PostgresCharacterRepository,
        PostgresGuildRepository,
        PostgresInventoryRepository,
        PostgresPartyRepository,
        PostgresUserRepository,
    )

    assert PostgresCharacterRepository is not None
    assert PostgresGuildRepository is not None
    assert PostgresInventoryRepository is not None
    assert PostgresPartyRepository is not None
    assert PostgresUserRepository is not None
