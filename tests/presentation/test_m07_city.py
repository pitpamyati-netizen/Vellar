"""Настоящий маршрут личной команды с заменённой отправкой Telegram."""

from dataclasses import replace

import pytest

from mmorpg.application.services.city_event import CityEvents
from mmorpg.domain.rules.economy import travel_price
from mmorpg.presentation.telegram.flows.play import advance, begin, render
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens import city_event as ui
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.presentation.test_party_flow import buttons
from tests.presentation.test_party_flow import cache as cache
from tests.presentation.test_party_flow import characters as characters
from tests.presentation.test_party_flow import guilds as guilds
from tests.presentation.test_party_flow import parties as parties
from tests.presentation.test_party_flow import registry as registry
from tests.presentation.test_party_flow import sent as sent
from tests.presentation.test_party_flow import table as table


def service(player):
    return CityEvents(
        player.deps["registry"].current,
        player.deps["cache"],
        player.deps["characters"],
        player.deps["inventory"],
    )


async def test_buttons_commands_back_menu_and_stale_craft(table):
    player, _other, hero, guest = table
    await player.press(labels.WORLD.text)
    city = await player.press(player.deps["registry"].current.city(hero.city_id).name)
    assert ui.OPEN.text in buttons(city)
    assert "Общее дело" in city.body()
    screen = await player.press(ui.OPEN.text)
    assert screen.id is ScreenId.CITY_EVENT
    assert "0 из 4" in screen.body()
    assert screen.all_rows()[-1] == labels.SERVICE_ROW
    assert (await player.press("Назад")).id is ScreenId.CITY
    await player.press("/событие")
    assert (await player.press("Главное меню")).id is ScreenId.MAIN_MENU
    await player.press("/событие")
    assert (await player.press("/дорога")).id is ScreenId.WORLD
    await player.press("/событие")
    assert (await player.press(ui.BATTLE.text)).id is ScreenId.LOCATION
    await player.press("/событие")
    assert (await player.press("/событие разведка")).id is ScreenId.LOCATION
    await player.press("/событие")
    events = service(player)
    for person in (hero, guest):
        for index in range(2):
            await events.record(person.id, hero.city_id, 1, "battle", f"a:{person.id}:{index}")
    await player.deps["inventory"].add(hero.id, "iron_scrap", 4)
    answer = await player.press("/событие ремесло")
    assert "изменилось" in answer.body()
    assert await player.deps["inventory"].count(hero.id, "iron_scrap") == 4
    assert "1 из 2" not in answer.body()
    assert "Этап 2 из 2" in answer.body()
    assert "передано городу" in (await player.press(ui.CRAFT.text)).body()


async def test_summary_is_shared_claim_is_bounded_and_remote_help_does_not_teleport(table):
    player, other, hero, guest = table
    events = service(player)
    for person in (hero, guest):
        for index in range(2):
            await events.record(person.id, hero.city_id, 1, "battle", f"a:{person.id}:{index}")
    assert "Этап 2 из 2" in (await other.press("/событие")).body()
    before = (await player.deps["characters"].get(hero.id)).gold
    assert "20 золота" in (await player.press("/событие награда")).body()
    await player.press("/событие награда")
    assert (await player.deps["characters"].get(hero.id)).gold == before + 20
    remote = await player.deps["characters"].save(
        replace(await player.deps["characters"].get(hero.id), city_id="millhaven")
    )
    reply = await player.press("/событие бой")
    assert "приезжайте" in reply.body()
    assert (await player.deps["characters"].get(hero.id)).city_id == remote.city_id


async def test_real_search_records_shared_progress_and_refusal_does_not(table):
    from mmorpg.domain.entities.location import NodeKind
    from mmorpg.presentation.telegram.flows.play import build_location
    from mmorpg.presentation.telegram.screens import play as play_ui
    from tests.presentation.test_party_flow import SETTINGS

    player, _other, hero, _guest = table
    await player.press("/событие разведка")
    content = player.deps["registry"].current
    flow = PlayState.deserialise((await player.state.get_data())["play"])
    location = build_location(content, SETTINGS.world_seed, flow.session, epoch=flow.session.epoch)
    node = next(
        one
        for one in location.nodes
        if one.kind in {NodeKind.CACHE, NodeKind.SHRINE, NodeKind.EVENT}
    )
    # Движение здесь подготовлено; действие и расход узла проходят обычный обработчик.
    flow = replace(flow, session=replace(flow.session, node=node.index))
    await player.state.update_data({"play": flow.serialise()})
    screen = await player.press("/осмотреться")
    action = play_ui.NODE_ACTIONS[node.kind]
    button = next(one for one in buttons(screen) if one.startswith(action))
    screen = await player.press(button)
    assert "вклад сохранён" in screen.body()
    events = service(player)
    assert len((await events.load(content.city_events[0])).contributions) == 1
    # Простое чтение и переход в другое место вкладом не считаются.
    await player.press("/осмотреться")
    assert len((await events.load(content.city_events[0])).contributions) == 1
    assert not await events.record(hero.id, "farhold", 3, "scout", "wrong-place")


@pytest.mark.parametrize("discount", [0, 10, 15, 20])
def test_old_road_uses_current_city_for_shown_price_and_payment(content, discount):
    from mmorpg.domain.entities import Character

    hero = Character(
        id=1,
        user_id=1,
        name="Путник",
        race_id="human",
        class_id="warrior",
        city_id=content.cities[1].id,
        level=40,
        gold=900,
    )
    state = replace(begin(hero).at(ScreenId.WORLD), city_id=content.cities[0].id)
    target = content.cities[2]
    fare = (
        travel_price(hero.level, target.order - content.cities[1].order) * (100 - discount) // 100
    )
    screen = render(content, hero, state, world_seed="test", travel_discount=discount)
    assert f"дорога {fare}" in screen.body()
    updated = advance(
        content, hero, state, target.name, world_seed="test", travel_discount=discount
    )
    assert updated.pending.character.gold == hero.gold - fare
    assert updated.pending.character.city_id == target.id


def test_discounted_price_matches_screen_and_keeps_ordinary_routes(content):
    from mmorpg.domain.entities import Character

    hero = Character(
        id=1,
        user_id=1,
        name="Путник",
        race_id="human",
        class_id="warrior",
        city_id="farhold",
        level=20,
        gold=900,
    )
    state = begin(hero).at(ScreenId.WORLD)
    target = content.cities[1]
    fare = travel_price(hero.level, target.order - content.cities[0].order) * 80 // 100
    screen = render(content, hero, state, world_seed="test", travel_discount=20)
    assert f"дорога {fare}" in screen.body()
    updated = advance(content, hero, state, target.name, world_seed="test", travel_discount=20)
    assert updated.pending.character.gold == hero.gold - fare
    assert updated.screen is ScreenId.CITY
    assert (
        PlayState.deserialise(replace(state, city_event_stage=1).serialise()).city_event_stage == 1
    )
