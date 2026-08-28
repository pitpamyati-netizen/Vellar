"""Перемещение по меню, миру, городу и локации."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import LocationState, NodeState
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    PlayState,
    advance,
    begin,
    build_location,
    render,
)
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)


@pytest.fixture
def hero(content: GameContent) -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="dwarf", class_id="warrior", level=3)


def step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, hero, current, message, clock=CLOCK, world_seed=WORLD_SEED)
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


# --- меню и мир -------------------------------------------------------


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


# --- лавка и сумка ----------------------------------------------------


def test_shop_and_inventory_are_real_screens(
    content: GameContent, hero: Character, in_city: PlayState, menu: PlayState
) -> None:
    from mmorpg.domain.rules.economy import buy_price, roll_assortment
    from mmorpg.presentation.telegram.flows.play import Goods
    from mmorpg.presentation.telegram.screens.shop import OwnedItem

    stock = roll_assortment(
        content,
        world_seed=WORLD_SEED,
        city_id="farhold",
        rotation=CLOCK.shop_rotation,
        character_level=hero.level,
    )
    goods = Goods(
        gold=500,
        owned=(OwnedItem("small_healing_potion", 2),),
        stock=stock,
        prices={item.id: buy_price(content, item) for item in stock},
    )

    shop = advance(content, hero, in_city, "Лавка", clock=CLOCK, world_seed=WORLD_SEED, goods=goods)
    assert shop.screen is ScreenId.SHOP
    assert (
        "Лавка города Дубно"
        in render(content, hero, shop, world_seed=WORLD_SEED, goods=goods).text()
    )

    inventory = advance(
        content, hero, menu, "Инвентарь", clock=CLOCK, world_seed=WORLD_SEED, goods=goods
    )
    assert inventory.screen is ScreenId.INVENTORY
    assert (
        "Малое зелье лечения"
        in render(content, hero, inventory, world_seed=WORLD_SEED, goods=goods).text()
    )


def test_buying_defers_the_write_to_the_handler(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """Ветка остаётся чистой: она записывает намерение, а исполняет хендлер."""
    from mmorpg.domain.rules.economy import buy_price, roll_assortment
    from mmorpg.presentation.telegram.flows.play import Goods
    from mmorpg.presentation.telegram.screens.shop import buy_label

    wealthy = replace(hero, gold=100_000)
    stock = roll_assortment(
        content,
        world_seed=WORLD_SEED,
        city_id="farhold",
        rotation=CLOCK.shop_rotation,
        character_level=hero.level,
    )
    prices = {item.id: buy_price(content, item) for item in stock}
    rich = Goods(gold=wealthy.gold, stock=stock, prices=prices)
    poor = Goods(gold=0, stock=stock, prices=prices)

    shop = advance(
        content, wealthy, in_city, "Лавка", clock=CLOCK, world_seed=WORLD_SEED, goods=rich
    )
    first = stock[0]
    pressed = buy_label(first, prices[first.id]).text

    card = advance(content, wealthy, shop, pressed, clock=CLOCK, world_seed=WORLD_SEED, goods=rich)
    assert card.screen is ScreenId.SHOP_ITEM
    assert card.pending.empty, "нажатие на товар открывает карточку, а не кошелёк"

    bought = advance(
        content, wealthy, card, "Купить", clock=CLOCK, world_seed=WORLD_SEED, goods=rich
    )
    assert bought.pending.items == ((first.id, 1),)
    assert bought.pending.character is not None
    assert bought.pending.character.gold == wealthy.gold - prices[first.id]
    assert "куплен" in bought.notice

    broke = advance(content, hero, shop, pressed, clock=CLOCK, world_seed=WORLD_SEED, goods=poor)
    assert broke.pending.empty
    # Карточка сама говорит, чего не хватает, и кнопки «Купить» на ней нет.
    card_screen = render(content, hero, broke, world_seed=WORLD_SEED, goods=poor)
    assert "не хватает" in card_screen.text()
    assert "Купить" not in [item.text for row in card_screen.rows for item in row]


@pytest.mark.parametrize(
    ("section", "screen"),
    [
        ("Подземелья", ScreenId.DUNGEON),
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
    """В городе не осталось заглушек: каждая служба отвечает и возвращает (Roadmap 1.5)."""
    opened = step(content, hero, in_city, section)
    assert opened.screen is screen
    rendered = render(content, hero, opened, world_seed=WORLD_SEED)
    assert rendered.lines[0].strip()
    assert rendered.all_rows()[-1][0].text == "Назад"
    assert step(content, hero, opened, "Назад").screen is ScreenId.CITY


# --- локации ----------------------------------------------------------


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
    """Обход графа нажатиями кнопок обязан привести к выходу."""
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


def test_working_a_node_takes_one_thing_out_of_it(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    """Собранное списывает хендлер: узел общий, и автомат только просит об этом."""
    location = build_location(content, WORLD_SEED, in_location.session)
    gather = next((node for node in location.nodes if node.kind.value == "gather"), None)
    if gather is None:
        pytest.skip("this seed produced no gathering node")

    at_node = replace(in_location, session=replace(in_location.session, node=gather.index))
    done = step(content, hero, at_node, "Собрать сырьё")
    assert done.pending.node_take == gather.index
    assert "сделано" in done.notice


def test_an_emptied_node_says_when_it_fills_up_again(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    location = build_location(content, WORLD_SEED, in_location.session)
    gather = next((node for node in location.nodes if node.kind.value == "gather"), None)
    if gather is None:
        pytest.skip("this seed produced no gathering node")

    at_node = replace(in_location, session=replace(in_location.session, node=gather.index))
    emptied = LocationState(nodes={gather.index: NodeState(taken=99, emptied_at=100)})
    refused = advance(
        content,
        hero,
        at_node,
        "Собрать сырьё",
        world_seed=WORLD_SEED,
        clock=Clock(now=160),
        location_state=emptied,
    )
    assert refused.pending.node_take < 0
    assert "разобрали" in refused.notice
    # Сколько ждать, говорит сам экран - и говорит это один раз.
    text = render(
        content,
        hero,
        refused,
        world_seed=WORLD_SEED,
        clock=Clock(now=160),
        location_state=emptied,
    ).text()
    assert "Здесь пусто. Новое появится примерно через" in text
    assert text.count("Новое появится") == 1, "срок называют один раз, а не дважды подряд"


def test_leaving_a_location_clears_the_session(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    left = step(content, hero, in_location, "Покинуть локацию")
    assert left.screen is ScreenId.LOCATION_LIST
    assert left.session.active is False


def test_the_map_does_not_move_under_your_feet(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    """Карту двигает выработка, а не часы: постоял, осмотрелся - всё на месте (ADR 0035)."""
    before = render(content, hero, in_location, world_seed=WORLD_SEED).text()
    later = advance(
        content,
        hero,
        in_location,
        "Осмотреться",
        clock=replace(CLOCK, now=CLOCK.now + 5),
        world_seed=WORLD_SEED,
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


# --- перемещение ------------------------------------------------------


def test_back_walks_the_stack(
    content: GameContent, hero: Character, in_location: PlayState
) -> None:
    assert step(content, hero, in_location, "Назад").screen is ScreenId.LOCATION_LIST
    assert step(content, hero, in_location, "Назад", "Назад").screen is ScreenId.CITY
    assert step(content, hero, in_location, "Назад", "Назад", "Назад").screen is ScreenId.WORLD


def test_coming_back_to_a_screen_unwinds_instead_of_stacking(
    content: GameContent, hero: Character
) -> None:
    """Положив умение в слот, «Назад» ведёт в «Умения», а не в выбор умения.

    Прогулка была стопкой посещённого: «Умения» → «Слоты» → «Слот 3» → «Слоты»,
    и шаг назад открывал тот самый выбор, который только что кончился, —
    «Слот 3, боевой. Выберите умение». Слот при этом читался пустым, хотя умение
    в нём уже лежало.
    """
    learned = replace(
        hero,
        loadout=replace(hero.loadout, ranks={**hero.loadout.ranks, "warrior_udar_shchitom": 1}),
    )
    slots = step(content, learned, begin(learned), "Умения", "Слоты умений")
    picking = step(content, learned, slots, "Боевой слот 3: пусто")
    assert picking.screen is ScreenId.SKILL_PICK

    back_to_slots = step(content, learned, picking, "Назад")
    assert back_to_slots.screen is ScreenId.SKILL_SLOTS
    assert back_to_slots.stack.screens.count(ScreenId.SKILL_SLOTS) == 1
    assert ScreenId.SKILL_PICK not in back_to_slots.stack.screens
    assert step(content, learned, back_to_slots, "Назад").screen is ScreenId.SKILLS


def test_the_main_menu_has_no_buttons_that_do_nothing(
    content: GameContent, hero: Character
) -> None:
    """«Назад» из главного меню вело в главное меню, «Главное меню» — туда же."""
    home = render(content, hero, begin(hero), world_seed=WORLD_SEED)
    assert home.id is ScreenId.MAIN_MENU
    pressed = [text for row in home.button_texts() for text in row]
    assert "Назад" not in pressed
    assert "Главное меню" not in pressed
    # Команды при этом работают, и старая клавиатура тоже не молчит.
    assert step(content, hero, begin(hero), "/назад").screen is ScreenId.MAIN_MENU
    assert step(content, hero, begin(hero), "Назад").screen is ScreenId.MAIN_MENU


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


def test_a_deep_descent_survives_a_round_trip(hero: Character) -> None:
    from mmorpg.presentation.telegram.flows.state import Descent

    deep = PlayState(descent=Descent(city_id="farhold", level=30, depth=2, started_at=7, tier=2))
    restored = PlayState.deserialise(deep.serialise())
    assert restored.descent == deep.descent
    assert restored.descent.deep


# --- состояние, пришедшее от прежнего выпуска -------------------------  Хранилище
# живёт дольше содержимого и дольше кода, который в него писал. Экран, называющий то,
# чего игра больше не может собрать, обязан посадить игрока на что-то настоящее: падение
# здесь отвечает извинением на *каждое* следующее нажатие, потому что вызвавшее его
# состояние читается каждый раз заново.

# Записано выпуском, который сохранял экран локации без стоящей за ним вылазки. Ровно
# такой документ нашёлся в Redis, и каждое нажатие по нему падало.
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


# --- вступление -------------------------------------------------------


def test_the_introduction_is_offered_until_it_is_done(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    """Разыгранному персонажу кнопка обучения навсегда не нужна."""
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
    """Смысл кнопки: не надо запоминать дорогу и не надо угадывать меню."""
    opened = step(content, hero, menu, "Обучение")
    assert opened.screen is ScreenId.TUTORIAL

    walked = step(content, hero, opened, "Перейти к шагу")
    assert walked.screen is ScreenId.STATS
    # Прочитать их и есть дело, поэтому оно уже отмечено.
    assert walked.pending.character is not None
    assert walked.pending.character.tutorial != hero.tutorial


def test_a_task_done_by_playing_counts_too(
    content: GameContent, hero: Character, menu: PlayState
) -> None:
    """Открывать вступление не обязательно, чтобы оно заметило сделанное."""
    from mmorpg.domain.rules import tutorial as tutorial_rules
    from mmorpg.domain.rules.tutorial import TutorialTask

    stats = step(content, hero, menu, "Персонаж", "Характеристики")
    assert stats.pending.character is not None
    assert tutorial_rules.is_done(stats.pending.character, TutorialTask.STATS)
    assert "Шаг обучения сделан" in stats.notice


def test_a_finished_introduction_says_so_and_offers_nothing(
    content: GameContent, hero: Character
) -> None:
    taught = replace(hero, tutorial=0b111111)
    # Кнопка ушла из меню, но экран всё равно отвечает тому, кто попал на него со старой
    # клавиатуры (правило доступности 12).
    standing = begin(taught).at(ScreenId.TUTORIAL)
    text = render(content, taught, standing, world_seed=WORLD_SEED).text()
    assert "Все шаги сделаны" in text

    pressed = step(content, taught, standing, "Перейти к шагу")
    assert pressed.pending.empty


# --- другие игроки на дороге ------------------------------------------


def test_a_free_location_shows_who_else_is_here(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    """На вольной локации важнее всего новость о том, кто стоит рядом."""
    from mmorpg.domain.entities.location import Presence

    grown = replace(hero, level=20)
    listed = step(content, grown, in_city, "Локации")
    text = render(content, grown, listed, world_seed=WORLD_SEED).text()
    assert "вольная" in text, "a location where you can be robbed must say so"

    walked = step(content, grown, listed, "4. Тракт на Затон")
    company = (Presence(character_id=99, name="Мерла", level=21, node=0),)
    screen = render(content, grown, walked, world_seed=WORLD_SEED, neighbours=company)
    assert "Мерла, уровень 21." in screen.text()
    assert "Напасть: Мерла" in [item.text for row in screen.rows for item in row]


def test_a_peaceful_location_offers_nobody_to_attack(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    from mmorpg.domain.entities.location import Presence

    grown = replace(hero, level=20)
    walked = step(content, grown, in_city, "Локации", "1. Луга у Заставы")
    company = (Presence(character_id=99, name="Мерла", level=21, node=0),)
    screen = render(content, grown, walked, world_seed=WORLD_SEED, neighbours=company)
    assert "Напасть: Мерла" not in [item.text for row in screen.rows for item in row]


def test_an_attack_on_a_newcomer_is_refused_in_words(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    from mmorpg.domain.entities.location import Presence

    grown = replace(hero, level=20)
    walked = step(content, grown, in_city, "Локации", "4. Тракт на Затон")
    company = (Presence(character_id=99, name="Мерла", level=3, node=0),)
    refused = advance(
        content,
        grown,
        walked,
        "Напасть: Мерла",
        clock=CLOCK,
        world_seed=WORLD_SEED,
        neighbours=company,
    )
    assert refused.fight == ""
    assert "под защитой" in refused.notice


def test_an_allowed_attack_hands_the_fight_to_the_handler(
    content: GameContent, hero: Character, in_city: PlayState
) -> None:
    from mmorpg.domain.entities.location import Presence

    grown = replace(hero, level=20)
    walked = step(content, grown, in_city, "Локации", "4. Тракт на Затон")
    company = (Presence(character_id=99, name="Мерла", level=21, node=0),)
    started = advance(
        content,
        grown,
        walked,
        "Напасть: Мерла",
        clock=CLOCK,
        world_seed=WORLD_SEED,
        neighbours=company,
    )
    assert started.fight == "pvp:99"
    assert started.screen is ScreenId.COMBAT
