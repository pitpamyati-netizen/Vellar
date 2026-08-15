"""Ремёсла from the main menu: gather a watch, make a batch, keep the bag honest."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    Goods,
    PlayState,
    advance,
    begin,
    render,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.shop import OwnedItem

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100, gather_cooldown=900)


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="dwarf",
        class_id="warrior",
        level=10,
        crafts=CraftLog(MappingProxyType({"smithing": CraftProgress(experience=120)})),
    )


@pytest.fixture
def bag() -> Goods:
    return Goods(
        gold=100,
        owned=(
            OwnedItem("iron_scrap", 6),
            OwnedItem("mountain_ore", 4),
            OwnedItem("wolf_pelt", 1),
        ),
    )


def step(
    content: GameContent,
    hero: Character,
    state: PlayState,
    *messages: str,
    goods: Goods | None = None,
    clock: Clock = CLOCK,
) -> PlayState:
    current = state
    for message in messages:
        current = advance(
            content, hero, current, message, clock=clock, world_seed=WORLD_SEED, goods=goods
        )
    return current


def screen(content: GameContent, hero: Character, state: PlayState, goods: Goods | None = None):
    return render(content, hero, state, world_seed=WORLD_SEED, goods=goods)


def button(
    content: GameContent,
    hero: Character,
    state: PlayState,
    prefix: str,
    goods: Goods | None = None,
) -> str:
    """The exact text of the button that starts with ``prefix``.

    Craft buttons carry the rank and the work left in them, so a test that typed
    them out by hand would break on every balance change and prove nothing.
    """
    for row in screen(content, hero, state, goods).rows:
        for pressed in row:
            if pressed.text.startswith(prefix):
                return pressed.text
    raise AssertionError(f"no button starts with {prefix!r}")


def pressable(
    content: GameContent, hero: Character, state: PlayState, goods: Goods | None = None
) -> str:
    """Every button on the screen, as one string to look through."""
    return " | ".join(
        pressed.text for row in screen(content, hero, state, goods).rows for pressed in row
    )


# --- getting there ----------------------------------------------------


def test_the_menu_opens_the_craft_list(content: GameContent, hero: Character) -> None:
    state = step(content, hero, begin(hero), "Ремёсла")
    assert state.screen is ScreenId.CRAFTS
    assert "Ремёсла" in screen(content, hero, state).text()
    offered = pressable(content, hero, state)
    assert "Кузнечное дело" in offered
    assert "ранг 2" in offered, "the rank a craft has reached is on its button"


def test_a_craft_opens_its_own_screen(content: GameContent, hero: Character) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла")
    state = step(content, hero, listed, button(content, hero, listed, "Горное дело"))
    assert state.screen is ScreenId.CRAFT
    assert state.craft_id == "mining"
    text = screen(content, hero, state).text()
    assert "Собрать сырьё" in text


def test_back_walks_out_the_way_it_came(content: GameContent, hero: Character) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла")
    state = step(content, hero, listed, button(content, hero, listed, "Горное дело"), "Назад")
    assert state.screen is ScreenId.CRAFTS
    assert step(content, hero, state, "Назад").screen is ScreenId.MAIN_MENU


# --- gathering --------------------------------------------------------


def test_gathering_puts_material_in_the_bag_and_records_the_work(
    content: GameContent, hero: Character
) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла")
    state = step(
        content, hero, listed, button(content, hero, listed, "Горное дело"), "Собрать сырьё"
    )
    assert "Собрано:" in state.notice
    assert state.pending.character is not None
    assert state.pending.character.crafts.progress("mining").experience > 0
    assert len(state.pending.items) == 1
    item_id, count = state.pending.items[0]
    assert item_id in {"iron_scrap", "mountain_ore"}
    assert count > 0


def test_a_second_gathering_inside_the_cooldown_is_refused(
    content: GameContent, hero: Character
) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла")
    first = step(
        content, hero, listed, button(content, hero, listed, "Горное дело"), "Собрать сырьё"
    )
    worked = first.pending.character
    assert worked is not None
    again = step(content, worked, first, "Собрать сырьё")
    assert "Следующий сбор через" in again.notice
    assert again.pending.empty, "a refused gathering writes nothing"


# --- making -----------------------------------------------------------


def test_a_batch_spends_from_the_bag_and_pays_in_items(
    content: GameContent, hero: Character, bag: Goods
) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла", goods=bag)
    state = step(content, hero, listed, button(content, hero, listed, "Кузнечное дело"), goods=bag)
    made = step(
        content, hero, state, button(content, hero, state, "Точильный камень", bag), goods=bag
    )
    assert "Сделано: Точильный камень" in made.notice
    assert "качество" in made.notice
    changes = dict(made.pending.items)
    assert changes["iron_scrap"] < 0
    assert changes["whetstone"] > 0
    assert made.pending.character is not None


def test_missing_materials_are_named_and_nothing_is_written(
    content: GameContent, hero: Character
) -> None:
    empty = Goods(gold=0)
    listed = step(content, hero, begin(hero), "Ремёсла", goods=empty)
    smithy = button(content, hero, listed, "Кузнечное дело")
    state = step(content, hero, listed, smithy, goods=empty)
    refused = step(
        content, hero, state, button(content, hero, state, "Точильный камень", empty), goods=empty
    )
    assert "Железный лом" in refused.notice
    assert refused.pending.empty


def test_recipes_above_the_rank_are_not_offered(
    content: GameContent, hero: Character, bag: Goods
) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла", goods=bag)
    state = step(content, hero, listed, button(content, hero, listed, "Кузнечное дело"), goods=bag)
    offered = pressable(content, hero, state, bag)
    assert "Железный шлем" in offered, "rank two is open"
    assert "Кольчужная рубаха" not in offered, "rank three is not"
    assert "откроется с рангом" in screen(content, hero, state, bag).text()


def test_a_stale_craft_screen_falls_back_to_the_list(content: GameContent, hero: Character) -> None:
    """A player can return to a craft that content no longer has; nothing raises."""
    stale = replace(begin(hero), screen=ScreenId.CRAFT, craft_id="basket_weaving")
    assert screen(content, hero, stale).id is ScreenId.CRAFTS


def test_the_walk_survives_a_round_trip_through_storage(
    content: GameContent, hero: Character
) -> None:
    listed = step(content, hero, begin(hero), "Ремёсла")
    state = step(content, hero, listed, button(content, hero, listed, "Горное дело"))
    restored = PlayState.deserialise(state.serialise())
    assert restored.screen is ScreenId.CRAFT
    assert restored.craft_id == "mining"
    assert restored.craft_moment == CLOCK.now
