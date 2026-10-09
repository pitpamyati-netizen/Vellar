"""Настоящий обработчик: кнопки, подтверждение, старый выбор и выход."""

from dataclasses import replace

from mmorpg.application.services.market import Market
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.presentation.test_party_flow import buttons
from tests.presentation.test_party_flow import cache as cache
from tests.presentation.test_party_flow import characters as characters
from tests.presentation.test_party_flow import guilds as guilds
from tests.presentation.test_party_flow import parties as parties
from tests.presentation.test_party_flow import registry as registry
from tests.presentation.test_party_flow import sent as sent
from tests.presentation.test_party_flow import table as table


async def prepare(player, hero):
    await player.deps["characters"].save(replace(hero, gold=500))
    return Market(
        player.deps["registry"].current,
        player.deps["cache"],
        player.deps["characters"],
        player.deps["inventory"],
    )


async def test_sell_buy_confirm_buttons_back_and_commands(table):
    player, other, hero, guest = table
    await prepare(player, hero)
    await prepare(other, guest)
    await player.deps["inventory"].add(hero.id, "iron_scrap", 2)
    assert (await player.press("Рынок")).id is ScreenId.MARKET
    bag = await player.press("Продать вещь на рынке")
    assert "Вещь 1" in bag.body()
    await player.press(next(one for one in buttons(bag) if one.startswith("Продать вещь 1:")))
    await player.press("/рынок количество 2")
    preview = await player.press("/рынок цена 101")
    assert "Цена всей партии: 101" in preview.body() and "Пошлина" in preview.body()
    assert await player.deps["inventory"].count(hero.id, "iron_scrap") == 2
    assert "опубликован" in (await player.press("Подтвердить сделку")).body()
    await other.press("/рынок лот 1")
    preview = await other.press("Купить этот лот")
    assert "101 золота" in preview.body()
    assert (await other.deps["characters"].get(guest.id)).gold == 500
    answer = await other.press("/рынок подтвердить")
    assert "продавцу: 96" in answer.body()
    assert await other.deps["inventory"].count(guest.id, "iron_scrap") == 2
    assert "не распознан" in (await other.press("Подтвердить сделку")).body()
    assert (await other.press("Главное меню")).id is ScreenId.MAIN_MENU
    await player.press("/рынок")
    assert (await player.press("/дорога")).id is ScreenId.WORLD


async def test_order_supply_report_and_stale_lot(table):
    player, other, hero, guest = table
    service = await prepare(player, hero)
    await prepare(other, guest)
    await other.deps["inventory"].add(guest.id, "iron_scrap", 2)
    await player.press("/рынок заказ 101")
    assert "Заказ 1 открыт" in (await player.press("/рынок подтвердить")).body()
    card = await other.press("/рынок заказ-номер 1")
    assert "Материалы мастера" in card.body()
    await other.press("Изготовить по заказу")
    assert "изготовлен" in (await other.press("Подтвердить сделку")).body()
    report = await player.press("/рынок отчёт")
    assert "спрос 0" in report.body() and "куплено" in report.body()
    await player.press("/событие")
    assert "Передано городу" in (await player.press("/событие снабдить")).body()
    await player.deps["inventory"].add(hero.id, "iron_scrap", 2)
    lot = await service.publish(hero.id, "iron_scrap", 2, 101, 10000000000)
    await other.press(f"/рынок купить {lot.id}")
    await service.cancel(hero.id, lot.id, 10000000000)
    assert "уже закрыт" in (await other.press("Подтвердить сделку")).body()


async def test_bad_input_pages_restore_state_and_single_instances(table):
    player, _other, hero, _guest = table
    await prepare(player, hero)
    assert "не распознан" in (await player.press("/рынок цена 2")).body()
    assert "Страница 1 из 1" in (await player.press("/рынок дальше")).body()
    await player.press("/рынок сумка")
    assert "не распознан" in (await player.press("/рынок вещь 999")).body()
    await player.press("/рынок заказ")
    assert "не распознан" in (await player.press("-2")).body()
    preview = await player.press("101")
    assert "Подтверждение" in preview.body()
    flow = PlayState.deserialise((await player.state.get_data())["play"])
    assert PlayState.deserialise(flow.serialise()).market_price == 101
    assert preview.fits_message_limit()
    assert (await player.press("Отменить выбор")).id is ScreenId.MARKET
