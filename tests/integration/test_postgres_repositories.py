"""Адаптеры на SQL против настоящего PostgreSQL.

Каждый запрос из ``mmorpg.infrastructure.persistence.postgres`` выполняется здесь
хотя бы раз. Адаптеры в памяти не поймают ни колонку, которую PostgreSQL
откажется разбирать, ни приведение к JSONB, не переживающее обратного пути; эти
тесты - могут.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import MappingProxyType

import pytest
import pytest_asyncio

from mmorpg.domain.entities.character import Character, Equipment, SkillLoadout
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeStatus
from mmorpg.domain.ports.repositories import AccessibilitySettings, User
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank
from mmorpg.domain.rules.party import Party as PlayerParty
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresContentOverlayRepository,
    PostgresGuildRepository,
    PostgresInventoryRepository,
    PostgresKeeperLogRepository,
    PostgresPartyRepository,
    PostgresPrivacyRepository,
    PostgresTradeRepository,
    PostgresUserRepository,
)

# Один цикл событий на весь пакет: соединения открываются раз на прогон
# (``conftest.py``), а привязаны они к тому циклу, в котором созданы.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# Далеко за пределами того, что выдаёт Telegram, чтобы тест никогда не столкнулся со
# строкой живого игрока в базе, на которой ещё и играют.
TEST_TELEGRAM_ID = -999_001
OTHER_TELEGRAM_ID = -999_002


@pytest_asyncio.fixture(loop_scope="session")
async def clean_user(pool) -> AsyncIterator[int]:
    """Один идентификатор пользователя, у которого всё принадлежащее убирается до и после."""

    async def purge() -> None:
        # персонажи и сумка уходят каскадом от строки пользователя.
        await pool.execute("DELETE FROM users WHERE telegram_id = $1", TEST_TELEGRAM_ID)

    await purge()
    try:
        yield TEST_TELEGRAM_ID
    finally:
        await purge()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_blocks(pool) -> AsyncIterator[tuple[int, int]]:
    """Два идентификатора аккаунтов, между которыми нет строк чёрного списка, - до и после."""
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
    """Персонаж с заполненными необязательными полями, чтобы непроверенным не осталось ничего."""
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
            racial="stonesense",
            ranks=MappingProxyType({"cleave": 3, "toughness": 1}),
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
        arena_wins=3,
        arena_losses=1,
        arena_credit=120,
        seals=2,
        pledges=("item:ashen_signet", "edge:cleave"),
        turning_cycle="toll",
        turning_answer="toll_keep",
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
    """Колонку за ``verbose`` нельзя так назвать; это доказывает, что читается она верно."""
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
    """Игрок, поменявший настройку до создания персонажа, всё равно получает свою строку."""
    users = PostgresUserRepository(pool)
    await users.save_settings(
        clean_user, AccessibilitySettings(emoji=True, verbose=True, page_size=8)
    )
    assert await users.get(clean_user) is not None


async def test_upsert_does_not_reset_settings(pool, clean_user) -> None:
    """Каждое обновление дописывает пользователя; отменять выбор игрока при этом нельзя."""
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


async def test_the_keeper_right_is_stored_on_the_account_and_read_back(pool, clean_user) -> None:
    """Право выдают и тому, кто ни разу не трогал настройки, поэтому строки может не быть."""
    users = PostgresUserRepository(pool)

    await users.set_keeper(clean_user, True)

    read = await users.get(clean_user)
    assert read is not None and read.keeper is True

    # Обычный upsert на каждом обновлении не должен его снимать.
    await users.upsert(User(telegram_id=clean_user, username="tester"))
    read = await users.get(clean_user)
    assert read is not None and read.keeper is True

    await users.set_keeper(clean_user, False)
    read = await users.get(clean_user)
    assert read is not None and read.keeper is False


# --- приватность -----------------------------------------------------------


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
    # Блокировка одностороння: обратной стороны в ней не значится.
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


# --- персонажи -------------------------------------------------------------


async def test_a_character_survives_a_round_trip(pool, clean_user) -> None:
    """Включая колонки JSONB - здесь их проще всего испортить."""
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
    # Умения, лежащие в панели, изучены по определению, поэтому ранг пассивного и
    # расового возвращается заполненным, хотя в строке хранился только тот один ранг,
    # который поднимали.
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
    # Сундук - отдельная колонка: кошелёк не вправе его поглотить.
    assert (read.gold, read.bank_gold) == (250, 900)
    # То, что держит Круг, - тоже сохранённое золото: без него победе после перезапуска
    # не из чего было бы платиться (``domain/rules/arena.py``).
    assert (read.arena_wins, read.arena_losses, read.arena_credit) == (3, 1, 120)
    # Печати и заклады тоже хранятся: без списка закладов грань, отданную в
    # перерождение, можно было бы выбрать заново (``domain/rules/turning.py``).
    assert read.seals == 2
    assert read.pledges == ("item:ashen_signet", "edge:cleave")
    assert (read.turning_cycle, read.turning_answer) == ("toll", "toll_keep")


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
            arena_credit=60,
            seals=3,
            pledges=("item:ashen_signet",),
            turning_cycle="gates",
            turning_answer="gates_one",
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
    assert read.arena_credit == 60
    assert read.seals == 3
    assert read.pledges == ("item:ashen_signet",)
    assert (read.turning_cycle, read.turning_answer) == ("gates", "gates_one")


async def test_the_tally_counts_seals_and_only_of_this_cycle(pool, clean_user) -> None:
    """Голос весит столько, сколько Печатей за ним, и считается по своему циклу."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)

    await characters.create(
        replace(
            a_character(clean_user, name=f"Голос{clean_user}"),
            seals=2,
            turning_cycle="toll",
            turning_answer="toll_low",
        )
    )
    await characters.create(
        replace(
            a_character(clean_user, name=f"Второй{clean_user}"),
            seals=1,
            turning_cycle="toll",
            turning_answer="toll_low",
        )
    )
    # Ответ на прошлый вопрос в этом счёте не участвует.
    await characters.create(
        replace(
            a_character(clean_user, name=f"Прошлый{clean_user}"),
            seals=5,
            turning_cycle="gates",
            turning_answer="gates_one",
        )
    )
    # Печати нет - голоса нет, даже если ответ записан.
    await characters.create(
        replace(
            a_character(clean_user, name=f"Немой{clean_user}"),
            seals=0,
            turning_cycle="toll",
            turning_answer="toll_high",
        )
    )

    assert dict(await characters.turning_tally("toll")) == {"toll_low": 3}


