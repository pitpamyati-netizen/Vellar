"""Рынок, резерв и городское снабжение: несколько участников и повтор."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.city_event import CityEvents
from mmorpg.application.services.market import Market
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.market import MarketResult
from mmorpg.domain.rules.market import supply_and_demand
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.instances import MemoryItemInstances
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)

NOW = 1000


@pytest.fixture
async def bazaar(content):
    cache = InMemoryStateCache()
    characters = InMemoryCharacterRepository()
    inventory = InMemoryInventoryRepository(MemoryItemInstances(content))
    heroes = [
        await characters.create(
            Character(
                id=0,
                user_id=8400 + i,
                name=f"Игрок{i}",
                race_id="human",
                class_id="warrior",
                gold=500,
                city_id="farhold",
            )
        )
        for i in range(3)
    ]
    return SimpleNamespace(
        cache=cache,
        characters=characters,
        inventory=inventory,
        heroes=heroes,
        service=Market(content, cache, characters, inventory),
    )


@atomic_action
async def act(service, author_id, command, *, operation_id=None) -> MarketResult:
    parts = command.split()
    if parts[0] == "sell":
        result = await service.publish(author_id, parts[1], 2, 101, NOW)
    else:
        result = await service.purchase(author_id, int(parts[1]), 101, NOW)
    if parts[-1] == "break":
        raise RuntimeError("after all writes")
    return result


async def test_last_lot_two_buyers_and_repeat_keep_tax_and_quantity(bazaar):
    e = bazaar
    seller, first, second = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    lot = await e.service.publish(seller.id, "iron_scrap", 2, 101, NOW)
    results = await asyncio.gather(
        *(e.service.purchase(hero.id, lot.id, 101, NOW) for hero in (first, second))
    )
    assert sum(one.ok for one in results) == 1
    assert sum([(await e.characters.get(hero.id)).gold for hero in e.heroes]) == 1495
    assert sum([await e.inventory.count(hero.id, "iron_scrap") for hero in (first, second)]) == 2
    assert not (await e.service.purchase(first.id, lot.id, 101, NOW)).ok
    assert (await e.service.load()).lots[0].tax == 5


async def test_reserve_return_and_expiry_restore_once(bazaar):
    e = bazaar
    seller, buyer, other = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    lot = await e.service.publish(seller.id, "iron_scrap", 2, 101, NOW)
    assert not (await e.service.purchase(seller.id, lot.id, 101, NOW)).ok
    assert not (await e.service.purchase(buyer.id, lot.id, 102, NOW)).ok
    assert (await e.service.purchase(buyer.id, lot.id, 101, NOW, reserve=True)).ok
    assert (await e.characters.get(buyer.id)).gold == 399
    assert not (await e.service.purchase(other.id, lot.id, 101, NOW)).ok
    assert not (await e.service.cancel(seller.id, lot.id, NOW)).ok
    assert not (await e.service.cancel(other.id, lot.id, NOW, reservation=True)).ok
    assert (await e.service.cancel(buyer.id, lot.id, NOW, reservation=True)).ok
    assert (await e.characters.get(buyer.id)).gold == 500
    assert not (await e.service.cancel(buyer.id, lot.id, NOW, reservation=True)).ok
    assert (await e.service.purchase(buyer.id, lot.id, 101, NOW, reserve=True)).ok
    assert await e.service.sweep(NOW + e.service.content.market_rules.lifetime) == 1
    assert await e.service.sweep(NOW + e.service.content.market_rules.lifetime) == 0
    assert (await e.characters.get(buyer.id)).gold == 500
    assert await e.inventory.count(seller.id, "iron_scrap") == 2


async def test_reserve_purchase_uses_deposit_and_timeout_reopens(bazaar):
    e = bazaar
    seller, buyer, _ = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 4)
    first = await e.service.publish(seller.id, "iron_scrap", 2, 101, NOW)
    second = await e.service.publish(seller.id, "iron_scrap", 2, 101, NOW)
    await e.service.purchase(buyer.id, first.id, 101, NOW, reserve=True)
    assert (await e.service.purchase(buyer.id, first.id, 101, NOW)).ok
    assert (await e.characters.get(buyer.id)).gold == 399
    await e.service.purchase(buyer.id, second.id, 101, NOW, reserve=True)
    assert await e.service.sweep(NOW + e.service.content.market_rules.reservation_lifetime) == 1
    assert (await e.characters.get(buyer.id)).gold == 399
    assert (await e.service.load()).lots[1].status == "open"
    assert (await e.service.cancel(seller.id, second.id, NOW + 86400)).ok


async def test_craft_order_is_player_transfer_and_bought_supply_changes_city(bazaar):
    e = bazaar
    buyer, maker, other = e.heroes
    await e.inventory.add(maker.id, "iron_scrap", 2)
    order = await e.service.order(buyer.id, "smith_ingot@1", 101, NOW)
    assert not (await e.service.fulfill(buyer.id, order.id, 101, NOW)).ok
    assert not (await e.service.fulfill(maker.id, order.id, 102, NOW)).ok
    results = await asyncio.gather(
        *(e.service.fulfill(hero.id, order.id, 101, NOW) for hero in (maker, other))
    )
    assert sum(one.ok for one in results) == 1
    assert (await e.characters.get(maker.id)).gold == 596
    assert (await e.characters.get(buyer.id)).gold == 399
    assert await e.inventory.count(maker.id, "iron_scrap") == 0
    assert await e.inventory.count(buyer.id, "crude_ingot") >= 1
    city = CityEvents(e.service.content, e.cache, e.characters, e.inventory)
    answer = await city.supply(buyer.id, "farhold", 0, "order-supply")
    assert "Передано городу" in answer
    assert len((await city.load(city.event("farhold"))).contributions) == 1
    assert "уже" in await city.supply(buyer.id, "farhold", 0, "order-supply")
    rows = supply_and_demand(await e.service.load(), {"smith_ingot@1": "crude_ingot"})
    assert rows[0][1:3] == (0, 0) and rows[0][3] >= 1


async def test_orders_cancel_expire_and_invalid_inputs(bazaar):
    e = bazaar
    buyer, other, _ = e.heroes
    assert not (await e.service.order(buyer.id, "unknown", 10, NOW)).ok
    assert not (await e.service.order(buyer.id, "smith_ingot@1", 0, NOW)).ok
    assert not (await e.service.order(buyer.id, "smith_ingot@1", 501, NOW)).ok
    order = await e.service.order(buyer.id, "smith_ingot@1", 101, NOW)
    assert not (await e.service.cancel_order(other.id, order.id, NOW)).ok
    assert (await e.service.cancel_order(buyer.id, order.id, NOW)).ok
    assert not (await e.service.cancel_order(buyer.id, order.id, NOW)).ok
    await e.service.order(buyer.id, "smith_ingot@1", 101, NOW)
    assert await e.service.sweep(NOW + 604800) == 1
    assert (await e.characters.get(buyer.id)).gold == 500
    e.service.content = replace(
        e.service.content, market_rules=replace(e.service.content.market_rules, open_limit=1)
    )
    await e.service.order(buyer.id, "smith_ingot@1", 101, NOW + 604800)
    assert not (await e.service.order(buyer.id, "smith_ingot@1", 101, NOW + 604800)).ok


async def test_publish_and_buy_rollback_and_saved_result(bazaar):
    e = bazaar
    seller, buyer, _ = e.heroes
    await e.inventory.add(seller.id, "iron_scrap", 2)
    with pytest.raises(RuntimeError):
        await act(e.service, seller.id, "sell iron_scrap break", operation_id="sell-break")
    assert not (await e.service.load()).lots
    assert await e.inventory.count(seller.id, "iron_scrap") == 2
    lot = await act(e.service, seller.id, "sell iron_scrap", operation_id="sell-ok")
    assert await act(e.service, seller.id, "sell iron_scrap", operation_id="sell-ok") == lot
    with pytest.raises(RuntimeError):
        await act(e.service, buyer.id, f"buy {lot.id} break", operation_id="buy-break")
    assert (await e.characters.get(buyer.id)).gold == 500
    assert (await e.service.load()).lots[0].status == "open"
    result = await act(e.service, buyer.id, f"buy {lot.id}", operation_id="buy-ok")
    assert await act(e.service, buyer.id, f"buy {lot.id}", operation_id="buy-ok") == result
