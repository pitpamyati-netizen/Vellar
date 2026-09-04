"""Кости и сборка снаряжения: два места, где в игре появляются числа.

Урон и броня перестали быть долями чего-то и стали числами (ADR 0015). Здесь
проверяется, что числа эти честные: кости читаются и бросаются в своих границах,
вещь собирается из вида, ступени и редкости одинаково у всех, а реликтовое падает
только с хозяина логова.
"""

from __future__ import annotations

import random

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.dice import MAX_SPREAD, Dice
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.procgen import items as gear_procgen

# --- кости -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2d6", Dice(2, 6)),
        ("1d14", Dice(1, 14)),
        ("2d8+3", Dice(2, 8, 3)),
        ("1d4-1", Dice(1, 4, -1)),
        (" 3 d 10 ", Dice(3, 10)),
    ],
)
def test_dice_are_read_the_way_they_are_written(text: str, expected: Dice) -> None:
    assert Dice.parse(text) == expected


@pytest.mark.parametrize("text", ["", "d6", "2d", "две кости", "2x6", "0d6", "2d0"])
def test_nonsense_is_a_refusal_and_not_a_silent_zero(text: str) -> None:
    with pytest.raises(ValueError, match=r"dice|at least"):
        Dice.parse(text)


def test_a_roll_stays_inside_its_own_bounds() -> None:
    dice = Dice(3, 12, 4)
    source = random.Random(20260820)
    rolls = [dice.roll(source) for _ in range(500)]
    assert min(rolls) >= dice.low
    assert max(rolls) <= dice.high
    assert dice.low <= sum(rolls) / len(rolls) <= dice.high


def test_the_same_seed_rolls_the_same_number() -> None:
    dice = Dice.parse("4d9+2")
    assert dice.roll(random.Random(7)) == dice.roll(random.Random(7))


def test_dice_are_spoken_and_never_spelled() -> None:
    """Экранный диктор читает «2d6» как «два дэ шесть». Это не речь."""
    assert Dice.parse("2d6").spoken() == "от 2 до 12"
    assert "d" not in Dice.parse("2d6").spoken()


def test_growing_keeps_the_average_it_promised() -> None:
    for text in ("1d16", "2d6", "2d8", "1d11"):
        dice = Dice.parse(text)
        for factor in (1.0, 10.0, 40.0, 111.6):
            grown = dice.scaled(factor)
            assert grown.average == pytest.approx(dice.average * factor, rel=0.03), (text, factor)


def test_growing_keeps_the_character_of_the_kind() -> None:
    """Булава бьёт вразброс и на трёхсотом уровне, меч — ровно."""
    mace = Dice.parse("1d16").scaled(111.6, spread=1.5)
    sword = Dice.parse("2d6").scaled(111.6, spread=1.2)
    assert (mace.high - mace.low) / mace.average > (sword.high - sword.low) / sword.average


# --- потолок размаха -------------------------------------------------


@pytest.mark.parametrize("text", ["1d16", "2d6", "2d8", "1d11", "2d5", "1d14", "1d3"])
@pytest.mark.parametrize("spread", [1.15, 1.2, 1.3, 1.4, 1.5])
def test_the_top_of_a_blow_is_never_more_than_half_again_the_bottom(
    text: str, spread: float
) -> None:
    """Полтора — потолок всей игры, на любой ступени и у любого рода.

    До этого размах задавали сами кости, и он плыл вверх вместе с вещью: «2d5+1»
    на первой ступени били вразброс втрое, а на трёхсотой — вдесятеро, и «от 146
    до 1404» не было числом, по которому можно решать (ADR 0017).
    """
    dice = Dice.parse(text)
    for level in (1, 2, 5, 30, 100, 300):
        grown = dice.scaled(1.0 + 0.37 * (level - 1), spread=spread)
        assert grown.low >= 1
        assert grown.high <= round(grown.low * MAX_SPREAD), (level, str(grown))


def test_a_kind_may_not_ask_for_more_spread_than_the_game_allows() -> None:
    """Потолок держится движком, а не доброй волей содержимого."""
    reckless = Dice.parse("2d6").scaled(50.0, spread=9.0)
    assert reckless.spread <= MAX_SPREAD


