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
    return step(content, hero, menu, "Мир", "Дальний Оплот")


@pytest.fixture
def in_location(content: GameContent, hero: Character, in_city: PlayState) -> PlayState:
    return step(content, hero, in_city, "Локации", "1. Тихие Луга")


# --- menu and world ---------------------------------------------------


def test_main_menu_states_where_you_are(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    text = render(content, hero, menu, world_seed=WORLD_SEED).text()
    assert text.startswith("Главное меню. Вы в городе Дальний Оплот.")
    assert "уровень 3" in text
    assert "Здоровье:" in text


def test_world_lists_only_unlocked_cities(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    world = step(content, hero, menu, "Мир")
    text = render(content, hero, world, world_seed=WORLD_SEED).text()
    assert "Дальний Оплот" in text
    assert "Закрыто городов: 14" in text


def test_a_locked_city_explains_itself(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    world = step(content, hero, menu, "Мир")
    blocked = step(content, hero, world, "Костяной Предел")
    assert blocked.screen is ScreenId.WORLD
    assert "откроется на уровне" in blocked.notice


def test_entering_a_city(content: GameContent, hero: Character, in_city: PlayState) -> None:
    assert in_city.screen is ScreenId.CITY
    assert in_city.city_id == "farhold"
    assert "Дальний Оплот" in render(content, hero, in_city, world_seed=WORLD_SEED).text()


# --- stubs ------------------------------------------------------------


@pytest.mark.parametrize("section", ["Данжи", "Таверна", "Наставник", "Банк"])
def test_unfinished_sections_answer_with_a_working_back(
    content: GameContent, hero: Character, in_city: PlayState, section: str
) -> None:
    """A stub is a real screen with a working Назад, never silence."""
    stub = step(content, hero, in_city, section)
    assert stub.screen is ScreenId.STUB
    screen = render(content, hero, stub, world_seed=WORLD_SEED)
    assert section in screen.text()
    assert "ещё не готов" in screen.text()
    assert screen.all_rows()[-1][0].text == "Назад"
    assert step(content, hero, stub, "Назад").screen is ScreenId.CITY


# --- locations --------------------------------------------------------


def test_location_list_shows_level_bands(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    listed = step(content, hero, in_city, "Локации")
    text = render(content, hero, listed, world_seed=WORLD_SEED).text()
    assert "Тихие Луга" in text
    assert "уровни с 1 по 4" in text


def test_entering_a_location_starts_at_the_entrance(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    assert in_location.screen is ScreenId.LOCATION
    assert in_location.session.active
    assert in_location.session.node == 0
    assert in_location.session.cycle == CYCLE
    text = render(content, hero, in_location, world_seed=WORLD_SEED).text()
    assert text.startswith("Локация Тихие Луга, узел 0: Вход.")


def test_a_location_above_your_level_is_refused(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    listed = step(content, hero, in_city, "Локации")
    blocked = step(content, hero, listed, "5. Заброшенная Шахта")
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
