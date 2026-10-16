"""Полный путь площади, возвраты, сохранённая лавка и устаревший товар."""

import json
from dataclasses import replace

import pytest

from mmorpg.domain.rules.economy import roll_assortment
from mmorpg.presentation.telegram.flows import play
from mmorpg.presentation.telegram.flows.state import Goods, LocationSession, PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import ListFilters, PageState


def step(content, hero, state, text, goods=None):
    return play.advance(content, hero, state, text, world_seed="square", goods=goods)


@pytest.mark.parametrize("number", range(1, 8))
def test_shop_route_purchase_return_and_saved_selection(content, hero, number):
    hero = replace(hero, gold=100000, tutorial=0b111111)
    state = PlayState(city_id=hero.city_id).at(ScreenId.CITY)
    square = step(content, hero, state, "Торговая площадь")
    assert square.screen is ScreenId.TRADE_SQUARE
    goods = roll_assortment(
        content, world_seed="square", city_id=hero.city_id, rotation=0, character_level=hero.level
    )
    shelf = Goods(gold=hero.gold, stock=goods, prices={i.id: 7 for i in goods})
    shop = content.shops[number - 1]
    opened = step(content, hero, square, f"/лавка {number}", shelf)
    assert opened.screen is ScreenId.SHOP and opened.shop_id == shop.id
    restored = PlayState.deserialise(opened.serialise())
    screen = play.render(content, hero, restored, world_seed="square", goods=shelf)
    assert shop.name in screen.text()
    chosen = next(b.text for row in screen.rows for b in row if b.text.endswith("купить"))
    card = step(content, hero, restored, chosen, shelf)
    assert card.screen is ScreenId.SHOP_ITEM and card.pending.empty
    returned = step(content, hero, card, "/назад", shelf)
    assert returned.screen is ScreenId.SHOP and returned.shop_id == shop.id
    card = step(content, hero, returned, chosen, shelf)
    bought = step(content, hero, card, "/купить", shelf)
    assert bought.pending.items == ((card.item_id, 1),)
    assert bought.pending.character.gold == hero.gold - 7
    square = step(content, hero, returned, "/назад", shelf)
    assert square.screen is ScreenId.TRADE_SQUARE
    assert step(content, hero, square, "/назад").screen is ScreenId.CITY


def test_new_shop_clears_search_filters_quantity_and_previous_card(content, hero):
    state = PlayState(
        city_id=hero.city_id,
        shop_id="weapons",
        item_id="sword@1#common",
        shop_price=7,
        shop_quantity=100,
        list_page=PageState(page=9, filters=ListFilters(query="меч")),
    )
    square = step(content, hero, state, "/площадь")
    opened = step(content, hero, square, "Лавка алхимика")
    assert opened.shop_id == "alchemy" and not opened.item_id
    assert opened.shop_price is None and opened.shop_quantity == 1
    assert opened.list_page == PageState() and not opened.searching


@pytest.mark.parametrize("old_screen", [ScreenId.SHOP, ScreenId.SHOP_ITEM])
@pytest.mark.parametrize("shop_id", ["", "removed"])
def test_old_shop_saves_and_deleted_shops_return_to_square_without_payment(
    content, hero, old_screen, shop_id
):
    state = PlayState(
        screen=old_screen,
        city_id=hero.city_id,
        shop_id=shop_id,
        item_id="sword@1#common",
        shop_price=7,
    )
    data = json.loads(state.serialise())
    if not shop_id:
        data.pop("shop_id")
    restored = PlayState.deserialise(json.dumps(data))
    result = step(content, hero, restored, "/купить")
    assert result.screen is ScreenId.TRADE_SQUARE and result.pending.empty
    assert not result.item_id


def test_another_merchants_item_cannot_be_bought_from_a_stale_card(content, hero):
    item = content.item("sword@1#common")
    goods = Goods(gold=10000, stock=(item,), prices={item.id: 7})
    state = PlayState(shop_id="alchemy", item_id=item.id, shop_price=7)
    state = state.at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM)
    result = step(content, hero, state, "/купить", goods)
    assert result.pending.empty and "больше нет" in result.notice


def test_sell_screen_and_invalid_shop_number_have_a_way_back(content, hero):
    state = PlayState(city_id=hero.city_id).at(ScreenId.CITY)
    square = step(content, hero, state, "/лавка")
    for number in (0, 8, 100):
        rejected = step(content, hero, square, f"/лавка {number}")
        assert rejected.screen is ScreenId.TRADE_SQUARE and rejected.pending.empty
    selling = step(content, hero, square, "/продать")
    assert selling.screen is ScreenId.SELL
    assert step(content, hero, selling, "/назад").screen is ScreenId.TRADE_SQUARE


@pytest.mark.parametrize("screen", [ScreenId.COMBAT, ScreenId.COMBAT_BAG, ScreenId.LOCATION])
def test_square_cannot_be_opened_during_battle_or_outside_city(content, hero, screen):
    state = PlayState(screen=screen, session=LocationSession(city_id="farhold", slot=1))
    refused = step(content, hero, state, "/площадь")
    assert refused.screen is screen and refused.pending.empty


def test_square_command_works_after_returning_to_city_from_an_expedition(content, hero):
    outside = PlayState(
        screen=ScreenId.LOCATION, session=LocationSession(city_id="farhold", slot=1)
    )
    city = step(content, hero, outside, "/город")
    assert city.screen is ScreenId.CITY
    command = step(content, hero, city, "/площадь")
    button = step(content, hero, city, "Торговая площадь")
    assert command.screen is button.screen is ScreenId.TRADE_SQUARE
    shop = step(content, hero, command, "/лавка 4")
    assert step(content, hero, shop, "/площадь").screen is ScreenId.TRADE_SQUARE