def test_every_weapon_kind_in_the_game_stays_under_the_ceiling(content: GameContent) -> None:
    for kind in content.weapon_types:
        assert kind.spread <= MAX_SPREAD, kind.id
        for level in (1, 12, 60, 150, 300):
            damage = kind.damage_at(1.0 + gear_procgen.FACES_PER_LEVEL * (level - 1))
            assert damage.high <= round(damage.low * MAX_SPREAD), (kind.id, level, str(damage))


def test_growing_never_turns_a_blow_into_a_coin_flip() -> None:
    """Одна кость на тысячу граней — это не удар, это подбрасывание монеты."""
    grown = Dice.parse("1d16").scaled(111.6)
    assert grown.count > 1
    assert grown.low / grown.average > 0.005


# --- сборка вещи -----------------------------------------------------


def test_a_gear_id_reads_back_the_way_it_was_written() -> None:
    item_id = gear_procgen.gear_id("sword", 24, "rare")
    assert gear_procgen.parse_gear_id(item_id) == ("sword", 24, "rare")


@pytest.mark.parametrize("item_id", ["wolf_pelt", "sword@45", "sword#rare", "", "sword@ноль#rare"])
def test_what_is_not_assembled_gear_says_so(item_id: str) -> None:
    assert gear_procgen.parse_gear_id(item_id) is None


def test_every_kind_exists_on_every_grade_in_every_rarity(content: GameContent) -> None:
    expected = len(content.gear_archetypes) * len(content.gear_tiers) * len(content.rarities)
    assembled = [item for item in content.items if gear_procgen.parse_gear_id(item.id) is not None]
    assert len(assembled) == expected


def test_gear_is_named_apart_from_everything_else(content: GameContent) -> None:
    """Кнопка в списке — это её текст: два одинаковых имени неразличимы на слух."""
    names = [item.name for item in content.items]
    assert len(set(names)) == len(names)


def test_a_later_grade_costs_more_and_a_rarer_thing_costs_more(content: GameContent) -> None:
    grades = [content.item(f"sword@{tier.level}#common").price for tier in content.gear_tiers]
    assert grades == sorted(grades)
    rarities = [content.item(f"sword@24#{rarity.id}").price for rarity in content.rarities]
    assert rarities == sorted(rarities)


# --- что падает ------------------------------------------------------


def rolls(content: GameContent, rank: EnemyRank, level: int = 24, tries: int = 400) -> list[str]:
    source = random.Random(20260820)
    dropped = [
        gear_procgen.roll_drop(content, source, level=level, rank=rank) for _ in range(tries)
    ]
    return [item_id for item_id in dropped if item_id is not None]


def test_a_boss_always_leaves_something_and_a_wolf_rarely_does(content: GameContent) -> None:
    assert len(rolls(content, EnemyRank.BOSS)) == 400
    assert 0 < len(rolls(content, EnemyRank.NORMAL)) < 200


def test_a_relic_comes_off_a_boss_and_nothing_else(content: GameContent) -> None:
    """Реликтовое не лежит на прилавке и не падает с волка."""
    relics = {rarity.id for rarity in content.rarities if rarity.scaling}

    def relic_count(rank: EnemyRank) -> int:
        return sum(
            1
            for item_id in rolls(content, rank)
            if (parsed := gear_procgen.parse_gear_id(item_id)) and parsed[2] in relics
        )

    assert relic_count(EnemyRank.BOSS) > 0
    assert relic_count(EnemyRank.ELITE) == 0
    assert relic_count(EnemyRank.NORMAL) == 0


def test_what_falls_is_a_thing_the_game_actually_has(content: GameContent) -> None:
    for rank in EnemyRank:
        for item_id in rolls(content, rank, tries=120):
            assert content.has_item(item_id), item_id


def test_what_falls_is_of_the_grade_of_the_one_who_dropped_it(content: GameContent) -> None:
    for level in (1, 24, 150):
        tier = gear_procgen.tier_at(content, level)
        assert tier is not None
        for item_id in rolls(content, EnemyRank.BOSS, level=level, tries=60):
            parsed = gear_procgen.parse_gear_id(item_id)
            assert parsed is not None
            assert parsed[1] == tier.level
