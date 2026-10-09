"""Изолированная копия сохраняет товар, износ, оплату, заказ и старый ответ."""

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from aiogram.fsm.storage.base import StorageKey
from scripts.test_stand import load_test_settings

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.market import Market
from mmorpg.domain.entities.character import Character, ItemWear
from mmorpg.domain.entities.market import MarketResult
from mmorpg.domain.ports.repositories import User
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresInventoryRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play

ACCOUNTS = (-99990800, -99990801)


@atomic_action
async def reserve(
    service: Market, author_id: int, command: str, *, operation_id: str
) -> MarketResult:
    return await service.purchase(author_id, int(command), 101, 1000, reserve=True)


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only isolated test databases are allowed")
    dsn = urlunsplit(urlsplit(settings.postgres_dsn)._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        content = load_content(Path("content"))
        characters = PostgresCharacterRepository(pool)
        inventory = PostgresInventoryRepository(pool, content)
        cache = PostgresGameplayState(pool, InMemoryStateCache())
        service = Market(content, cache, characters, inventory)
        storage = PostgresStorage(pool)
        key = StorageKey(bot_id=1, chat_id=ACCOUNTS[1], user_id=ACCOUNTS[1])
        if seed:
            users = PostgresUserRepository(pool)
            people = []
            for index, account in enumerate(ACCOUNTS):
                await users.upsert(User(telegram_id=account))
                people.append(
                    await characters.create(
                        Character(
                            id=0,
                            user_id=account,
                            name=f"Рынок{index}",
                            race_id="human",
                            class_id="warrior",
                            gold=500,
                        )
                    )
                )
            seller, buyer = people
            await inventory.add(seller.id, "sword@1#common")
            item = (await inventory.list_items(seller.id))[0].item_id
            actor = await characters.get(seller.id)
            assert actor
            await characters.save(replace(actor, wear=ItemWear({item: 7})))
            lot = await service.publish(seller.id, item, 1, 101, 1000)
            assert lot.ok
            assert (
                await reserve(service, buyer.id, str(lot.id), operation_id="m08-probe:reserve")
            ).ok
            await inventory.add(seller.id, "iron_scrap", 2)
            order = await service.order(buyer.id, "smith_ingot@1", 101, 1000)
            assert (await service.fulfill(seller.id, order.id, 101, 1000)).ok
            await storage.set_state(key, Play.market)
            await storage.set_data(
                key,
                {
                    "play": PlayState(
                        screen=ScreenId.MARKET,
                        market_mode="confirm",
                        market_target=str(lot.id),
                        market_price=101,
                        market_action="купить",
                    ).serialise()
                },
            )
        else:
            seller = await characters.get_active(ACCOUNTS[0])
            buyer = await characters.get_active(ACCOUNTS[1])
            assert seller and buyer and seller.gold == 596 and buyer.gold == 298
            state = await service.load()
            lot = state.lots[0]
            assert lot.status == "reserved" and lot.buyer_id == buyer.id
            assert not await inventory.count(seller.id, lot.item_id)
            number = int(lot.item_id.partition("!")[2])
            assert await pool.fetchval("SELECT used FROM item_instances WHERE id=$1", number) == 7
            assert state.orders[0].status == "filled"
            assert await inventory.count(buyer.id, "crude_ingot") == state.orders[0].quantity
            assert (
                await reserve(service, buyer.id, str(lot.id), operation_id="m08-probe:reserve")
            ).ok
            assert (await characters.get(buyer.id)).gold == 298
            flow = PlayState.deserialise((await storage.get_data(key))["play"])
            assert flow.market_mode == "confirm" and flow.market_price == 101
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM economic_entries WHERE operation_id='m08-probe:reserve'"
                )
                == 2
            )
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
    print("M08: market escrow, wear, order, confirmation and replay verified")


if __name__ == "__main__":
    main()
