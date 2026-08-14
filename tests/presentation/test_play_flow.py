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
        "Лавка города Дальний Оплот"
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

    stock = roll_assortment(
        content, world_seed=WORLD_SEED, city_id="farhold", cycle=CYCLE, character_level=hero.level
    )
    prices = {item.id: buy_price(content, item) for item in stock}
    rich = Goods(gold=100_000, stock=stock, prices=prices)
    poor = Goods(gold=0, stock=stock, prices=prices)

    shop = advance(content, hero, in_city, "Лавка", cycle=CYCLE, world_seed=WORLD_SEED, goods=rich)
    first = stock[0]
    pressed = buy_label(first, prices[first.id]).text

    bought = advance(content, hero, shop, pressed, cycle=CYCLE, world_seed=WORLD_SEED, goods=rich)
    assert bought.pending_purchase == first.id
    assert "куплен" in bought.notice

    broke = advance(content, hero, shop, pressed, cycle=CYCLE, world_seed=WORLD_SEED, goods=poor)
    assert broke.pending_purchase == ""
    assert "Не хватает" in broke.notice


# --- city sections ----------------------------------------------------


@pytest.mark.parametrize(
    ("section", "screen_id"),
    [
        ("Данжи", ScreenId.DUNGEONS),
        ("Таверна", ScreenId.TAVERN),
        ("Наставник", ScreenId.MENTOR),
        ("Банк", ScreenId.BANK),
    ],
)
def test_every_city_section_is_a_real_screen(
    content: GameContent, hero: Character, in_city: PlayState, section: str, screen_id: ScreenId
) -> None:
    """No section answers with "not ready yet": each one opens and walks back."""
    opened = step(content, hero, in_city, section)
    assert opened.screen is screen_id
    screen = render(content, hero, opened, world_seed=WORLD_SEED, cycle=CYCLE)
    assert screen.text()
    assert "ещё не готов" not in screen.text()
    assert screen.all_rows()[-1][0].text == "Назад"
    assert step(content, hero, opened, "Назад").screen is ScreenId.CITY


def test_the_tavern_reads_the_watch_summary(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    from mmorpg.presentation.telegram.flows.play import rumours_of

    tavern = step(content, hero, in_city, "Таверна")
    rumours = rumours_of(content, hero, tavern, world_seed=WORLD_SEED, cycle=CYCLE)
    assert rumours
    text = render(content, hero, tavern, world_seed=WORLD_SEED, cycle=CYCLE).text()
    assert rumours[0].location_name in text

    asked = advance(
        content,
        hero,
        tavern,
        f"Расспросить: {rumours[0].location_name}",
        cycle=CYCLE,
        world_seed=WORLD_SEED,
    )
    assert rumours[0].location_name in asked.notice
    assert "стражи" in asked.notice


def test_the_mentor_defers_the_point_to_the_handler(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """The flow decides which stat grows; the handler saves the character."""
    trainee = replace(hero, unspent_stat_points=1)
    mentor = step(content, trainee, in_city, "Наставник")

    spent = advance(content, trainee, mentor, "Поднять силу", cycle=CYCLE, world_seed=WORLD_SEED)
    assert spent.pending_stat == "STR"
    assert "Свободных очков: 0" in spent.notice

    empty = advance(content, hero, mentor, "Поднять силу", cycle=CYCLE, world_seed=WORLD_SEED)
    assert empty.pending_stat == ""
    assert "Свободных очков нет" in empty.notice


def test_the_vault_takes_gold_in_and_hands_it_back(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    rich = replace(hero, gold=1000)
    bank = step(content, rich, in_city, "Банк")

    deposited = advance(content, rich, bank, "Положить 100", cycle=CYCLE, world_seed=WORLD_SEED)
    assert deposited.pending_transfer is not None
    assert deposited.pending_transfer.amount == 100
    assert deposited.pending_transfer.fee == 2

    # Typing the same thing is the same act (accessibility rule 10).
    typed = advance(content, rich, bank, "положить 100", cycle=CYCLE, world_seed=WORLD_SEED)
    assert typed.pending_transfer == deposited.pending_transfer

    holder = replace(hero, bank_gold=50)
    stored = step(content, holder, in_city, "Банк")
    taken = advance(content, holder, stored, "Снять всё", cycle=CYCLE, world_seed=WORLD_SEED)
    assert taken.pending_transfer is not None
    assert taken.pending_transfer.amount == 50
    assert taken.pending_transfer.fee == 0

    broke = advance(content, hero, bank, "Положить 1000", cycle=CYCLE, world_seed=WORLD_SEED)
    assert broke.pending_transfer is None
    assert broke.notice


async def test_the_handler_writes_what_the_flow_decided(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """The intent is not a promise: the character comes back changed and saved."""
    from mmorpg.infrastructure.persistence.memory import InMemoryCharacterRepository
    from mmorpg.presentation.telegram.handlers.play import _settle

    characters = InMemoryCharacterRepository()
    stored = await characters.create(replace(hero, gold=500, unspent_stat_points=1))

    mentor = step(content, stored, in_city, "Наставник")
    trained = advance(
        content, stored, mentor, "Поднять выносливость", cycle=CYCLE, world_seed=WORLD_SEED
    )
    after_lesson = await _settle(characters, stored, trained)
    assert after_lesson.allocated.END == stored.allocated.END + 1
    assert after_lesson.unspent_stat_points == 0

    bank = step(content, after_lesson, in_city, "Банк")
    moved = advance(content, after_lesson, bank, "Положить 200", cycle=CYCLE, world_seed=WORLD_SEED)
    after_deposit = await _settle(characters, after_lesson, moved)
    assert after_deposit.bank_gold == 200
    assert after_deposit.gold == after_lesson.gold - 204

    saved = await characters.get(stored.id)
    assert saved == after_deposit


def test_a_dungeon_entrance_can_be_looked_at(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    from mmorpg.presentation.telegram.flows.play import dungeons_of

    gates = step(content, hero, in_city, "Данжи")
    dungeons = dungeons_of(content, hero, gates, world_seed=WORLD_SEED)
    assert dungeons

    looked = advance(
        content,
        hero,
        gates,
        f"{dungeons[0].name}, осмотреть вход",
        cycle=CYCLE,
        world_seed=WORLD_SEED,
    )
    assert "первый ярус" in looked.notice
    assert looked.screen is ScreenId.DUNGEONS


def test_skills_open_from_the_menu_and_read_a_skill_back(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    from mmorpg.presentation.telegram.screens.skills import known_skills

    skills = step(content, hero, menu, "Умения")
    assert skills.screen is ScreenId.SKILLS
    text = render(content, hero, skills, world_seed=WORLD_SEED).text()
    assert "Активные слоты:" in text

    first = known_skills(content, hero)[0]
    read = step(content, hero, skills, f"{first.name}, активное")
    assert first.name in read.notice
    assert step(content, hero, skills, "Назад").screen is ScreenId.MAIN_MENU


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
