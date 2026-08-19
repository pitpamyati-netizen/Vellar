"""The keeper screen: who sees it, what it does, and who it stops.

The right comes from ``ADMIN_IDS`` or from an account that was handed it; the
character column only mirrors the two. Every half is tested here, because a
service door that opens for a player is the one bug this feature can have.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.application.services.keeper import is_keeper, set_keeper, sync_keeper
from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import keeper as keeper_rules
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryUserRepository,
)
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    PlayState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.paginated import PageState

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)
PLAYER = 500_100


@pytest.fixture
def player() -> Character:
    return Character(id=1, user_id=PLAYER, name="Аргус", race_id="human", class_id="warrior")


@pytest.fixture
def keeper(player: Character) -> Character:
    return replace(player, is_admin=True)


def step(content: GameContent, who: Character, state: PlayState, *messages: str) -> PlayState:
    current = state
    for message in messages:
        current = advance(content, who, current, message, clock=CLOCK, world_seed=WORLD_SEED)
    return current


def buttons(content: GameContent, who: Character, state: PlayState) -> list[str]:
    screen = render(content, who, state, world_seed=WORLD_SEED)
    return [text for row in screen.button_texts() for text in row]


# --- who sees the door ------------------------------------------------


def test_a_player_never_sees_the_keeper_button(content: GameContent, player: Character) -> None:
    assert labels.KEEPER.text not in buttons(content, player, begin(player))


def test_a_keeper_sees_it_in_the_main_menu(content: GameContent, keeper: Character) -> None:
    assert labels.KEEPER.text in buttons(content, keeper, begin(keeper))


def test_a_player_typing_the_label_is_not_let_in(content: GameContent, player: Character) -> None:
    """The label is text, and text can be typed by anybody."""
    pressed = step(content, player, begin(player), labels.KEEPER.text)
    assert pressed.screen is ScreenId.MAIN_MENU
    assert pressed.notice


def test_a_keeper_opens_the_screen(content: GameContent, keeper: Character) -> None:
    opened = step(content, keeper, begin(keeper), labels.KEEPER.text)
    assert opened.screen is ScreenId.KEEPER
    assert "Смотритель." in render(content, keeper, opened, world_seed=WORLD_SEED).text()


def test_the_right_can_be_taken_away_between_two_presses(
    content: GameContent, keeper: Character, player: Character
) -> None:
    """ADMIN_IDS changed and the character was reloaded without the flag."""
    opened = step(content, keeper, begin(keeper), labels.KEEPER.text)
    refused = step(content, player, opened, labels.KEEPER_GOLD.text)

    assert refused.screen is ScreenId.MAIN_MENU
    assert refused.pending.character is None
    assert render(content, player, refused, world_seed=WORLD_SEED).id is ScreenId.MAIN_MENU


# --- what the buttons do ----------------------------------------------


@pytest.fixture
def at_keeper(content: GameContent, keeper: Character) -> PlayState:
    return step(content, keeper, begin(keeper), labels.KEEPER.text)


def test_gold_is_handed_over_and_left_for_the_handler_to_store(
    content: GameContent, keeper: Character, at_keeper: PlayState
) -> None:
    paid = step(content, keeper, at_keeper, labels.KEEPER_GOLD.text)

    assert paid.pending.character is not None
    assert paid.pending.character.gold == keeper.gold + keeper_rules.GOLD_STEP
    assert str(keeper_rules.GOLD_STEP) in paid.notice


def test_a_level_is_granted_with_its_points(
    content: GameContent, keeper: Character, at_keeper: PlayState
) -> None:
    grown = step(content, keeper, at_keeper, labels.KEEPER_LEVEL.text)

    assert grown.pending.character is not None
    assert grown.pending.character.level == keeper.level + 1
    assert grown.pending.character.unspent_stat_points > keeper.unspent_stat_points


def test_healing_reports_the_number_it_reached(
    content: GameContent, keeper: Character, at_keeper: PlayState
) -> None:
    wounded = replace(keeper, health=3)
    healed = step(content, wounded, at_keeper, labels.KEEPER_HEAL.text)

    assert healed.pending.character is not None
    assert healed.pending.character.health > 3
    assert str(healed.pending.character.health) in healed.notice


def test_points_are_handed_out_in_both_kinds(
    content: GameContent, keeper: Character, at_keeper: PlayState
) -> None:
    granted = step(content, keeper, at_keeper, labels.KEEPER_POINTS.text)

    assert granted.pending.character is not None
    assert granted.pending.character.unspent_stat_points == keeper_rules.POINTS_STEP
    assert granted.pending.character.unspent_skill_points == keeper_rules.POINTS_STEP


def test_the_service_row_still_works_here(
    content: GameContent, keeper: Character, at_keeper: PlayState
) -> None:
    assert step(content, keeper, at_keeper, "Главное меню").screen is ScreenId.MAIN_MENU
    assert step(content, keeper, at_keeper, "Назад").screen is ScreenId.MAIN_MENU


# --- the road is open -------------------------------------------------


def test_a_keeper_walks_into_a_city_a_player_could_not(
    content: GameContent, keeper: Character, player: Character
) -> None:
    locked = next(city for city in content.cities if city.unlock_level > player.level)

    assert step(content, player, begin(player), "Мир", locked.name).screen is ScreenId.WORLD
    walked = step(content, keeper, begin(keeper), "Мир", locked.name)
    assert walked.screen is ScreenId.CITY
    assert walked.city_id == locked.id


# --- the flag mirrors the setting -------------------------------------


async def test_the_flag_is_written_when_the_setting_names_the_player(player: Character) -> None:
    characters = InMemoryCharacterRepository()
    stored = await characters.create(player)
    settings = Settings(_env_file=None, admin_ids=f"{PLAYER}, 1")  # type: ignore[call-arg]

    synced = await sync_keeper(stored, PLAYER, settings, characters)

    assert synced.is_admin is True
    read = await characters.get(stored.id)
    assert read is not None and read.is_admin is True


async def test_the_flag_is_taken_back_when_the_setting_stops_naming_them(
    keeper: Character,
) -> None:
    characters = InMemoryCharacterRepository()
    stored = await characters.create(keeper)
    settings = Settings(_env_file=None, admin_ids="")  # type: ignore[call-arg]

    synced = await sync_keeper(stored, PLAYER, settings, characters)

    assert synced.is_admin is False
    read = await characters.get(stored.id)
    assert read is not None and read.is_admin is False


async def test_nothing_is_written_when_the_answer_did_not_change(player: Character) -> None:
    characters = InMemoryCharacterRepository()
    stored = await characters.create(player)
    settings = Settings(_env_file=None, admin_ids="")  # type: ignore[call-arg]

    assert await sync_keeper(stored, PLAYER, settings, characters) is stored


def test_a_malformed_admin_ids_names_nobody() -> None:
    settings = Settings(_env_file=None, admin_ids="не число, ,  42 ")  # type: ignore[call-arg]

    assert settings.admins == frozenset({42})
    assert settings.is_admin(42) is True
    assert settings.is_admin(PLAYER) is False


# --- the right handed out from inside the game -------------------------


async def test_a_granted_account_gets_the_flag_without_the_setting(player: Character) -> None:
    characters = InMemoryCharacterRepository()
    stored = await characters.create(player)
    settings = Settings(_env_file=None, admin_ids="")  # type: ignore[call-arg]

    synced = await sync_keeper(stored, PLAYER, settings, characters, granted=True)

    assert synced.is_admin is True


async def test_the_right_lands_on_the_account_and_on_every_character() -> None:
    """Персонажей у человека может быть несколько, а право у него одно."""
    characters = InMemoryCharacterRepository()
    users = InMemoryUserRepository()
    settings = Settings(_env_file=None, admin_ids="")  # type: ignore[call-arg]
    first = await characters.create(
        Character(id=0, user_id=PLAYER, name="Аргус", race_id="human", class_id="warrior")
    )
    second = await characters.create(
        Character(id=0, user_id=PLAYER, name="Мерла", race_id="human", class_id="warrior")
    )

    assert await set_keeper(users, characters, PLAYER, keeper=True, settings=settings) is True

    account = await users.get(PLAYER)
    assert account is not None and account.keeper is True
    for character_id in (first.id, second.id):
        read = await characters.get(character_id)
        assert read is not None and read.is_admin is True

    assert await set_keeper(users, characters, PLAYER, keeper=False, settings=settings) is True

    account = await users.get(PLAYER)
    assert account is not None and account.keeper is False
    read = await characters.get(first.id)
    assert read is not None and read.is_admin is False


async def test_the_right_that_comes_from_the_setting_cannot_be_taken_here() -> None:
    characters = InMemoryCharacterRepository()
    users = InMemoryUserRepository()
    settings = Settings(_env_file=None, admin_ids=str(PLAYER))  # type: ignore[call-arg]
    stored = await characters.create(
        Character(
            id=0,
            user_id=PLAYER,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            is_admin=True,
        )
    )

    assert await set_keeper(users, characters, PLAYER, keeper=False, settings=settings) is False

    read = await characters.get(stored.id)
    assert read is not None and read.is_admin is True


async def test_is_keeper_answers_from_either_source() -> None:
    users = InMemoryUserRepository()
    named = Settings(_env_file=None, admin_ids=str(PLAYER))  # type: ignore[call-arg]
    nameless = Settings(_env_file=None, admin_ids="")  # type: ignore[call-arg]

    assert await is_keeper(users, PLAYER, named) is True
    assert await is_keeper(users, PLAYER, nameless) is False

    await users.set_keeper(PLAYER, True)

    assert await is_keeper(users, PLAYER, nameless) is True


def test_the_main_menu_survives_a_city_taken_out_of_the_game(content: GameContent) -> None:
    """Смотритель может убрать город, пока в нём кто-то стоит (docs/keeper.md).

    Экран, который после этого падает у каждого жителя убранного города, — это
    игра, в которую они больше не могут войти.
    """
    from mmorpg.domain.rules.stats import derived_stats
    from mmorpg.presentation.telegram.screens import play as play_screens

    lost = Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        city_id="города-такого-нет",
    )
    menu = play_screens.main_menu_screen(content, lost, derived_stats(content, lost))
    assert menu.text().startswith("Главное меню.")
    assert content.cities[0].name in menu.text()

    world = play_screens.world_screen(content, lost, PageState())
    assert content.cities[0].name in world.text()
