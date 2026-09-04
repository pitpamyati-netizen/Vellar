"""Кузница в городе: чинят, разбирают и перековывают (ADR 0057, 0059, 0060)."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import Equipment, ItemWear
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.domain.rules import salvage as salvage_rules
from mmorpg.presentation.telegram.flows.play import Clock, Goods, PlayState, advance, begin, render
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.shop import OwnedItem

WORLD_SEED = "vellar-test"
CLOCK = Clock(now=1_700_000_000, shop_rotation=100)

SWORD = "sword@5#common"
PLATE = "heavy_body@5#common"


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


def _step(
    content: GameContent,
    hero: Character,
    state: PlayState,
    *messages: str,
    goods: Goods | None = None,
) -> PlayState:
    for message in messages:
        state = advance(
            content, hero, state, message, clock=CLOCK, world_seed=WORLD_SEED, goods=goods
        )
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


# --- разбор и перековка (ADR 0060) -----------------------------------

SPARE = "medium_body@5#uncommon"


def _bag(battered: Character) -> Goods:
    return Goods(gold=battered.gold, owned=(OwnedItem(SPARE, 1),))


def test_the_forge_offers_more_than_mending(content: GameContent, battered: Character) -> None:
    state = _step(content, battered, begin(battered), "Мир", "Кузница")
    screen = render(content, battered, state, world_seed=WORLD_SEED, clock=CLOCK)
    buttons = {button.text for row in screen.rows for button in row}
    assert any("Разобрать вещи" in text for text in buttons)
    assert any("Перековать вещь" in text for text in buttons)


def test_taking_a_thing_apart_gives_material_back(
    content: GameContent, battered: Character
) -> None:
    goods = _bag(battered)
    item = content.item(SPARE)
    state = _step(
        content,
        battered,
        begin(battered),
        "Мир",
        "Кузница",
        "Разобрать вещи",
        f"Разобрать: {item.name}",
        goods=goods,
    )
    made = salvage_rules.yield_of(content, item)
    assert state.pending.items == ((SPARE, -1), *made)
    assert "разобран" in state.notice


def test_the_forge_never_takes_apart_what_is_worn(
    content: GameContent, battered: Character
) -> None:
    """Кузнец не снимает с игрока сапоги: надетое в список не попадает вовсе."""
    goods = Goods(gold=battered.gold, owned=(OwnedItem(PLATE, 1),))
    state = _step(
        content,
        battered,
        begin(battered),
        "Мир",
        "Кузница",
        "Разобрать вещи",
        f"Разобрать: {content.item(PLATE).name}",
        goods=goods,
    )
    assert state.pending.items == ()
    assert "надета" in state.notice


def test_reforging_pays_and_changes_the_stamp(content: GameContent, battered: Character) -> None:
    goods = _bag(battered)
    item = content.item(SPARE)
    price = salvage_rules.reforge_price(content, item)
    state = _step(
        content,
        battered,
        begin(battered),
        "Мир",
        "Кузница",
        "Перековать вещь",
        f"Перековать: {item.name}",
        goods=goods,
    )
    assert state.pending.character is not None
    assert state.pending.character.gold == battered.gold - price
    taken, given = state.pending.items
    assert taken == (SPARE, -1)
    made = gear_procgen.parse_gear_id(given[0])
    assert made is not None and made[:3] == ("medium_body", 5, "uncommon")
    assert made[3] != 0, "оттиск сменился"
    assert content.item(given[0]).name in state.notice


def test_the_forge_refuses_to_reforge_without_the_money(
    content: GameContent, battered: Character
) -> None:
    poor = replace(battered, gold=1)
    goods = Goods(gold=poor.gold, owned=(OwnedItem(SPARE, 1),))
    state = _step(
        content,
        poor,
        begin(poor),
        "Мир",
        "Кузница",
        "Перековать вещь",
        f"Перековать: {content.item(SPARE).name}",
        goods=goods,
    )
    assert state.pending.character is None
    assert "Работа стоит" in state.notice
