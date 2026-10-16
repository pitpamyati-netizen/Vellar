"""Настоящая запись стопки и очков: откат, повтор и одновременная покупка."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from mmorpg.application.operations import atomic_action
from mmorpg.presentation.telegram.flows import play
from mmorpg.presentation.telegram.flows.state import Goods, PlayState
from mmorpg.presentation.telegram.handlers.play import _apply
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.integration.test_economic_operations import economy as _economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
POTION = "small_healing_potion"
economy = _economy


@atomic_action
async def purchase(characters, inventory, users, content, character_id, operation_id: str) -> str:
    character = await characters.get(character_id)
    goods = Goods(gold=character.gold, stock=(content.item(POTION),), prices={POTION: 7})
    state = replace(
        PlayState().at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM),
        item_id=POTION,
        shop_price=7,
        shop_quantity=50,
    )
    bought = play.advance(content, character, state, "/купить", world_seed="bulk", goods=goods)
    await _apply(bought.pending, character, character.user_id, characters, inventory, users)
    return bought.notice


async def test_bulk_purchase_is_saved_once_even_without_redis(economy, content):
    e = economy
    operation_id = str(uuid4())
    args = (e.characters, e.inventory, e.users, content, e.heroes[0].id, operation_id)
    first = await purchase(*args)
    assert "Куплено:" in first
    await e.redis.flushdb()
    assert await purchase(*args) == first
    saved = await e.characters.get(e.heroes[0].id)
    assert saved.gold == 150
    assert await e.inventory.count(saved.id, POTION) == 50


async def test_bulk_purchase_rolls_back_items_if_character_write_fails(
    economy, content, monkeypatch
):
    e = economy
    save = e.characters.save

    async def failed(character):
        raise RuntimeError("bulk controlled failure")

    monkeypatch.setattr(e.characters, "save", failed)
    operation_id = str(uuid4())
    args = (e.characters, e.inventory, e.users, content, e.heroes[0].id, operation_id)
    with pytest.raises(RuntimeError, match="bulk controlled failure"):
        await purchase(*args)
    assert await e.inventory.count(e.heroes[0].id, POTION) == 0
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    monkeypatch.setattr(e.characters, "save", save)
    assert "Куплено:" in await purchase(*args)
    assert await e.inventory.count(e.heroes[0].id, POTION) == 50
    assert (await e.characters.get(e.heroes[0].id)).gold == 150


async def test_two_bulk_purchases_cannot_spend_the_same_gold(economy, content):
    e = economy
    results = await asyncio.gather(
        *(
            purchase(e.characters, e.inventory, e.users, content, e.heroes[0].id, str(uuid4()))
            for _ in range(2)
        )
    )
    assert sum("Куплено:" in result for result in results) == 1
    assert sum("Не хватает" in result for result in results) == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 150
    assert await e.inventory.count(e.heroes[0].id, POTION) == 50


@atomic_action
async def spend(characters, inventory, users, content, character_id, operation_id: str) -> str:
    character = await characters.get(character_id)
    state = replace(PlayState().at(ScreenId.STATS), stat_amount=100)
    spent = play.advance(content, character, state, "Вложить 100: Сила", world_seed="bulk")
    await _apply(spent.pending, character, character.user_id, characters, inventory, users)
    return spent.notice


async def test_stat_batch_is_saved_once_and_old_button_cannot_spend_again(economy, content):
    from mmorpg.domain.entities import StatCode

    e = economy
    character = await e.characters.get(e.heroes[0].id)
    await e.characters.save(replace(character, unspent_stat_points=100))
    args = (e.characters, e.inventory, e.users, content, character.id)
    operation_id = str(uuid4())
    first = await spend(*args, operation_id)
    await e.redis.flushdb()
    assert await spend(*args, operation_id) == first
    assert await spend(*args, str(uuid4())) != first
    saved = await e.characters.get(character.id)
    assert saved.allocated[StatCode.STR] == 100
    assert saved.unspent_stat_points == 0
