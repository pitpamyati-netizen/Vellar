"""The SQL adapters against a real PostgreSQL.

Every statement in ``mmorpg.infrastructure.persistence.postgres`` is executed here
at least once. The in-memory adapters cannot catch a column PostgreSQL refuses to
parse or a JSONB cast that does not round-trip; these tests can.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities.character import Character, Equipment, SkillLoadout
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeStatus
from mmorpg.domain.ports.repositories import AccessibilitySettings, User
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresInventoryRepository,
    PostgresPrivacyRepository,
    PostgresTradeRepository,
    PostgresUserRepository,
)

pytestmark = pytest.mark.integration

# Far outside the range Telegram issues, so a test can never collide with a real
# player's row in a database someone is also playing on.
TEST_TELEGRAM_ID = -999_001
OTHER_TELEGRAM_ID = -999_002


@pytest.fixture
async def clean_user(pool) -> AsyncIterator[int]:
    """One user id, with everything it owns removed before and after."""

    async def purge() -> None:
        # characters and inventory cascade from the user row.
        await pool.execute("DELETE FROM users WHERE telegram_id = $1", TEST_TELEGRAM_ID)

    await purge()
    try:
        yield TEST_TELEGRAM_ID
    finally:
        await purge()


@pytest.fixture
async def clean_blocks(pool) -> AsyncIterator[tuple[int, int]]:
    """Two account ids with no black list rows between them, before and after."""
    pair = (TEST_TELEGRAM_ID, OTHER_TELEGRAM_ID)

    async def purge() -> None:
        await pool.execute(
            "DELETE FROM blocks WHERE owner_id = ANY($1::bigint[])"
            " OR blocked_id = ANY($1::bigint[])",
            list(pair),
        )

    await purge()
    try:
        yield pair
    finally:
        await purge()


def a_character(user_id: int, name: str = "Тестовый") -> Character:
    """A character with every optional field populated, so nothing is left untested."""
    return Character(
        id=0,
        user_id=user_id,
        name=name,
        race_id="dwarf",
        class_id="warrior",
        level=7,
        experience=1234,
        gold=250,
        allocated=StatBlock(STR=3, AGI=2, END=4, INT=1, WIS=0, CHA=1, LCK=2),
        trait_ids=("stoic", "keen_eye"),
        loadout=SkillLoadout(
            actives=("cleave", None, None, None, None, None),
            passives=("toughness", None, None),
            racial="stonesense",
            ranks=MappingProxyType({"cleave": 3}),
            edges=MappingProxyType({"cleave": "wide"}),
        ),
        equipment=Equipment(MappingProxyType({"weapon": "iron_axe"})),
        city_id="farhold",
        unspent_stat_points=5,
        unspent_skill_points=2,
        health=33,
        bank_gold=900,
        quests=QuestLog(taken=MappingProxyType({"farhold_tallies": 2}), done=("prologue",)),
        crafts=CraftLog(
            MappingProxyType({"mining": CraftProgress(experience=260, gathered_at=1_700_000_000)})
        ),
        is_admin=True,
    )


# --- users -------------------------------------------------------------------


async def test_a_user_survives_a_round_trip(pool, clean_user) -> None:
    users = PostgresUserRepository(pool)
    assert await users.get(clean_user) is None

    stored = await users.upsert(
        User(
            telegram_id=clean_user,
            username="tester",
            settings=AccessibilitySettings(emoji=True, verbose=False, page_size=12),
        )
    )
    assert stored.telegram_id == clean_user
    assert stored.username == "tester"


async def test_accessibility_settings_are_saved_and_read_back(pool, clean_user) -> None:
    """The column behind ``verbose`` cannot be named that; this proves it is read correctly."""
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user, username="tester"))

    await users.save_settings(
        clean_user, AccessibilitySettings(emoji=True, verbose=False, page_size=20)
    )

    read = await users.get(clean_user)
    assert read is not None
    assert read.settings.emoji is True
    assert read.settings.verbose is False
    assert read.settings.page_size == 20


async def test_save_settings_creates_the_user_when_there_is_none(pool, clean_user) -> None:
    """A player who changes a setting before creating a character still gets a row."""
    users = PostgresUserRepository(pool)
    await users.save_settings(
        clean_user, AccessibilitySettings(emoji=True, verbose=True, page_size=8)
    )
    assert await users.get(clean_user) is not None


async def test_upsert_does_not_reset_settings(pool, clean_user) -> None:
    """Every update upserts the user; that must not undo what the player chose."""
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user, username="tester"))
    await users.save_settings(
        clean_user, AccessibilitySettings(emoji=True, verbose=False, page_size=20)
    )

    await users.upsert(User(telegram_id=clean_user, username="renamed"))

    read = await users.get(clean_user)
    assert read is not None
    assert read.username == "renamed"
    assert read.settings.page_size == 20


# --- privacy -----------------------------------------------------------------


async def test_an_account_nobody_stored_anything_about_is_open(pool, clean_user) -> None:
    privacy = PostgresPrivacyRepository(pool)

    assert await privacy.profile_visible(clean_user) is True
    assert await privacy.blocks(clean_user, OTHER_TELEGRAM_ID) is False


async def test_profile_visibility_is_saved_and_read_back(pool, clean_user) -> None:
    privacy = PostgresPrivacyRepository(pool)
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user, username="tester"))

    await privacy.set_profile_visible(clean_user, False)
    hidden = await privacy.profile_visible(clean_user)
    await privacy.set_profile_visible(clean_user, True)

    assert hidden is False
    assert await privacy.profile_visible(clean_user) is True


async def test_closing_the_profile_creates_the_user_row_when_there_is_none(
    pool, clean_user
) -> None:
    privacy = PostgresPrivacyRepository(pool)

    await privacy.set_profile_visible(clean_user, False)

    assert await privacy.profile_visible(clean_user) is False


async def test_a_block_is_written_once_and_lifted_once(pool, clean_blocks) -> None:
    privacy = PostgresPrivacyRepository(pool)
    owner, other = clean_blocks

    first = await privacy.block(owner, other, at=1000)
    again = await privacy.block(owner, other, at=1001)

    assert (first, again) == (True, False)
    assert await privacy.blocks(owner, other) is True
    # A block is one direction: the other side is not listed by it.
    assert await privacy.blocks(other, owner) is False
    assert await privacy.unblock(owner, other) is True
    assert await privacy.unblock(owner, other) is False
    assert await privacy.blocks(owner, other) is False


async def test_the_database_refuses_a_block_on_oneself(pool, clean_blocks) -> None:
    import asyncpg

    privacy = PostgresPrivacyRepository(pool)
    owner, _ = clean_blocks

    with pytest.raises(asyncpg.CheckViolationError):
        await privacy.block(owner, owner, at=1000)


# --- characters --------------------------------------------------------------


async def test_a_character_survives_a_round_trip(pool, clean_user) -> None:
    """Including the JSONB columns, which are the easiest thing here to get wrong."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)

    created = await characters.create(a_character(clean_user))
    assert created.id > 0

    read = await characters.get(created.id)
    assert read is not None
    assert read.name == "Тестовый"
    assert read.allocated == StatBlock(STR=3, AGI=2, END=4, INT=1, WIS=0, CHA=1, LCK=2)
    assert read.trait_ids == ("stoic", "keen_eye")
    assert read.loadout.actives[0] == "cleave"
    assert read.loadout.racial == "stonesense"
    # Skills lying in the panel are known by definition, so the rank of the
    # passive and of the racial comes back filled in even though the row stored
    # only the one rank that was raised.
    assert dict(read.loadout.ranks) == {"cleave": 3, "toughness": 1, "stonesense": 1}
    assert dict(read.loadout.edges) == {"cleave": "wide"}
    assert dict(read.equipment.items) == {"weapon": "iron_axe"}
    assert read.health == 33
    assert read.bank_gold == 900
    assert dict(read.quests.taken) == {"farhold_tallies": 2}
    assert read.quests.done == ("prologue",)
    assert read.crafts.progress("mining") == CraftProgress(
        experience=260, gathered_at=1_700_000_000
    )
    assert read.unspent_stat_points == 5
    assert read.is_admin is True
    # The vault is a column of its own: the purse must never absorb it.
    assert (read.gold, read.bank_gold) == (250, 900)


