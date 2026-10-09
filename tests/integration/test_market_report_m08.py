"""Отчёт использует настоящий SQL и не превращает резерв в расход."""

import json
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from scripts.m08_economy import read_report, render

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.market import Market
from mmorpg.domain.entities.market import MarketResult
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from tests.integration.test_economic_operations import economy as economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def report_data(pool):
    scope = f"m08-report:{uuid4()}"
    ids = []

    async def add(name, entries, *, age=0, complete=True, private=False):
        operation_id = f"{scope}:{name}"
        ids.append(operation_id)
        await pool.execute(
            "INSERT INTO economic_operations(id,kind,completed,created_at,result)"
            " VALUES($1,$2,$3,now()-$4::double precision * interval '1 hour',$5::jsonb)",
            operation_id,
            scope,
            complete,
            age,
            json.dumps({"replies": ["секретная переписка"]}) if private else "null",
        )
        for resource, item_id, amount, container in entries:
            await pool.execute(
                "INSERT INTO economic_entries(operation_id,owner_kind,owner_id,container,"
                "resource,item_id,amount) VALUES($1,'character',17,$2,$3,$4,$5)",
                operation_id,
                container,
                resource,
                item_id,
                amount,
            )
        return operation_id

    try:
        yield scope, add
    finally:
        await pool.execute("DELETE FROM gold_flow WHERE operation_id=ANY($1::text[])", ids)
        await pool.execute("DELETE FROM economic_entries WHERE operation_id=ANY($1::text[])", ids)
        await pool.execute("DELETE FROM economic_operations WHERE id=ANY($1::text[])", ids)


async def report(pool, content, **options):
    async with pool.acquire() as connection:
        return await read_report(connection, content, **options)


async def test_creation_and_consumption_do_not_cancel_each_other_between_operations(
    pool, content, report_data
):
    scope, add = report_data
    await add("create", [("gold", "", 50, "gold"), ("item", "iron_scrap", 4, "bag")])
    await add("consume", [("gold", "", -50, "gold"), ("item", "iron_scrap", -4, "bag")])
    await add("transfer", [("gold", "", -100, "gold"), ("gold", "", 100, "bank_gold")])
    await add(
        "reserve", [("item", "iron_scrap", -2, "bag"), ("item", "iron_scrap", 2, "market_escrow")]
    )
    await add("old", [("gold", "", 777, "gold")], age=25)
    await add("unfinished", [("gold", "", 333, "gold")], complete=False)
    result = await report(pool, content, hours=24)
    rows = {one.resource: one for one in result.movements if one.kind == scope}
    assert (rows["gold"].created, rows["gold"].consumed, rows["gold"].transfers) == (50, 50, 1)
    assert (rows["item"].created, rows["item"].consumed, rows["item"].transfers) == (4, 4, 1)
    lifetime = await report(pool, content, hours=0)
    gold = next(one for one in lifetime.movements if one.kind == scope and one.resource == "gold")
    assert gold.created == 827 and gold.consumed == 50
    text = render(result, content)
    assert "сам по себе не доказывает инфляцию" in text


async def test_resources_are_balanced_independently_inside_one_operation(
    pool, content, report_data
):
    scope, add = report_data
    await add("craft", [("item", "iron_scrap", -3, "bag"), ("item", "iron_ingot", 1, "bag")])
    result = await report(pool, content)
    rows = {one.item_id: one for one in result.movements if one.kind == scope}
    assert rows["iron_scrap"].consumed == 3 and rows["iron_scrap"].created == 0
    assert rows["iron_ingot"].created == 1 and rows["iron_ingot"].consumed == 0


@pytest_asyncio.fixture(loop_scope="session")
async def market_report_env(economy, content):
    e = economy
    await e.pool.execute("DELETE FROM gameplay_state WHERE key='market:board'")
    cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, RedisStateCache(e.redis)))
    e.market = Market(content, cache, e.characters, e.inventory)
    e.content = content
    e.now = int(await e.pool.fetchval("SELECT extract(epoch from now())"))
    await e.inventory.add(e.heroes[0].id, "iron_scrap", 3)
    try:
        yield e
    finally:
        await e.pool.execute("DELETE FROM gameplay_state WHERE key='market:board'")


@atomic_action
async def market_action(service, actor_id, action, args, *, operation_id=None) -> MarketResult:
    return await getattr(service, action)(actor_id, *args)


