"""Кузница в городе: чинят по одной и всё разом, и платят вперёд (ADR 0057)."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import Equipment, ItemWear
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.presentation.telegram.flows.play import Clock, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)

SWORD = "sword@6#common"
PLATE = "heavy_body@6#common"


@pytest.fixture
def battered() -> Character:
    """Тот, у кого меч сточен наполовину, а латы сломаны совсем."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        gold=4000,
        equipment=Equipment(MappingProxyType({"weapon": SWORD, "body": PLATE})),
        wear=ItemWear(MappingProxyType({SWORD: 20, PLATE: 10_000})),
    )


def _step(content: GameContent, hero: Character, state: PlayState, *messages: str) -> PlayState:
    for message in messages:
        state = advance(content, hero, state, message, clock=CLOCK, world_seed=WORLD_SEED)
    return state


def test_the_city_offers_the_forge(content: GameContent, battered: Character) -> None:
    state = _step(content, battered, begin(battered), "Мир", "Кузница")
    assert state.screen is ScreenId.FORGE


def test_the_forge_names_the_broken_and_the_price(
    content: GameContent, battered: Character
) -> None:
    state = _step(content, battered, begin(battered), "Мир", "Кузница")
    text = render(content, battered, state, world_seed=WORLD_SEED, clock=CLOCK).text()
    assert "сломана и не даёт ничего" in text
    assert "Починить всё разом" in text
    assert "Инструмент здесь не чинят" in text


def test_a_whole_character_is_told_there_is_nothing_to_mend(
    content: GameContent, battered: Character
) -> None:
    whole = replace(battered, wear=ItemWear())
    state = _step(content, whole, begin(whole), "Мир", "Кузница")
    screen = render(content, whole, state, world_seed=WORLD_SEED, clock=CLOCK)
    assert "Чинить нечего" in screen.text()
    assert screen.rows == ()


def test_mending_one_thing_pays_and_returns_it(content: GameContent, battered: Character) -> None:
    item = content.item(SWORD)
    price = repair_rules.price_of(battered, item)
    state = _step(content, battered, begin(battered), "Мир", "Кузница", f"Починить: {item.name}")

    fixed = state.pending.character
    assert fixed is not None
    assert fixed.gold == battered.gold - price
    assert fixed.wear.spent(SWORD) == 0
    assert fixed.wear.spent(PLATE) == battered.wear.spent(PLATE), "чужой износ не трогают"


def test_mending_everything_pays_the_whole_bill(content: GameContent, battered: Character) -> None:
    whole = repair_rules.total(repair_rules.bill(content, battered))
    state = _step(content, battered, begin(battered), "Мир", "Кузница", "Починить всё")

    fixed = state.pending.character
    assert fixed is not None
    assert fixed.gold == battered.gold - whole
    assert repair_rules.bill(content, fixed) == ()


def test_the_smith_refuses_without_the_money(content: GameContent, battered: Character) -> None:
    poor = replace(battered, gold=1)
    state = _step(content, poor, begin(poor), "Мир", "Кузница", "Починить всё")
    assert state.pending.character is None
    assert "Работа стоит" in state.notice