async def test_gold_is_spent_in_one_step_or_not_at_all(pool, clean_user) -> None:
    """Дыра, которую это закрывает: кошелёк прочитан, а записан обратно несколькими await позже.

    Сделка закрывается против золота, которое его владелец в это самое мгновение,
    возможно, тратит, поэтому проверка и вычитание обязаны быть одним запросом. Два
    закрытия, гоняющихся за одним кошельком: выиграть вправе ровно одно.
    """
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    created = await characters.create(replace(a_character(clean_user), gold=100))

    both = await asyncio.gather(
        characters.spend_gold(created.id, 100),
        characters.spend_gold(created.id, 100),
    )
    assert sorted(both) == [False, True], "one purse paid twice"

    read = await characters.get(created.id)
    assert read is not None
    assert read.gold == 0

    assert await characters.spend_gold(created.id, 1) is False
    await characters.grant_gold(created.id, 40)
    after = await characters.get(created.id)
    assert after is not None and after.gold == 40


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
    """Уникальный указатель стоит на lower(name); проверка обязана с ним сходиться."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    await characters.create(a_character(clean_user, name="Уникум"))

    assert await characters.name_taken("уникум") is True
    assert await characters.name_taken("УНИКУМ") is True
    assert await characters.name_taken("Кто-то другой") is False


async def test_the_level_range_is_enforced_by_the_database(pool, clean_user) -> None:
    """Правила держат потолок в 300 уровней; схема тоже - как последний рубеж."""
    import asyncpg

    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)

    too_high = replace(a_character(clean_user, name="Слишкомвысокий"), level=301)
    with pytest.raises(asyncpg.CheckViolationError):
        await characters.create(too_high)


# --- сумка -----------------------------------------------------------------


async def test_inventory_adds_stack_and_removals_are_atomic(pool, clean_user) -> None:
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    character = await PostgresCharacterRepository(pool).create(a_character(clean_user))
    inventory = PostgresInventoryRepository(pool)

    await inventory.add(character.id, "health_potion", 3)
    await inventory.add(character.id, "health_potion", 2)
    assert await inventory.count(character.id, "health_potion") == 5

    assert await inventory.remove(character.id, "health_potion", 4) is True
    assert await inventory.count(character.id, "health_potion") == 1

    # Не хватило: строку обязаны не тронуть, а не увести в минус.
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

# Своя отдельная группа, чтобы тест никогда не увидел предложение, оставленное живым
# игроком.
TEST_SCOPE = "test-group"
NOW = 1_700_000_000


@pytest_asyncio.fixture(loop_scope="session")
async def two_parties(pool, clean_user) -> tuple[Party, Party]:
    """Два персонажа, чтобы торговать друг с другом, уходящие каскадом вместе с пользователем."""
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
        item_id="light_body@6#common",
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
    """Именно это и делает так, что два нажатия «Принять» закрывают ровно одну сделку."""
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
    """Частичный уникальный указатель - последний рубеж против гонки."""
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
    # Предложение, которое ещё молодо, не тронули.
    assert await trades.pending(fresh.number, scope=TEST_SCOPE) is not None


async def test_a_sweep_without_a_scope_reaches_every_group(pool, two_parties) -> None:
    """Ставку, оставшуюся в затихшей группе, освобождает тот, кто заговорит следующим."""
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    here = await trades.open(an_offer(author, target, created_at=NOW), scope=TEST_SCOPE)
    quiet = await trades.open(an_offer(author, target, created_at=NOW), scope="quiet-group")
    assert here is not None and quiet is not None

    swept = await trades.expire(before=NOW + 500)

    assert {record.scope for record in swept} == {TEST_SCOPE, "quiet-group"}
    assert await trades.pending(quiet.number, scope="quiet-group") is None


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


async def test_a_settled_trade_is_rolled_back_once(pool, two_parties) -> None:
    """Откат — тот же затвор, что и расчёт: два смотрителя двигают вещи один раз."""
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    opened = await trades.open(an_offer(author, target, price=40), scope=TEST_SCOPE)
    assert opened is not None
    settled = await trades.close(
        opened.number, scope=TEST_SCOPE, status=TradeStatus.ACCEPTED, settled_at=NOW + 1, tax=2
    )
    assert settled is not None and settled.id

    undone = await trades.revert(settled.id)
    again = await trades.revert(settled.id)

    assert undone is not None and undone.offer.price == 40 and undone.tax == 2
    assert again is None
    journal = await trades.journal(author.character_id)
    assert journal[0].status is TradeStatus.REVERTED
    # Момент расчёта не переписан: когда откатили, знает журнал смотрителя.
    assert journal[0].settled_at == NOW + 1


async def test_an_offer_nobody_settled_cannot_be_rolled_back(pool, two_parties) -> None:
    author, target = two_parties
    trades = PostgresTradeRepository(pool)
    opened = await trades.open(an_offer(author, target), scope=TEST_SCOPE)
    assert opened is not None and opened.id

    assert await trades.revert(opened.id) is None
    assert await trades.pending(opened.number, scope=TEST_SCOPE) is not None


# --- панель смотрителя -------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def clean_overlay(pool) -> AsyncIterator[str]:
    """Ключ для правки, которого нет ни до, ни после теста."""
    entity_id = "keeper_test_1"

    async def purge() -> None:
        await pool.execute("DELETE FROM content_overlay WHERE entity_id = $1", entity_id)

    await purge()
    try:
        yield entity_id
    finally:
        await purge()


async def test_an_edit_survives_a_round_trip(pool, clean_overlay) -> None:
    overlays = PostgresContentOverlayRepository(pool)
    record = OverlayRecord(
        kind=OverlayKind.NPC,
        entity_id=clean_overlay,
        fields=MappingProxyType({"name": "Довен", "city": "farhold", "role": "писарь"}),
        author_id=TEST_TELEGRAM_ID,
        updated_at=NOW,
    )

    await overlays.put(record)
    stored = next(found for found in await overlays.all() if found.entity_id == clean_overlay)

    assert stored.kind is OverlayKind.NPC
    assert dict(stored.fields) == dict(record.fields)
    assert stored.author_id == TEST_TELEGRAM_ID
    assert stored.removed is False


async def test_one_entity_keeps_exactly_one_edit(pool, clean_overlay) -> None:
    """Вторая правка той же сущности заменяет первую, а не ложится рядом."""
    overlays = PostgresContentOverlayRepository(pool)
    first = OverlayRecord(
        kind=OverlayKind.NPC, entity_id=clean_overlay, fields=MappingProxyType({"name": "Довен"})
    )
    await overlays.put(first)
    await overlays.put(replace(first, fields=MappingProxyType({"name": "Мерла"}), removed=True))

    mine = [found for found in await overlays.all() if found.entity_id == clean_overlay]

    assert len(mine) == 1
    assert mine[0].value("name") == "Мерла"
    assert mine[0].removed is True


async def test_dropping_an_edit_says_whether_there_was_one(pool, clean_overlay) -> None:
    overlays = PostgresContentOverlayRepository(pool)
    await overlays.put(OverlayRecord(kind=OverlayKind.QUEST, entity_id=clean_overlay))

    assert await overlays.forget(OverlayKind.QUEST, clean_overlay) is True
    assert await overlays.forget(OverlayKind.QUEST, clean_overlay) is False


async def test_the_census_counts_what_the_panel_shows(pool, clean_user) -> None:
    characters = PostgresCharacterRepository(pool)
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user))
    await characters.create(a_character(clean_user, name="Перепись"))

    counted = await characters.census(day=NOW, week=NOW, stale=NOW)

    assert counted.characters >= 1
    assert counted.accounts >= 1
    assert counted.top_level >= 7
    assert counted.gold_on_hand >= 250
    assert counted.leaders


async def test_a_character_is_found_by_name_whatever_the_case(pool, clean_user) -> None:
    characters = PostgresCharacterRepository(pool)
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user))
    stored = await characters.create(a_character(clean_user, name="Мерла"))

    found = await characters.find_by_name("мЕрЛа")

    assert found is not None and found.id == stored.id
    assert await characters.find_by_name("Никто") is None


async def test_the_newest_characters_come_first(pool, clean_user) -> None:
    characters = PostgresCharacterRepository(pool)
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user))
    older = await characters.create(a_character(clean_user, name="Старший"))
    newer = await characters.create(a_character(clean_user, name="Младший"))

    listed = await characters.newest(limit=2)

    assert [person.id for person in listed] == [newer.id, older.id]


async def test_a_character_is_deleted_once(pool, clean_user) -> None:
    characters = PostgresCharacterRepository(pool)
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user))
    stored = await characters.create(a_character(clean_user, name="Ушедший"))

    assert await characters.delete(stored.id) is True
    assert await characters.delete(stored.id) is False
    assert await characters.get(stored.id) is None


async def test_only_untouched_first_level_characters_are_swept(pool, clean_user) -> None:
    """Брошенный — тот, кто ничего не начал. Игравший остаётся, что бы ни было."""
    characters = PostgresCharacterRepository(pool)
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user))
    played = await characters.create(a_character(clean_user, name="Игравший"))
    abandoned = await characters.create(
        replace(a_character(clean_user, name="Брошенный"), level=1, experience=0, tutorial=0)
    )

    swept = await characters.purge_abandoned(before=NOW + 10**10)

    assert swept >= 1
    assert await characters.get(abandoned.id) is None
    assert await characters.get(played.id) is not None


async def test_a_blocked_account_is_remembered_and_then_removed(pool, clean_user) -> None:
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user))
    before = await users.blocked_count()

    assert clean_user in await users.unchecked(limit=1000, before=NOW)
    await users.mark_checked(clean_user, at=NOW, blocked=True)

    assert await users.blocked_count() == before + 1
    # Проверенного и заблокировавшего второй раз не спрашивают.
    assert clean_user not in await users.unchecked(limit=1000, before=NOW)
    assert await users.purge_blocked() >= 1
    assert await users.get(clean_user) is None


async def test_an_account_that_still_reads_the_bot_is_only_marked_checked(pool, clean_user) -> None:
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user))

    await users.mark_checked(clean_user, at=NOW, blocked=False)

    assert clean_user not in await users.unchecked(limit=1000, before=NOW - 1)
    assert await users.get(clean_user) is not None


# --- блокировка и журнал -----------------------------------------------------


async def test_a_ban_is_stored_read_back_and_lifted(pool, clean_user) -> None:
    """Блокировка переживает круг до базы и обратно, включая причину."""
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user))
    before = await users.banned_count(now=NOW)

    await users.set_ban(clean_user, Ban(until=NOW + 3600, reason="обман в сделке"))

    account = await users.get(clean_user)
    assert account is not None
    assert account.ban == Ban(until=NOW + 3600, reason="обман в сделке")
    assert await users.banned_count(now=NOW) == before + 1
    # Истёкший срок никто не снимает, он просто перестаёт считаться.
    assert await users.banned_count(now=NOW + 7200) == before

    await users.set_ban(clean_user, Ban())
    lifted = await users.get(clean_user)
    assert lifted is not None and lifted.ban == Ban()


async def test_warnings_count_up_and_stop_at_zero(pool, clean_user) -> None:
    users = PostgresUserRepository(pool)

    # Строку заводит сам ``warn``: предупреждение выносят и незнакомому аккаунту.
    assert await users.warn(clean_user) == 1
    assert await users.warn(clean_user) == 2
    assert await users.warn(clean_user, delta=-3) == 0

    account = await users.get(clean_user)
    assert account is not None and account.warnings == 0


async def test_a_ban_without_an_end_is_counted_whenever_it_is_asked(pool, clean_user) -> None:
    users = PostgresUserRepository(pool)
    await users.upsert(User(telegram_id=clean_user))
    before = await users.banned_count(now=NOW)

    await users.set_ban(clean_user, Ban(until=-1, reason="повторно"))

    assert await users.banned_count(now=NOW + 100 * 365 * 24 * 3600) == before + 1


async def test_the_keeper_journal_is_written_and_read_from_the_end(pool, clean_user) -> None:
    log = PostgresKeeperLogRepository(pool)
    # Журнал ничем не убирается изнутри игры - в том и смысл, - поэтому свои
    # строки тест убирает сам, и до записи тоже: прошлый прогон писал те же.
    await pool.execute("DELETE FROM keeper_log WHERE keeper_id = $1", clean_user)
    for step, (action, who) in enumerate(
        ((KeeperAction.GOLD, "Мерла"), (KeeperAction.BAN, "Мерла"), (KeeperAction.HEAL, "Аргус")),
        start=1,
    ):
        await log.record(
            KeeperEntry(
                at=NOW + step,
                keeper_id=clean_user,
                keeper_name="Смотритель",
                action=action,
                target=who,
                detail=f"шаг {step}",
            )
        )

    latest = await log.latest(limit=50)

    mine = [entry for entry in latest if entry.keeper_id == clean_user]
    assert [entry.action for entry in mine] == [
        KeeperAction.HEAL,
        KeeperAction.BAN,
        KeeperAction.GOLD,
    ]
    assert mine[0].keeper_name == "Смотритель"
    assert mine[0].detail == "шаг 3"

    # Фильтр по цели — без учёта регистра — и страница вглубь по ней же.
    about_merla = await log.latest(limit=50, target="мЕрЛа")
    assert [entry.detail for entry in about_merla] == ["шаг 2", "шаг 1"]
    deeper = await log.latest(limit=1, offset=1, target="Мерла")
    assert deeper[0].detail == "шаг 1"
    assert await log.count(target="Мерла") == 2
    assert await log.count() >= 3
    await pool.execute("DELETE FROM keeper_log WHERE keeper_id = $1", clean_user)


# --- отряд (ADR 0029) ------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def three_fighters(pool, clean_user) -> tuple[int, int, int]:
    """Трое персонажей одного аккаунта, уходящие каскадом вместе с ним."""
    await PostgresUserRepository(pool).upsert(User(telegram_id=clean_user, username="tester"))
    characters = PostgresCharacterRepository(pool)
    a = await characters.create(a_character(clean_user, name="Вожак"))
    b = await characters.create(a_character(clean_user, name="Второй"))
    c = await characters.create(a_character(clean_user, name="Третий"))
    return (a.id, b.id, c.id)


async def test_a_party_roster_survives_a_round_trip(pool, three_fighters) -> None:
    leader, second, third = three_fighters
    parties = PostgresPartyRepository(pool)

    await parties.save(PlayerParty(leader_id=leader, members=(leader, second, third)))

    by_leader = await parties.by_leader(leader)
    assert by_leader is not None
    assert by_leader.members == (leader, second, third)
    for member in three_fighters:
        found = await parties.of(member)
        assert found is not None and found.leader_id == leader

    await parties.disband(leader)


async def test_the_database_keeps_one_party_per_character(pool, three_fighters) -> None:
    leader, second, third = three_fighters
    parties = PostgresPartyRepository(pool)
    await parties.save(PlayerParty(leader_id=leader, members=(leader, second)))
    await parties.save(PlayerParty(leader_id=third, members=(third, second)))

    first = await parties.by_leader(leader)
    assert first is not None and second not in first.members
    moved = await parties.of(second)
    assert moved is not None and moved.leader_id == third

    await parties.disband(leader)
    await parties.disband(third)


async def test_shrinking_a_party_drops_only_the_one_who_left(pool, three_fighters) -> None:
    leader, second, third = three_fighters
    parties = PostgresPartyRepository(pool)
    await parties.save(PlayerParty(leader_id=leader, members=(leader, second, third)))

    await parties.save(PlayerParty(leader_id=leader, members=(leader, third)))

    left = await parties.by_leader(leader)
    assert left is not None and left.members == (leader, third)
    assert await parties.of(second) is None

    await parties.disband(leader)


async def test_disbanding_removes_the_whole_party(pool, three_fighters) -> None:
    leader, second, _ = three_fighters
    parties = PostgresPartyRepository(pool)
    await parties.save(PlayerParty(leader_id=leader, members=(leader, second)))

    await parties.disband(leader)

    assert await parties.by_leader(leader) is None
    assert await parties.of(second) is None


# --- гильдия (ADR 0030) --------------------------------------------


async def test_a_guild_survives_a_round_trip_with_ranks_and_vault(pool, three_fighters) -> None:
    founder, officer, member = three_fighters
    guilds = PostgresGuildRepository(pool)

    made = await guilds.create("Ирисы", founder)
    await guilds.save(
        Guild(
            id=made.id,
            name="Ирисы",
            founder_id=founder,
            members=(
                GuildMember(founder, GuildRank.FOUNDER),
                GuildMember(officer, GuildRank.OFFICER),
                GuildMember(member, GuildRank.MEMBER),
            ),
        )
    )
    await guilds.deposit(made.id, 900)

    read = await guilds.by_id(made.id)
    assert read is not None
    assert read.vault_gold == 900
    assert read.rank_of(officer) is GuildRank.OFFICER
    assert (await guilds.by_name("ИРИСЫ")) is not None
    for who in three_fighters:
        assert (await guilds.of(who)) is not None

    await guilds.disband(made.id)


async def test_the_database_keeps_one_guild_per_character(pool, three_fighters) -> None:
    a, b, c = three_fighters
    guilds = PostgresGuildRepository(pool)
    first = await guilds.create("Ирисы", a)
    await guilds.save(first.with_member(b))
    second = await guilds.create("Полынь", c)

    # b нельзя завести во вторую гильдию: строку из первой выметают.
    await guilds.save(second.with_member(b))
    left = await guilds.by_id(first.id)
    assert left is not None and b not in [m.character_id for m in left.members]
    moved = await guilds.of(b)
    assert moved is not None and moved.id == second.id

    await guilds.disband(first.id)
    await guilds.disband(second.id)


async def test_the_vault_withdraw_is_atomic(pool, three_fighters) -> None:
    founder, *_ = three_fighters
    guilds = PostgresGuildRepository(pool)
    made = await guilds.create("Ирисы", founder)
    await guilds.deposit(made.id, 250)

    assert await guilds.withdraw(made.id, 400) is False
    assert await guilds.withdraw(made.id, 250) is True
    kept = await guilds.by_id(made.id)
    assert kept is not None and kept.vault_gold == 0

    await guilds.disband(made.id)


async def test_disbanding_removes_the_guild_and_its_members(pool, three_fighters) -> None:
    founder, officer, _ = three_fighters
    guilds = PostgresGuildRepository(pool)
    made = await guilds.create("Ирисы", founder)
    await guilds.save(made.with_member(officer, GuildRank.OFFICER))

    await guilds.disband(made.id)

    assert await guilds.by_id(made.id) is None
    assert await guilds.of(officer) is None
