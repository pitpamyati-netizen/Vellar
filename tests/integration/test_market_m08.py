"""SQL проверяет резерв, журнал, конкуренцию, отмену и восстановление."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.market import Market
from mmorpg.domain.entities.market import MarketResult
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from tests.integration.test_economic_operations import economy as economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
NOW = 1000


@pytest_asyncio.fixture(loop_scope="session")
async def market_sql(economy, content):
    e = economy
    await e.pool.execute("DELETE FROM gameplay_state WHERE key='market:board'")
    e.market = Market(
        content, PostgresGameplayState(e.pool, InMemoryStateCache()), e.characters, e.inventory
    )
    try:
        yield e
    finally:
        await e.pool.execute("DELETE FROM gameplay_state WHERE key='market:board'")


@atomic_action
async def press(service, author_id, command, *, operation_id=None) -> MarketResult:
    parts = command.split()
    if parts[0] == "sell":
        result = await service.publish(author_id, "iron_scrap", 2, 101, NOW)
    elif parts[0] == "order":
        result = await service.order(author_id, "smith_ingot@1", 101, NOW)
    elif parts[0] == "make":
        result = await service.fulfill(author_id, int(parts[1]), 101, NOW)
    else:
        result = await service.purchase(
            author_id, int(parts[1]), 101, NOW, reserve=parts[0] == "reserve"
        )
    if parts[-1] == "break":
        raise RuntimeError("after market writes")
    return result


async def net(e, operation_id, resource):
    return await e.pool.fetchval(
        "SELECT coalesce(sum(amount),0) FROM economic_entries "
        "WHERE operation_id=$1 AND resource=$2",
        operation_id,
        resource,
    )


async def test_sql_publish_buy_failure_replay_and_last_lot(market_sql):
    e = market_sql
    seller, buyer, other = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    with pytest.raises(RuntimeError):
        await press(e.market, seller.id, "sell break", operation_id="m08:" + uuid4().hex)
    assert await e.inventory.count(seller.id, "iron_scrap") == 2
    assert not (await e.market.load()).lots
    sell_key = "m08:" + uuid4().hex
    lot = await press(e.market, seller.id, "sell", operation_id=sell_key)
    assert await net(e, sell_key, "item") == 0
    assert (
        await e.pool.fetchval(
            "SELECT count(*) FROM economic_entries "
            "WHERE operation_id=$1 AND container='market_escrow'",
            sell_key,
        )
        == 1
    )
    with pytest.raises(RuntimeError):
        await press(e.market, buyer.id, f"buy {lot.id} break", operation_id="m08:" + uuid4().hex)
    assert (await e.characters.get(buyer.id)).gold == 500
    keys = ["m08:" + uuid4().hex for _ in range(2)]
    results = await asyncio.gather(
        *(
            press(e.market, actor.id, f"buy {lot.id}", operation_id=key)
            for actor, key in zip((buyer, other), keys, strict=True)
        )
    )
    assert sum(one.ok for one in results) == 1
    assert sum([await net(e, key, "gold") for key in keys]) == -5
    assert sum([await net(e, key, "item") for key in keys]) == 0
    await e.redis.flushdb()
    fresh = Market(
        e.market.content,
        PostgresGameplayState(e.pool, InMemoryStateCache()),
        e.characters,
        e.inventory,
    )
    assert await press(fresh, seller.id, "sell", operation_id=sell_key) == lot
    assert (await fresh.load()).lots[0].status == "sold"
    for actor, key, result in zip((buyer, other), keys, results, strict=True):
        assert await press(fresh, actor.id, f"buy {lot.id}", operation_id=key) == result
    assert sum([(await e.characters.get(one.id)).gold for one in e.heroes]) == 1495


async def test_sql_reserve_refund_and_expiry_journal_conserve_everything(market_sql):
    e = market_sql
    seller, buyer, _ = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    lot = await press(e.market, seller.id, "sell", operation_id="m08:" + uuid4().hex)
    key = "m08:" + uuid4().hex
    assert (await press(e.market, buyer.id, f"reserve {lot.id}", operation_id=key)).ok
    assert await net(e, key, "gold") == 0
    assert (await e.characters.get(buyer.id)).gold == 399
    fresh = Market(
        e.market.content,
        PostgresGameplayState(e.pool, InMemoryStateCache()),
        e.characters,
        e.inventory,
    )
    await e.redis.flushdb()
    assert await fresh.sweep(NOW + 604800) == 1
    assert await fresh.sweep(NOW + 604800) == 0
    assert (await e.characters.get(buyer.id)).gold == 500
    assert await e.inventory.count(seller.id, "iron_scrap") == 2
    total = await e.pool.fetchval(
        "SELECT coalesce(sum(amount),0) FROM economic_entries "
        "WHERE container='market_escrow' AND owner_id=ANY($1::bigint[])",
        [one.id for one in e.heroes],
    )
    assert total == 0


async def test_sql_craft_order_rollback_two_makers_and_saved_result(market_sql):
    e = market_sql
    buyer, maker, other = e.heroes
    for person in (maker, other):
        await e.inventory.add(person.id, "iron_scrap", 2)
    order_key = "m08:" + uuid4().hex
    order = await press(e.market, buyer.id, "order", operation_id=order_key)
    assert await net(e, order_key, "gold") == 0
    with pytest.raises(RuntimeError):
        await press(e.market, maker.id, f"make {order.id} break", operation_id="m08:" + uuid4().hex)
    assert await e.inventory.count(maker.id, "iron_scrap") == 2
    assert (await e.characters.get(maker.id)).gold == 500
    keys = ["m08:" + uuid4().hex for _ in range(2)]
    results = await asyncio.gather(
        *(
            press(e.market, person.id, f"make {order.id}", operation_id=key)
            for person, key in zip((maker, other), keys, strict=True)
        )
    )
    assert sum(one.ok for one in results) == 1
    assert sum([await net(e, key, "gold") for key in keys]) == -5
    state = await e.market.load()
    filled = state.orders[0]
    assert filled.status == "filled" and filled.quantity >= 1
    assert await e.inventory.count(buyer.id, filled.item_id) == filled.quantity
    assert sum([await e.inventory.count(person.id, "iron_scrap") for person in (maker, other)]) == 2


async def test_physical_gear_keeps_number_wear_in_escrow_and_return(market_sql):
    from dataclasses import replace

    from mmorpg.domain.entities.character import ItemWear
    from mmorpg.infrastructure.persistence.postgres import PostgresInventoryRepository

    e = market_sql
    e.market.inventory = PostgresInventoryRepository(e.pool, e.market.content)
    hero = e.heroes[0]
    await e.market.inventory.add(hero.id, "sword@1#common")
    item = next(one for one in await e.market.inventory.list_items(hero.id) if "!" in one.item_id)
    actor = await e.characters.get(hero.id)
    await e.characters.save(replace(actor, wear=ItemWear({item.item_id: 7})))
    lot = await e.market.publish(hero.id, item.item_id, 1, 101, NOW)
    assert lot.ok
    with pytest.raises(asyncpg.ForeignKeyViolationError, match="active market escrow"):
        await e.characters.delete(hero.id)
    assert (await e.market.cancel(hero.id, lot.id, NOW)).ok
    assert await e.market.inventory.count(hero.id, item.item_id) == 1
    assert (await e.characters.get(hero.id)).wear.spent(item.item_id) == 7
    second = await e.market.publish(hero.id, item.item_id, 1, 101, NOW)
    assert (await e.market.purchase(e.heroes[1].id, second.id, 101, NOW)).ok
    assert (await e.characters.get(e.heroes[1].id)).wear.spent(item.item_id) == 7


@atomic_action
async def return_broken(service, *, operation_id=None) -> int:
    result = await service.sweep(NOW + 604800)
    raise RuntimeError(f"after refund {result}")


async def test_expiry_failure_rolls_back_then_returns_and_cleanup_keeps_owner(market_sql):
    e = market_sql
    seller, buyer, _ = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    lot = await e.market.publish(seller.id, "iron_scrap", 2, 101, NOW)
    await e.market.purchase(buyer.id, lot.id, 101, NOW, reserve=True)
    await e.pool.execute("UPDATE users SET blocked_at=1 WHERE telegram_id=ANY($1::bigint[])", e.ids)
    # Третьего можно очистить; двое с резервом остаются, а вся очистка не падает.
    await e.users.purge_blocked()
    assert await e.characters.get(seller.id) and await e.characters.get(buyer.id)
    with pytest.raises(RuntimeError, match="after refund"):
        await return_broken(e.market, operation_id="m08:" + uuid4().hex)
    assert (await e.characters.get(buyer.id)).gold == 399
    assert not await e.inventory.count(seller.id, "iron_scrap")
    assert (await e.market.load()).lots[0].status == "reserved"
    assert await e.market.sweep(NOW + 604800) == 1
    assert (await e.characters.get(buyer.id)).gold == 500
    assert await e.inventory.count(seller.id, "iron_scrap") == 2
