"""Прочность снаряжения и кузница, которая её возвращает (ADR 0057)."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.character import Equipment, ItemWear
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules import economy as shop_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import repair as repair_rules
from mmorpg.domain.rules import tools as tool_rules
from mmorpg.domain.rules.stats import derived_stats

SWORD = "sword@6#common"
PLATE = "heavy_body@6#common"
PICK = "pick@1#common"


@pytest.fixture
def knight(content: GameContent) -> Character:
    """Тот, у кого в руке меч, а на теле латы, - и всё новое."""
    return Character(
        id=1,
        user_id=42,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=10,
        gold=400,
        equipment=Equipment(MappingProxyType({"weapon": SWORD, "body": PLATE})),
    )


def worn(character: Character, item_id: str, spent: int) -> Character:
    return replace(character, wear=ItemWear(MappingProxyType({item_id: spent})))


# --- сколько её вообще ------------------------------------------------


def test_durability_grows_with_the_level_of_the_thing(content: GameContent) -> None:
    early = content.item("sword@1#common")
    late = content.item("sword@45#common")
    assert repair_rules.limit(late) > repair_rules.limit(early)


def test_durability_grows_with_the_rarity_of_the_thing(content: GameContent) -> None:
    plain = content.item("sword@6#common")
    named = content.item("sword@6#legendary")
    assert repair_rules.limit(named) > repair_rules.limit(plain)


def test_a_thing_that_is_not_worn_has_no_durability(content: GameContent) -> None:
    """Расходники и сырьё не стачиваются: точит их не бой, а употребление."""
    assert repair_rules.limit(content.item("small_healing_potion")) == 0


# --- что её точит -----------------------------------------------------


def test_a_fight_wears_everything_that_is_on(content: GameContent, knight: Character) -> None:
    after, broke = repair_rules.wear(content, knight, repair_rules.WEAR_PER_FIGHT)
    assert after.wear.spent(SWORD) == repair_rules.WEAR_PER_FIGHT
    assert after.wear.spent(PLATE) == repair_rules.WEAR_PER_FIGHT
    assert broke == ()


def test_a_defeat_wears_three_times_as_much(content: GameContent, knight: Character) -> None:
    lost = adventure.resolve_defeat(content, knight)
    assert lost.character.wear.spent(SWORD) == repair_rules.WEAR_ON_DEFEAT
    assert lost.broken == ()


def test_the_tool_is_worn_by_its_own_work_and_not_by_fights(
    content: GameContent, knight: Character
) -> None:
    """Два счёта на одну вещь разошлись бы: инструмент точат сборы (ADR 0056)."""
    digger = replace(knight, equipment=knight.equipment.equip(tool_rules.TOOL_SLOT, PICK))
    after, _ = repair_rules.wear(content, digger, repair_rules.WEAR_ON_DEFEAT)
    assert after.wear.spent(PICK) == 0


def test_a_thing_worn_to_the_end_is_named_once_and_wears_no_further(
    content: GameContent, knight: Character
) -> None:
    limit = repair_rules.limit(content.item(SWORD))
    tired = worn(knight, SWORD, limit - 1)
    after, broke = repair_rules.wear(content, tired, 1)
    assert [item.id for item in broke] == [SWORD]

    again, broke_again = repair_rules.wear(content, after, 1)
    assert broke_again == (), "сломанное не ломается дважды"
    assert again.wear.spent(SWORD) == limit, "и дальше не точится"


# --- что делает сломанная вещь ----------------------------------------


def test_a_broken_armour_holds_nothing(content: GameContent, knight: Character) -> None:
    whole = derived_stats(content, knight).armor
    broken = derived_stats(content, worn(knight, PLATE, 10_000)).armor
    assert broken < whole


def test_a_broken_sword_strikes_as_a_fist(content: GameContent, knight: Character) -> None:
    armed = gear.weapon_dice(content, knight)
    empty = gear.weapon_dice(content, worn(knight, SWORD, 10_000))
    assert gear.weapon_of(content, worn(knight, SWORD, 10_000)) is None
    assert empty.average < armed.average


def test_a_broken_thing_stays_on_the_character(content: GameContent, knight: Character) -> None:
    """Игра не снимает вещь сама: снять её или починить - решение игрока."""
    broken = worn(knight, PLATE, 10_000)
    assert broken.equipment.item_in("body") == PLATE
    assert [item.id for item in repair_rules.broken_on(content, broken)] == [PLATE]


# --- почём чинят ------------------------------------------------------


def test_a_whole_thing_costs_nothing_and_is_not_billed(
    content: GameContent, knight: Character
) -> None:
    assert repair_rules.bill(content, knight) == ()
    assert repair_rules.price_of(knight, content.item(SWORD)) == 0


def test_the_price_follows_the_wear(content: GameContent, knight: Character) -> None:
    limit = repair_rules.limit(content.item(SWORD))
    half = repair_rules.price_of(worn(knight, SWORD, limit // 2), content.item(SWORD))
    whole = repair_rules.price_of(worn(knight, SWORD, limit), content.item(SWORD))
    assert 0 < half < whole


def test_the_price_follows_level_and_rarity(content: GameContent, knight: Character) -> None:
    plain = content.item("sword@6#common")
    named = content.item("sword@6#rare")
    late = content.item("sword@45#common")
    spent_plain = repair_rules.price_of(worn(knight, plain.id, repair_rules.limit(plain)), plain)
    spent_named = repair_rules.price_of(worn(knight, named.id, repair_rules.limit(named)), named)
    spent_late = repair_rules.price_of(worn(knight, late.id, repair_rules.limit(late)), late)
    assert spent_plain < spent_named
    assert spent_plain < spent_late


def test_repair_is_always_cheaper_than_a_new_thing(content: GameContent, knight: Character) -> None:
    """Починка дороже вещи - это не починка, а лавка."""
    for item_id in (SWORD, PLATE, "sword@45#legendary"):
        item = content.item(item_id)
        price = repair_rules.price_of(worn(knight, item_id, repair_rules.limit(item)), item)
        assert price < shop_rules.buy_price(content, item)


def test_the_bill_names_every_worn_thing_and_adds_up(
    content: GameContent, knight: Character
) -> None:
    battered = replace(knight, wear=ItemWear(MappingProxyType({SWORD: 5, PLATE: 10_000})))
    entries = repair_rules.bill(content, battered)
    assert [item.id for item, _ in entries] == [SWORD, PLATE]
    assert repair_rules.total(entries) == sum(price for _, price in entries)


def test_repair_gives_the_whole_thing_back(content: GameContent, knight: Character) -> None:
    battered = replace(knight, wear=ItemWear(MappingProxyType({SWORD: 5, PLATE: 10_000})))
    entries = repair_rules.bill(content, battered)
    fixed = repair_rules.repaired(battered, [item for item, _ in entries])
    assert fixed.wear.used == {}
    assert repair_rules.broken_on(content, fixed) == ()
    assert derived_stats(content, fixed).armor == derived_stats(content, knight).armor