async def test_saving_a_character_updates_every_column(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    created = await characters.create(a_character(clean_user))

    await characters.save(
        replace(
            created,
            level=42,
            experience=99_999,
            gold=7,
            city_id="dunmoor",
            health=11,
            bank_gold=1_500,
            quests=QuestLog(taken=MappingProxyType({"farhold_tallies": 3}), done=()),
            crafts=CraftLog(
                MappingProxyType({"smithing": CraftProgress(experience=40, gathered_at=0)})
            ),
            is_admin=True,
        )
    )

    read = await characters.get(created.id)
    assert read is not None
    assert (read.level, read.experience, read.gold, read.city_id) == (42, 99_999, 7, "dunmoor")
    assert read.is_admin is True
    assert (read.health, read.bank_gold) == (11, 1_500)
    assert read.quests.progress("farhold_tallies") == 3
    assert read.quests.done == ()
    assert read.crafts.progress("smithing").experience == 40
    assert read.crafts.progress("mining").experience == 0, "the whole document is replaced"


async def test_the_active_character_is_the_first_one(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)

    first = await characters.create(a_character(clean_user, name="Первый"))
    await characters.create(a_character(clean_user, name="Второй"))

    active = await characters.get_active(clean_user)
    assert active is not None
    assert active.id == first.id
    assert [c.name for c in await characters.list_for_user(clean_user)] == ["Первый", "Второй"]


async def test_names_are_taken_case_insensitively(pool, clean_user) -> None:
    """The unique index is on lower(name); the check has to agree with it."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    await characters.create(a_character(clean_user, name="Уникум"))

    assert await characters.name_taken("уникум") is True
    assert await characters.name_taken("УНИКУМ") is True
    assert await characters.name_taken("Кто-то другой") is False


async def test_the_level_range_is_enforced_by_the_database(pool, clean_user) -> None:
    """The rules cap levels at 300; so does the schema, as the last line of defence."""
    import asyncpg

    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)

    too_high = replace(a_character(clean_user, name="Слишкомвысокий"), level=301)
    with pytest.raises(asyncpg.CheckViolationError):
        await characters.create(too_high)


# --- inventory ---------------------------------------------------------------


async def test_inventory_adds_stack_and_removals_are_atomic(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    character = await PostgresCharacterRepository(pool).create(a_character(clean_user))
    inventory = PostgresInventoryRepository(pool)

    await inventory.add(character.id, "health_potion", 3)
    await inventory.add(character.id, "health_potion", 2)
    assert await inventory.count(character.id, "health_potion") == 5

    assert await inventory.remove(character.id, "health_potion", 4) is True
    assert await inventory.count(character.id, "health_potion") == 1

    # Not enough left: the row must be untouched, not driven negative.
    assert await inventory.remove(character.id, "health_potion", 2) is False
    assert await inventory.count(character.id, "health_potion") == 1


async def test_emptied_stacks_disappear_from_the_listing(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    character = await PostgresCharacterRepository(pool).create(a_character(clean_user))
    inventory = PostgresInventoryRepository(pool)

    await inventory.add(character.id, "rope", 1)
    await inventory.add(character.id, "torch", 2)
    await inventory.remove(character.id, "rope", 1)

    listed = await inventory.list_items(character.id)
    assert [entry.item_id for entry in listed] == ["torch"]


async def test_counting_an_item_the_character_never_had_is_zero(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    character = await PostgresCharacterRepository(pool).create(a_character(clean_user))
    inventory = PostgresInventoryRepository(pool)

    assert await inventory.count(character.id, "nothing_like_this") == 0
    assert await inventory.remove(character.id, "nothing_like_this", 1) is False


# --- trades ------------------------------------------------------------------

# One group of its own, so a test never sees an offer left by a real player.
TEST_SCOPE = "test-group"
NOW = 1_700_000_000


@pytest.fixture
async def two_parties(pool, clean_user) -> tuple[Party, Party]:
    """Two characters to trade with each other, cascading away with the user."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    first = await characters.create(a_character(clean_user, name="Продавец"))
    second = await characters.create(a_character(clean_user, name="Покупатель"))
    return (
        Party(user_id=clean_user, character_id=first.id, name=first.name),
        Party(user_id=clean_user, character_id=second.id, name=second.name),
    )


def an_offer(author: Party, target: Party, *, price: int = 100, created_at: int = NOW) -> Offer:
    return Offer(
        number=0,
        kind=OfferKind.SELL,
        author=author,
        target=target,
        item_id="leather_armor",
        item_name="Кожаная броня",
        price=price,
        quantity=2,
        created_at=created_at,
    )


async def test_an_offer_survives_a_round_trip_and_gets_a_number(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)

    opened = await trades.open(an_offer(author, target), scope=TEST_SCOPE)

    assert opened is not None
    assert 1 <= opened.number <= 999
    read = await trades.pending(opened.number, scope=TEST_SCOPE)
    assert read is not None
    assert read.status is TradeStatus.PENDING
    assert read.offer.kind is OfferKind.SELL
    assert read.offer.item_name == "Кожаная броня"
    assert read.offer.price == 100
    assert read.offer.quantity == 2
    assert read.offer.created_at == NOW
    assert read.offer.author.name == "Продавец"
    assert read.offer.target.character_id == target.character_id
    assert read.tax == 0 and read.settled_at is None


async def test_two_live_offers_never_share_a_number(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)

    first = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    second = await trades.open(an_offer(author, target), scope=TEST_SCOPE)

    assert first is not None and second is not None
    assert first.number != second.number


async def test_a_number_comes_back_round_once_its_offer_closes(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    first = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    assert first is not None
    await trades.close(
        first.number, scope=TEST_SCOPE, status=TradeStatus.DECLINED, settled_at=NOW + 1
    )

    again = await trades.open(an_offer(author, target), scope=TEST_SCOPE)

    assert again is not None
    assert again.number == first.number


async def test_a_trade_can_only_be_closed_once(pool, two_parties) -> None:
    """This is what makes two taps on "Принять" settle exactly one trade."""
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    opened = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    assert opened is not None

    first = await trades.close(
        opened.number, scope=TEST_SCOPE, status=TradeStatus.ACCEPTED, settled_at=NOW + 5, tax=5
    )
    second = await trades.close(
        opened.number, scope=TEST_SCOPE, status=TradeStatus.ACCEPTED, settled_at=NOW + 6, tax=5
    )

    assert first is not None
    assert first.status is TradeStatus.ACCEPTED
    assert first.tax == 5 and first.settled_at == NOW + 5
    assert second is None
    assert await trades.pending(opened.number, scope=TEST_SCOPE) is None


async def test_the_index_refuses_a_second_pending_offer_on_one_number(pool, two_parties) -> None:
    """The partial unique index is the last line of defence against a race."""
    import asyncpg

    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    opened = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    assert opened is not None

    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            """
            INSERT INTO trades (
                scope, number, kind, price, item_id, item_name, created_at,
                author_user_id, author_character_id, author_name,
                target_user_id, target_character_id, target_name
            )
            VALUES ($1, $2, 'sell', 1, 'x', 'x', $3, $4, $5, 'x', $6, $7, 'x')
            """,
            TEST_SCOPE,
            opened.number,
            NOW,
            author.user_id,
            author.character_id,
            target.user_id,
            target.character_id,
        )


async def test_expiry_hands_back_each_stale_offer_exactly_once(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    stale = await trades.open(an_offer(author, target, created_at=NOW), scope=TEST_SCOPE)
    fresh = await trades.open(an_offer(author, target, created_at=NOW + 1_000), scope=TEST_SCOPE)
    assert stale is not None and fresh is not None

    first_sweep = await trades.expire(scope=TEST_SCOPE, before=NOW + 500)
    second_sweep = await trades.expire(scope=TEST_SCOPE, before=NOW + 500)

    assert [record.number for record in first_sweep] == [stale.number]
    assert first_sweep[0].status is TradeStatus.EXPIRED
    assert second_sweep == ()
    # The offer that is still young was not touched.
    assert await trades.pending(fresh.number, scope=TEST_SCOPE) is not None


async def test_another_group_never_sees_these_offers(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    opened = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    assert opened is not None

    assert await trades.pending(opened.number, scope="some-other-group") is None
    assert await trades.expire(scope="some-other-group", before=NOW + 10_000) == ()


async def test_the_journal_lists_both_sides_newest_first(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    older = await trades.open(an_offer(author, target, price=10), scope=TEST_SCOPE)
    assert older is not None
    await trades.close(
        older.number, scope=TEST_SCOPE, status=TradeStatus.ACCEPTED, settled_at=NOW + 1, tax=1
    )
    newer = await trades.open(an_offer(author, target, price=20), scope=TEST_SCOPE)
    assert newer is not None

    for character_id in (author.character_id, target.character_id):
        journal = await trades.journal(character_id)
        assert [record.offer.price for record in journal] == [20, 10]
        assert journal[1].tax == 1