async def test_real_publish_reserve_and_purchase_count_only_duty_as_gold_consumption(
    market_report_env,
):
    e = market_report_env
    seller, buyer = e.heroes[:2]
    opened = await market_action(e.market, seller.id, "publish", ("iron_scrap", 3, 100, e.now))
    assert opened.ok
    await e.market.purchase(buyer.id, opened.id, 100, e.now, reserve=True)
    before = await report(e.pool, e.content, lot_id=opened.id)
    scrap = next(one for one in before.demand if one.item_id == "iron_scrap")
    assert (scrap.offered, scrap.reserved, scrap.sold) == (0, 3, 0)
    assert before.investigation.category == "лот"
    for receipt in before.investigation.receipts:
        deltas = {}
        for entry in receipt.entries:
            key = (entry["resource"], entry["item_id"])
            deltas[key] = deltas.get(key, 0) + entry["amount"]
        assert all(amount == 0 for amount in deltas.values())
    await e.market.purchase(buyer.id, opened.id, 100, e.now)
    after = await report(e.pool, e.content, lot_id=opened.id)
    assert after.investigation.status == "sold" and after.investigation.tax == 5
    assert next(one for one in after.demand if one.item_id == "iron_scrap").sold == 3
    purchase = after.investigation.receipts[-1]
    gold = sum(one["amount"] for one in purchase.entries if one["resource"] == "gold")
    items = sum(one["amount"] for one in purchase.entries if one["resource"] == "item")
    assert gold == -5 and items == 0
    flows = {one["flow"]: one["amount"] for one in purchase.gold_flows}
    assert flows == {"trade_price": 95, "trade_duty": -5}
    assert (await e.characters.get(seller.id)).gold == seller.gold + 95
    assert (await e.characters.get(buyer.id)).gold == buyer.gold - 100


async def test_order_demand_and_investigation_never_read_reply_or_private_detail(market_report_env):
    e = market_report_env
    recipe = e.content.market_rules.order_recipes[0]
    opened = await e.market.order(e.heroes[0].id, recipe, 100, e.now)
    assert opened.ok
    state = await e.market.load()
    receipt_id = state.orders[0].receipts[0]
    await e.pool.execute(
        "UPDATE economic_operations SET result=$2::jsonb WHERE id=$1",
        receipt_id,
        json.dumps({"replies": ["секретная переписка"]}),
    )
    await e.pool.execute(
        "INSERT INTO gold_flow(flow,amount,character_id,detail,operation_id)"
        " VALUES('test',0,$1,'секретный текст',$2)",
        e.heroes[0].id,
        receipt_id,
    )
    result = await report(e.pool, e.content, lot_id=opened.id)
    demand = next(one for one in result.demand if one.item_id == e.content.recipe(recipe).output_id)
    assert demand.orders == 1 and demand.ordered_base == e.content.recipe(recipe).output_count
    assert result.investigation.category == "заказ"
    assert sum(one["amount"] for one in result.investigation.receipts[0].entries) == 0
    text = render(result, e.content)
    assert "секретная переписка" not in text and "секретный текст" not in text
    await e.market.cancel_order(e.heroes[0].id, opened.id, e.now)
    cancelled = await report(e.pool, e.content, lot_id=opened.id)
    assert cancelled.investigation.status == "cancelled" and not cancelled.demand


async def test_read_only_snapshot_rejects_writes_and_keeps_same_market_state(
    pool, content, report_data
):
    _, add = report_data
    operation_id = await add("known", [("gold", "", 3, "gold")])

    class WatchedConnection:
        def __init__(self, connection):
            self.connection = connection
            self.snapshot = False

        def transaction(self, **options):
            assert options == {"isolation": "repeatable_read", "readonly": True}
            return self.connection.transaction(**options)

        async def fetchval(self, query, *args):
            if query == "SELECT transaction_timestamp()":
                assert await self.connection.fetchval("SHOW transaction_read_only") == "on"
                assert (
                    await self.connection.fetchval("SHOW transaction_isolation")
                    == "repeatable read"
                )
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    async with self.connection.transaction():
                        await self.connection.execute(
                            "UPDATE economic_operations SET completed=false WHERE id=$1",
                            operation_id,
                        )
                self.snapshot = True
            return await self.connection.fetchval(query, *args)

        async def fetch(self, query, *args):
            assert self.snapshot
            # Запись другого соединения после открытия снимка не изменяет отчёт.
            if "WITH operation_delta" in query:
                await pool.execute(
                    "UPDATE economic_operations SET completed=false WHERE id=$1", operation_id
                )
            return await self.connection.fetch(query, *args)

        async def fetchrow(self, query, *args):
            return await self.connection.fetchrow(query, *args)

    async with pool.acquire() as connection:
        result = await read_report(WatchedConnection(connection), content)
    row = next(one for one in result.movements if one.kind.startswith("m08-report:"))
    assert row.created == 3
