"""Menu, world, city and location navigation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.presentation.telegram.flows.play import (
    PlayState,
    advance,
    begin,
    build_location,
    render,
)
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CYCLE = 100


@pytest.fixture
def hero(content: GameContent) -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="dwarf", class_id="warrior", level=3)


def step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, hero, current, message, cycle=CYCLE, world_seed=WORLD_SEED)
    return current


@pytest.fixture
def menu(hero: Character) -> PlayState:
    return begin(hero)


@pytest.fixture
def in_city(content: GameContent, hero: Character, menu: PlayState) -> PlayState:
    return step(content, hero, menu, "Мир", "Дубно")


@pytest.fixture
def in_location(content: GameContent, hero: Character, in_city: PlayState) -> PlayState:
    return step(content, hero, in_city, "Локации", "1. Луга у Заставы")


# --- menu and world ---------------------------------------------------


def test_main_menu_states_where_you_are(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    text = render(content, hero, menu, world_seed=WORLD_SEED).text()
    assert text.startswith("Главное меню. Вы в городе Дубно.")
    assert "уровень 3" in text
    assert "Здоровье:" in text


def test_world_lists_only_unlocked_cities(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    world = step(content, hero, menu, "Мир")
    text = render(content, hero, world, world_seed=WORLD_SEED).text()
    assert "Дубно" in text
    assert "Закрыто городов: 14" in text


def test_a_locked_city_explains_itself(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    world = step(content, hero, menu, "Мир")
    blocked = step(content, hero, world, "Мглин")
    assert blocked.screen is ScreenId.WORLD
    assert "откроется на уровне" in blocked.notice


def test_entering_a_city(content: GameContent, hero: Character, in_city: PlayState) -> None:
    assert in_city.screen is ScreenId.CITY
    assert in_city.city_id == "farhold"
    assert "Дубно" in render(content, hero, in_city, world_seed=WORLD_SEED).text()


# --- shop and inventory -----------------------------------------------


def test_shop_and_inventory_are_real_screens(
    content: GameContent, hero: Character, in_city: PlayState, menu: PlayState
) -> None:
    from mmorpg.domain.rules.economy import buy_price, roll_assortment
    from mmorpg.presentation.telegram.flows.play import Goods
    from mmorpg.presentation.telegram.screens.shop import OwnedItem

    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", cycle=CYCLE, character_level=hero.level
    )
    goods = Goods(
        gold=500,
        owned=(OwnedItem("small_healing_potion", 2),),
        stock=stock,
        prices={item.id: buy_price(content, item) for item in stock},
    )

    shop = advance(content, hero, in_city, "Лавка", cycle=CYCLE, world_seed=WORLD_SEED, goods=goods)
    assert shop.screen is ScreenId.SHOP
    assert (
        "Лавка города Дубно"
        in render(content, hero, shop, world_seed=WORLD_SEED, goods=goods).text()
    )

    inventory = advance(
        content, hero, menu, "Инвентарь", cycle=CYCLE, world_seed=WORLD_SEED, goods=goods
    )
    assert inventory.screen is ScreenId.INVENTORY
    assert (
        "Малое зелье лечения"
        in render(content, hero, inventory, world_seed=WORLD_SEED, goods=goods).text()
    )


def test_buying_defers_the_write_to_the_handler(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """The flow stays pure: it records the intent, the handler performs it."""
    from mmorpg.domain.rules.economy import buy_price, roll_assortment
    from mmorpg.presentation.telegram.flows.play import Goods
    from mmorpg.presentation.telegram.screens.shop import buy_label

    wealthy = replace(hero, gold=100_000)
    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", cycle=CYCLE, character_level=hero.level
    )
    prices = {item.id: buy_price(content, item) for item in stock}
    rich = Goods(gold=wealthy.gold, stock=stock, prices=prices)
    poor = Goods(gold=0, stock=stock, prices=prices)

    shop = advance(
        content, wealthy, in_city, "Лавка", cycle=CYCLE, world_seed=WORLD_SEED, goods=rich
    )
    first = stock[0]
    pressed = buy_label(first, prices[first.id]).text

    bought = advance(
        content, wealthy, shop, pressed, cycle=CYCLE, world_seed=WORLD_SEED, goods=rich
    )
    assert bought.pending.items == ((first.id, 1),)
    assert bought.pending.character is not None
    assert bought.pending.character.gold == wealthy.gold - prices[first.id]
    assert "куплен" in bought.notice

    broke = advance(content, hero, shop, pressed, cycle=CYCLE, world_seed=WORLD_SEED, goods=poor)
    assert broke.pending.empty
    assert "Не хватает" in broke.notice


@pytest.mark.parametrize(
    ("section", "screen"),
    [
        ("Данжи", ScreenId.DUNGEON),
        ("Таверна", ScreenId.TAVERN),
        ("Наставник", ScreenId.MENTOR),
        ("Банк", ScreenId.BANK),
    ],
)
def test_every_city_service_is_a_real_screen(
    content: GameContent,
    hero: Character,
    in_city: PlayState,
    section: str,
    screen: ScreenId,
) -> None:
    """No stubs left in a city: each service answers and comes back (Roadmap 1.5)."""
    opened = step(content, hero, in_city, section)
    assert opened.screen is screen
    rendered = render(content, hero, opened, world_seed=WORLD_SEED)
    assert rendered.lines[0].strip()
    assert rendered.all_rows()[-1][0].text == "Назад"
    assert step(content, hero, opened, "Назад").screen is ScreenId.CITY


# --- locations --------------------------------------------------------


def test_location_list_shows_level_bands(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    listed = step(content, hero, in_city, "Локации")
    text = render(content, hero, listed, world_seed=WORLD_SEED).text()
    assert "Луга у Заставы" in text
    assert "уровни с 1 по 4" in text


def test_entering_a_location_starts_at_the_entrance(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    assert in_location.screen is ScreenId.LOCATION
    assert in_location.session.active
    assert in_location.session.node == 0
    assert in_location.session.cycle == CYCLE
    text = render(content, hero, in_location, world_seed=WORLD_SEED).text()
    assert text.startswith("Локация Луга у Заставы, узел 0: Вход.")


def test_a_location_above_your_level_is_refused(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    listed = step(content, hero, in_city, "Локации")
    blocked = step(content, hero, listed, "5. Выработки")
    assert blocked.screen is ScreenId.LOCATION_LIST
    assert "рассчитана на уровни" in blocked.notice


def test_moving_between_nodes(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    location = build_location(content, WORLD_SEED, in_location.session)
    neighbour = location.node(location.entrance.links[0])
    moved = step(content, hero, in_location, f"Узел {neighbour.index}: {neighbour.name}")
    assert moved.session.node == neighbour.index


def test_every_node_is_reachable_from_the_entrance(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    """Walking the graph by button presses must reach the exit."""
    location = build_location(content, WORLD_SEED, in_location.session)
    seen = {0}
    frontier = [0]
    while frontier:
        current = frontier.pop()
        for link in location.node(current).links:
            if link not in seen:
                seen.add(link)
                frontier.append(link)
    assert len(seen) == len(location.nodes)
    assert location.exit_node.index in seen


def test_clearing_a_node_is_remembered_for_the_cycle(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    location = build_location(content, WORLD_SEED, in_location.session)
    gather = next((node for node in location.nodes if node.kind.value == "gather"), None)
    if gather is None:
        pytest.skip("this seed produced no gathering node")

    at_node = replace(in_location, session=replace(in_location.session, node=gather.index))
    done = step(content, hero, at_node, "Собрать ресурсы")
    assert done.session.cleared != 0
    again = step(content, hero, done, "Собрать ресурсы")
    assert "уже пройден" in again.notice


def test_leaving_a_location_clears_the_session(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    left = step(content, hero, in_location, "Покинуть локацию")
    assert left.screen is ScreenId.LOCATION_LIST
    assert left.session.active is False


def test_the_cycle_is_captured_on_entry(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    """The map must not change under a player who is standing in it."""
    before = render(content, hero, in_location, world_seed=WORLD_SEED).text()
    later = advance(
        content, hero, in_location, "Осмотреться", cycle=CYCLE + 5, world_seed=WORLD_SEED
    )
    assert render(content, hero, later, world_seed=WORLD_SEED).text() == before


def test_a_combat_node_hands_over_to_the_fight_screen(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    location = build_location(content, WORLD_SEED, in_location.session)
    battle = next(node for node in location.nodes if node.kind.is_combat)
    at_node = replace(in_location, session=replace(in_location.session, node=battle.index))
    from mmorpg.presentation.telegram.screens.play import NODE_ACTIONS

    fighting = step(content, hero, at_node, NODE_ACTIONS[battle.kind])
    assert fighting.screen is ScreenId.COMBAT


# --- navigation -------------------------------------------------------


def test_back_walks_the_stack(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    assert step(content, hero, in_location, "Назад").screen is ScreenId.LOCATION_LIST
    assert step(content, hero, in_location, "Назад", "Назад").screen is ScreenId.CITY
    assert step(content, hero, in_location, "Назад", "Назад", "Назад").screen is ScreenId.WORLD


def test_main_menu_resets_the_stack(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    home = step(content, hero, in_location, "Главное меню")
    assert home.screen is ScreenId.MAIN_MENU
    assert home.stack.screens == (ScreenId.MAIN_MENU,)


def test_look_changes_nothing(content: GameContent, hero: Character, in_city: PlayState) -> None:
    looked = step(content, hero, in_city, "Осмотреться")
    assert looked.screen is in_city.screen
    assert looked.session == in_city.session


def test_unknown_input_is_answered(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    answered = step(content, hero, in_city, "Вихрь клинков")
    assert answered.screen is in_city.screen
    assert answered.notice


def test_state_survives_a_round_trip(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    restored = PlayState.deserialise(in_location.serialise())
    assert restored.screen is in_location.screen
    assert restored.session == in_location.session
    assert restored.stack == in_location.stack


# --- state that came back from an older release -----------------------
#
# Storage outlives content and outlives the code that wrote it. A screen that
# names something the game can no longer build must land the player somewhere
# real: a crash here is answered with an apology on *every* press afterwards,
# because the state that caused it is read again each time.

# Written by a release that stored the location screen without the visit behind
# it. This exact document was found in Redis, and every press against it raised.
STALE_LOCATION = (
    '{"screen": "location", "stack": "main_menu,world,city,location_list,location",'
    ' "world_page": 1, "location_page": 1, "city": "farhold",'
    ' "session": ["", 0, 0, 0, 0], "descent": ["", 0, 0, 0], "stub": "",'
    ' "pick": ["", 0], "edge": "", "quest": "", "pages": [1, 1, 1]}'
)


def test_a_location_screen_without_a_visit_lands_on_the_location_list(
    content: GameContent, hero: Character
) -> None:
    stale = PlayState.deserialise(STALE_LOCATION)
    screen = render(content, hero, stale, world_seed=WORLD_SEED)

    assert screen.id is ScreenId.LOCATION_LIST
    assert "Та вылазка уже закончилась." in screen.text()


def test_a_step_on_a_lost_visit_answers_instead_of_raising(
    content: GameContent, hero: Character
) -> None:
    stale = PlayState.deserialise(STALE_LOCATION)
    moved = step(content, hero, stale, "Осмотреться")

    assert moved.screen is ScreenId.LOCATION_LIST
    assert not moved.session.active


def test_a_city_content_no_longer_has_falls_back_to_the_players_own(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    lost = replace(menu, city_id="a_city_that_burned_down", screen=ScreenId.CITY)
    screen = render(content, hero, lost, world_seed=WORLD_SEED)

    assert content.city(hero.city_id).name in screen.text()


def test_a_skill_edge_screen_for_an_unknown_skill_falls_back_to_the_list(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    lost = replace(menu, edge_skill="skill_that_was_renamed", screen=ScreenId.SKILL_EDGE)
    assert render(content, hero, lost, world_seed=WORLD_SEED).id is ScreenId.SKILLS


def test_a_quest_offer_for_an_unknown_contract_falls_back_to_the_board(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    lost = replace(menu, quest_id="quest_that_was_cut", screen=ScreenId.QUEST_OFFER)
    assert render(content, hero, lost, world_seed=WORLD_SEED).id is ScreenId.QUEST_BOARD


# --- the introduction -------------------------------------------------


def test_the_introduction_is_offered_until_it_is_done(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    """A played character does not need a tutorial button for ever."""
    fresh = render(content, hero, menu, world_seed=WORLD_SEED)
    assert "Обучение" in [item.text for row in fresh.rows for item in row]

    from mmorpg.domain.rules import tutorial as tutorial_rules

    taught = replace(hero, tutorial=0b111111)
    assert tutorial_rules.finished(taught)
    done = render(content, taught, begin(taught), world_seed=WORLD_SEED)
    assert "Обучение" not in [item.text for row in done.rows for item in row]


def test_a_task_button_walks_the_player_to_the_screen(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    """The point of the button: no route to memorise, no menu to guess."""
    opened = step(content, hero, menu, "Обучение")
    assert opened.screen is ScreenId.TUTORIAL

    walked = step(content, hero, opened, "Выполнить задание")
    assert walked.screen is ScreenId.STATS
    # Reading them is the task, so it is already ticked off.
    assert walked.pending.character is not None
    assert walked.pending.character.tutorial != hero.tutorial


def test_a_task_done_by_playing_counts_too(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    """Nobody has to open the introduction for it to notice what they did."""
    from mmorpg.domain.rules import tutorial as tutorial_rules
    from mmorpg.domain.rules.tutorial import TutorialTask

    stats = step(content, hero, menu, "Персонаж", "Характеристики")
    assert stats.pending.character is not None
    assert tutorial_rules.is_done(stats.pending.character, TutorialTask.STATS)
    assert "Задание обучения сделано" in stats.notice


def test_a_finished_introduction_says_so_and_offers_nothing(
    content: GameContent, hero: Character
) -> None:
    taught = replace(hero, tutorial=0b111111)
    # The button is gone from the menu, but the screen still answers whoever
    # arrives on it from an older keyboard (accessibility rule 12).
    standing = begin(taught).at(ScreenId.TUTORIAL)
    text = render(content, taught, standing, world_seed=WORLD_SEED).text()
    assert "Все задания сделаны" in text

    pressed = step(content, taught, standing, "Выполнить задание")
    assert pressed.pending.empty
