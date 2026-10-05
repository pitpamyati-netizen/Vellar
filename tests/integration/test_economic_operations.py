"""M01: ценности и журнал настоящего PostgreSQL при сбое, повторе и конкуренции."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import (
    MissingResourceError,
    OperationCommittedError,
    OperationKeyConflictError,
    OperationOutcomeUnknownError,
    StaleCharacterError,
    atomic_action,
)
from mmorpg.application.services.group_trade import (
    GroupResult,
    GroupTrade,
    release_expired_offers,
    roll_back,
)
from mmorpg.application.services.guild import GuildStore
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.economy import trade_tax
from mmorpg.domain.rules.group_commands import parse_group_command
from mmorpg.domain.rules.group_offers import OFFER_TTL_SECONDS, SWEEP_GRACE_SECONDS
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresGuildRepository,
    PostgresInventoryRepository,
    PostgresPrivacyRepository,
    PostgresTradeRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import PendingWrite, PlayState
from mmorpg.presentation.telegram.handlers.play import _apply, _guild_store_step, _guild_vault_step

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
SWORD = "sword@1#common"
NOW = 10_000


@pytest_asyncio.fixture(loop_scope="session")
async def economy(pool, redis, content):
    ids = [-989_501, -989_502, -989_503]
    await pool.execute("DELETE FROM users WHERE telegram_id = ANY($1::bigint[])", ids)
    pool.economic_cache = redis
    users = PostgresUserRepository(pool)
    characters = PostgresCharacterRepository(pool)
    inventory = PostgresInventoryRepository(pool)
    trades = PostgresTradeRepository(pool)
    scope = f"m01-test:{uuid4()}"
    heroes = []
    for account, name in zip(ids, ("Аргус", "Мерла", "Мирна"), strict=True):
        await users.upsert(User(telegram_id=account))
        hero = await characters.create(
            Character(
                id=0,
                user_id=account,
                name=name,
                race_id="human",
                class_id="warrior",
                gold=500,
            )
        )
        heroes.append(hero)
    await inventory.add(heroes[0].id, SWORD)
    await inventory.add(heroes[1].id, SWORD)
    trade = GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        trades=trades,
        privacy=PostgresPrivacyRepository(pool),
        scope=scope,
    )
    environment = SimpleNamespace(
        pool=pool,
        redis=redis,
        users=users,
        characters=characters,
        inventory=inventory,
        trades=trades,
        trade=trade,
        heroes=heroes,
        ids=ids,
        scope=scope,
        guilds=GuildStore(PostgresGuildRepository(pool), RedisStateCache(redis)),
    )
    try:
        yield environment
    finally:
        await characters.operations.recover()
        await pool.execute("DELETE FROM trades WHERE scope = $1", scope)
        await pool.execute("DELETE FROM users WHERE telegram_id = ANY($1::bigint[])", ids)


async def command(e, text, *, author=0, target=1, operation_id=None, now=NOW, fault=None):
    parsed = parse_group_command(text)
    assert parsed is not None
    return await replace(e.trade, fault_hook=fault).run(
        parsed,
        author_id=e.ids[author],
        target_id=e.ids[target] if target is not None else None,
        now=now,
        operation_id=operation_id,
    )


async def values(e):
    return tuple(
        [
            ((await e.characters.get(hero.id)).gold, await e.inventory.count(hero.id, SWORD))
            for hero in e.heroes
        ]
    )


def fail_at(point):
    async def stop(reached):
        if reached == point:
            raise RuntimeError("controlled failure")

    return stop


async def no_operation(e, operation_id):
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM economic_operations WHERE id = $1", operation_id
        )
        == 0
    )
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM economic_entries WHERE operation_id = $1", operation_id
        )
        == 0
    )


@pytest.mark.parametrize(
    "point",
    ["gift.after_debit", "gift.after_credit", "gift_item.after_debit", "gift_item.after_credit"],
)
async def test_gift_failure_restores_both_participants_and_journal(economy, point):
    e = economy
    before = await values(e)
    operation_id = str(uuid4())
    text = "передать Ветхий меч" if point.startswith("gift_item") else "передать 100 золота"
    with pytest.raises(RuntimeError, match="controlled failure"):
        await command(e, text, operation_id=operation_id, fault=fail_at(point))
    assert await values(e) == before
    await no_operation(e, operation_id)
    # Тот же номер после отмены можно безопасно довести.
    result = await command(e, text, operation_id=operation_id)
    assert result.result in (GroupResult.GOLD_GIVEN, GroupResult.ITEM_GIVEN)


@pytest.mark.parametrize("kind", ["продать", "купить"])
@pytest.mark.parametrize("point", ["offer.after_open", "offer.after_stake"])
async def test_proposal_failure_cannot_create_unbacked_escrow(economy, kind, point):
    e = economy
    before = await values(e)
    operation_id = str(uuid4())
    with pytest.raises(RuntimeError, match="controlled failure"):
        await command(e, f"{kind} 100 Ветхий меч", operation_id=operation_id, fault=fail_at(point))
    assert await values(e) == before
    assert await e.pool.fetchval("SELECT count(*) FROM trades WHERE scope = $1", e.scope) == 0
    await no_operation(e, operation_id)


@pytest.mark.parametrize(
    ("kind", "point"),
    [
        (kind, point)
        for kind, debit in (("продать", "gold"), ("купить", "item"))
        for point in (
            f"settle.after_{debit}_debit",
            "settle.after_close",
            "settle.after_item_credit",
            "settle.after_gold_credit",
        )
    ],
)
async def test_settlement_rolls_back_at_every_write_boundary(economy, kind, point):
    e = economy
    offer = await command(e, f"{kind} 100 Ветхий меч")
    before = await values(e)
    operation_id = str(uuid4())
    with pytest.raises(RuntimeError, match="controlled failure"):
        await command(
            e,
            f"принять {offer.offer.number}",
            author=1,
            target=None,
            operation_id=operation_id,
            fault=fail_at(point),
        )
    assert await values(e) == before
    assert await e.trades.pending(offer.offer.number, scope=e.scope) is not None
    await no_operation(e, operation_id)
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM gold_flow WHERE operation_id = $1", operation_id
        )
        == 0
    )


async def test_acceptance_replay_survives_new_repository_and_has_one_audit(economy):
    e = economy
    offer = await command(e, "продать 100 Ветхий меч")
    operation_id = str(uuid4())
    results = await asyncio.gather(
        *[
            command(
                e, f"принять {offer.offer.number}", author=1, target=None, operation_id=operation_id
            )
            for _ in range(2)
        ]
    )
    assert results[0] == results[1]
    assert results[0].result is GroupResult.OFFER_ACCEPTED
    e.trade = replace(
        e.trade,
        characters=PostgresCharacterRepository(e.pool),
        trades=PostgresTradeRepository(e.pool),
    )
    repeated = await command(
        e,
        f"принять {offer.offer.number}",
        author=1,
        target=None,
        operation_id=operation_id,
        now=NOW + 1000,
    )
    assert repeated == results[0]
    assert await values(e) == ((595, 0), (400, 2), (500, 0))
    rows = await e.pool.fetch(
        "SELECT resource, sum(amount) AS total FROM economic_entries"
        " WHERE operation_id = $1 GROUP BY resource",
        operation_id,
    )
    assert {row["resource"]: row["total"] for row in rows} == {"gold": -trade_tax(100), "item": 0}
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM gold_flow WHERE operation_id = $1", operation_id
        )
        == 2
    )


async def test_reusing_an_operation_number_for_another_gift_is_rejected(economy):
    e = economy
    operation_id = str(uuid4())
    await command(e, "передать 100 золота", operation_id=operation_id)
    with pytest.raises(OperationKeyConflictError):
        await command(e, "передать 200 золота", operation_id=operation_id)
    assert await values(e) == ((400, 1), (600, 1), (500, 0))


async def test_two_buyers_compete_for_last_item_without_creating_another(economy):
    e = economy
    first = await command(e, "купить 100 Ветхий меч")
    second = await command(e, "купить 100 Ветхий меч", author=2)
    results = await asyncio.gather(
        *[
            command(e, f"принять {one.offer.number}", author=1, target=None)
            for one in (first, second)
        ]
    )
    assert sum(one.result is GroupResult.OFFER_ACCEPTED for one in results) == 1
    assert sum(one.result is GroupResult.REFUSED for one in results) == 1
    balances = await values(e)
    assert sum(gold for gold, _ in balances) == 1500 - trade_tax(100)
    assert sum(items for _, items in balances) == 2
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM trades WHERE scope = $1 AND status = 'pending'", e.scope
        )
        == 0
    )


@pytest.mark.parametrize("kind", ["продать", "купить"])
@pytest.mark.parametrize("action", ["decline", "expire"])
async def test_failure_returning_reserve_keeps_offer_and_can_be_retried(
    economy, monkeypatch, kind, action
):
    e = economy
    offer = await command(e, f"{kind} 100 Ветхий меч")
    before = await values(e)
    repo = e.inventory if kind == "продать" else e.characters
    method = "add" if kind == "продать" else "grant_gold"
    original = getattr(repo, method)

    async def broken(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(repo, method, broken)

    async def cancel():
        if action == "decline":
            return await command(e, f"отказ {offer.offer.number}", author=1, target=None)
        return await release_expired_offers(
            trades=e.trades,
            characters=e.characters,
            inventory=e.inventory,
            now=NOW + OFFER_TTL_SECONDS + SWEEP_GRACE_SECONDS + 1,
        )

    with pytest.raises(RuntimeError, match="controlled failure"):
        await cancel()
    assert await values(e) == before
    assert await e.trades.pending(offer.offer.number, scope=e.scope) is not None
    monkeypatch.setattr(repo, method, original)
    await cancel()
    assert await values(e) == ((500, 1), (500, 1), (500, 0))


async def test_keeper_rollback_is_atomic_and_safe_to_repeat(economy, monkeypatch):
    e = economy
    offer = await command(e, "продать 100 Ветхий меч")
    await command(e, f"принять {offer.offer.number}", author=1, target=None)
    record = (await e.trades.journal(e.heroes[0].id))[0]
    before = await values(e)
    original = e.characters.grant_gold

    async def broken(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(e.characters, "grant_gold", broken)
    with pytest.raises(RuntimeError, match="controlled failure"):
        await roll_back(record.id, trades=e.trades, characters=e.characters, inventory=e.inventory)
    assert await values(e) == before
    assert (await e.trades.journal(e.heroes[0].id))[0].status.value == "accepted"
    monkeypatch.setattr(e.characters, "grant_gold", original)
    operation_id = str(uuid4())
    rollback = await roll_back(
        record.id,
        trades=e.trades,
        characters=e.characters,
        inventory=e.inventory,
        operation_id=operation_id,
    )
    repeated = await roll_back(
        record.id,
        trades=e.trades,
        characters=e.characters,
        inventory=e.inventory,
        operation_id=operation_id,
    )
    assert rollback == repeated and rollback.whole
    assert await values(e) == ((500, 1), (495, 1), (500, 0))


async def test_stale_character_cannot_erase_trade_even_with_inventory_write(economy):
    e = economy
    old = await e.characters.get(e.heroes[0].id)
    await command(e, "передать 100 золота")
    with pytest.raises(StaleCharacterError):
        await _apply(
            PendingWrite(character=old.with_gold(30), items=((SWORD, 1),)),
            old,
            old.user_id,
            e.characters,
            e.inventory,
            e.users,
        )
    assert await values(e) == ((400, 1), (600, 1), (500, 0))


async def test_missing_craft_material_cancels_payment_and_product(economy):
    e = economy
    hero = await e.characters.get(e.heroes[0].id)
    write = PendingWrite(
        character=hero.with_gold(-100),
        items=((SWORD, -1), ("missing-material", -1), ("new-product", 1)),
    )
    with pytest.raises(MissingResourceError):
        await _apply(write, hero, hero.user_id, e.characters, e.inventory, e.users)
    assert await values(e) == ((500, 1), (500, 1), (500, 0))
    assert await e.inventory.count(hero.id, "new-product") == 0


@pytest.mark.parametrize("bank", [False, True])
async def test_bank_and_reward_racing_with_trade_preserve_both_effects(economy, bank):
    e = economy

    @atomic_action
    async def change(characters, inventory, users) -> None:
        hero = await characters.get(e.heroes[0].id)
        updated = (
            replace(hero, gold=hero.gold - 80, bank_gold=hero.bank_gold + 80)
            if bank
            else hero.with_gold(70)
        )
        await _apply(
            PendingWrite(character=updated), hero, hero.user_id, characters, inventory, users
        )

    await asyncio.gather(
        command(e, "передать 100 золота"), change(e.characters, e.inventory, e.users)
    )
    hero = await e.characters.get(e.heroes[0].id)
    assert (hero.gold, hero.bank_gold) == ((320, 80) if bank else (470, 0))


@pytest.mark.parametrize("store", [False, True])
async def test_guild_value_transfer_rolls_back_both_storages(economy, store, content, monkeypatch):
    e = economy
    guild = await e.guilds._roster.create(f"Гильдия {uuid4().hex[:8]}", e.heroes[0].id)

    original = e.guilds._roster.stow if store else e.guilds._roster.deposit

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(e.guilds._roster, "stow" if store else "deposit", fail)
    with pytest.raises(RuntimeError, match="controlled failure"):
        if store:
            await _guild_store_step(
                content,
                e.heroes[0],
                PlayState(vault_action="stow", vault_item=SWORD, vault_amount=1),
                e.inventory,
                e.guilds,
                Settings(),
                NOW,
            )
        else:
            await _guild_vault_step(
                content,
                e.heroes[0],
                "deposit",
                "100",
                e.characters,
                e.guilds,
                guild,
                Settings(),
                NOW,
            )
    assert (await e.guilds.by_id(guild.id)).vault_gold == 0
    assert await e.guilds.stock(guild.id) == ()
    assert await values(e) == ((500, 1), (500, 1), (500, 0))


async def test_postgres_profile_result_with_equipment_is_replayable(economy):
    e = economy
    hero = await e.characters.get(e.heroes[0].id)
    await e.characters.save(replace(hero, equipment=hero.equipment.equip("weapon", SWORD)))
    key = str(uuid4())
    first = await command(e, "профиль", author=1, target=0, operation_id=key)
    second = await command(e, "профиль", author=1, target=0, operation_id=key)
    assert first == second
    assert second.character.equipment.item_in("weapon") == SWORD


async def test_cache_and_fsm_do_not_change_when_sql_rolls_back(economy):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.redis import RedisStorage

    from mmorpg.infrastructure.cache.operations import TransactionalRedis

    e = economy
    cache = RedisStateCache(e.redis)
    storage = RedisStorage(TransactionalRedis(e.redis))
    state = FSMContext(
        storage=storage, key=StorageKey(bot_id=1, chat_id=e.ids[0], user_id=e.ids[0])
    )
    key = f"m01-cache:{uuid4()}"
    await cache.set(key, "ongoing", 60)
    await state.set_data({"battle": "old turn"})

    @atomic_action
    async def fail(characters) -> None:
        await characters.spend_gold(e.heroes[0].id, 100)
        await cache.set(key, "settled", 60)
        await state.set_data({"battle": "next turn"})
        assert await cache.get(key) == "settled"
        assert (await state.get_data())["battle"] == "next turn"

        # Другая задача читает настоящее состояние, пока изменений ещё нет.
        async def outside():
            assert await cache.get(key) == "ongoing"
            assert (await state.get_data())["battle"] == "old turn"

        await asyncio.create_task(outside())
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled failure"):
        await fail(e.characters)
    assert await cache.get(key) == "ongoing"
    assert (await state.get_data())["battle"] == "old turn"
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    await cache.delete(key)
    await state.clear()


async def test_saved_cache_handoff_recovers_before_new_action_and_never_pays_twice(
    economy, monkeypatch
):
    import mmorpg.infrastructure.persistence.operations as infrastructure

    e = economy
    cache = RedisStateCache(e.redis)
    key = f"m01-cache:{uuid4()}"
    operation_id = str(uuid4())

    @atomic_action
    async def move(characters, operation_id: str) -> int:
        await characters.grant_gold(e.heroes[0].id, 50)
        await cache.set(key, "settled", 60)
        return 50

    original = infrastructure.apply_changes

    async def fail(*args):
        raise ConnectionError("controlled Redis outage after SQL commit")

    monkeypatch.setattr(infrastructure, "apply_changes", fail)
    with pytest.raises(OperationCommittedError):
        await move(e.characters, operation_id)
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    assert await cache.get(key) is None
    row = await e.pool.fetchrow("SELECT * FROM economic_operations WHERE id = $1", operation_id)
    assert row["completed"] and not row["cache_applied"]
    assert len(json.loads(row["cache_changes"])) == 1
    monkeypatch.setattr(infrastructure, "apply_changes", original)
    # Новый адаптер имитирует новый процесс, но читает тот же постоянный журнал.
    assert await move(PostgresCharacterRepository(e.pool), operation_id) == 50
    assert await cache.get(key) == "settled"
    assert (await e.characters.get(e.heroes[0].id)).gold == 550
    assert await e.pool.fetchval(
        "SELECT cache_applied FROM economic_operations WHERE id = $1", operation_id
    )
    await cache.delete(key)


async def test_cancellation_rolls_back_and_releases_shared_lock(economy):
    e = economy
    reached = asyncio.Event()

    async def pause(point):
        if point == "gift.after_debit":
            reached.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(command(e, "передать 100 золота", fault=pause))
    await asyncio.wait_for(reached.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await values(e) == ((500, 1), (500, 1), (500, 0))
    result = await asyncio.wait_for(command(e, "передать 100 золота"), 2)
    assert result.result is GroupResult.GOLD_GIVEN


async def test_connection_termination_inside_transaction_cannot_leave_partial_gift(economy):
    from asyncpg import InterfaceError

    from mmorpg.infrastructure.persistence.operations import OperationPool

    e = economy

    async def terminate(point):
        if point == "gift.after_debit":
            connection = OperationPool(e.pool).active()
            connection.terminate()

    with pytest.raises(InterfaceError):
        await command(e, "передать 100 золота", fault=terminate)
    assert await values(e) == ((500, 1), (500, 1), (500, 0))


@pytest.mark.parametrize("phase", ["commit", "unlock", "release", "unconfirmed"])
async def test_lost_reply_after_commit_never_claims_that_transfer_was_cancelled(
    economy, monkeypatch, phase
):
    from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool

    e = economy
    original = ReconnectingPool.acquire
    injected = False
    operation_id = str(uuid4())

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
            if phase in ("commit", "unconfirmed") and not injected:
                injected = True
                raise ConnectionResetError("controlled lost COMMIT reply")

        async def execute(self, query, *args):
            nonlocal injected
            result = await self.connection.execute(query, *args)
            if "pg_advisory_unlock" in query and phase == "unlock" and not injected:
                injected = True
                raise ConnectionResetError("controlled lost unlock reply")
            return result

    @asynccontextmanager
    async def acquire(pool):
        nonlocal injected
        if phase == "unconfirmed" and injected:
            raise ConnectionResetError("controlled PostgreSQL outage during reconciliation")
        async with original(pool) as connection:
            yield Connection(connection)
        if phase == "release" and not injected:
            injected = True
            raise ConnectionResetError("controlled pool release failure")

    with monkeypatch.context() as patch:
        patch.setattr(ReconnectingPool, "acquire", acquire)
        if phase == "commit":
            result = await command(e, "передать 100 золота", operation_id=operation_id)
            assert result.result is GroupResult.GOLD_GIVEN
        else:
            expected = (
                OperationOutcomeUnknownError if phase == "unconfirmed" else OperationCommittedError
            )
            with pytest.raises(expected):
                await command(e, "передать 100 золота", operation_id=operation_id)
    assert await values(e) == ((400, 1), (600, 1), (500, 0))
    # Исходный запрос доводится без второго списания после восстановления связи.
    repeated = await command(e, "передать 100 золота", operation_id=operation_id)
    assert repeated.result is GroupResult.GOLD_GIVEN
    assert await values(e) == ((400, 1), (600, 1), (500, 0))
