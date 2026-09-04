"""Полоса в сто пятьдесят уровней и то, чем она держится (ADR 0058).

Здесь проверяется не формула, а обещание: что растёт у героя, дойдя до потолка,
что не упирается в стену по дороге и во что игре обходится золото. Числа берутся
те же, что видит игрок, - через `derived_stats`, `gold_at`, `buy_price`.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, StatBlock
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.procgen.enemies import gold_at
from mmorpg.domain.rules import economy
from mmorpg.domain.rules import stats as stat_rules
from mmorpg.domain.rules.combat import skill_cost
from mmorpg.domain.rules.progression import (
    MAX_LEVEL,
    experience_reward,
    experience_to_next_level,
)

#: Точки замера по всей полосе, а не только там, где случается играть.
LEVELS = (1, 10, 40, 90, 150)


def grown(content: GameContent, warrior: Character, level: int, focus: StatCode) -> Character:
    """Герой этого уровня, вложивший все свои очки в одну характеристику."""
    points = stat_rules.stat_allowance(content, level)
    return replace(warrior, level=level, allocated=StatBlock.from_mapping({focus.value: points}))


# --- полоса ----------------------------------------------------------


def test_the_band_ends_at_a_hundred_and_fifty(content: GameContent) -> None:
    assert MAX_LEVEL == 150
    assert content.rules.max_character_level == MAX_LEVEL
    assert experience_to_next_level(MAX_LEVEL) == 0


def test_a_level_costs_more_fights_than_the_one_before(content: GameContent) -> None:
    """Кривая растёт, но не обгоняет плату за бой настолько, чтобы встать стеной."""
    fights = [
        experience_to_next_level(level)
        / experience_reward(enemy_level=level, character_level=level)
        for level in LEVELS[:-1]
    ]
    assert fights == sorted(fights)
    # Первый уровень - несколько схваток, последний - несколько десятков, и нигде
    # между ними не сотни.
    assert 3 <= fights[0] <= 6
    assert fights[-1] <= 60


# --- характеристики --------------------------------------------------


def test_every_stat_grows_even_unspent(content: GameContent, warrior: Character) -> None:
    """Непрофильная характеристика растёт сама - иначе пять из семи мертвы (ADR 0058)."""
    low = stat_rules.primary_stats(content, grown(content, warrior, 1, StatCode.STR))
    high = stat_rules.primary_stats(content, grown(content, warrior, MAX_LEVEL, StatCode.STR))
    assert high[StatCode.LCK] > low[StatCode.LCK] * 5
    # Розданное всё равно решает больше: фокус обгоняет то, что пришло само.
    assert high[StatCode.STR] > high[StatCode.LCK] * 3


def test_crit_and_dodge_keep_paying_and_never_hit_the_wall(
    content: GameContent, warrior: Character
) -> None:
    """Убывающая отдача: каждое очко прибавляет, но следующее - меньше."""
    crits = [
        stat_rules.derived_stats(content, grown(content, warrior, level, StatCode.LCK)).crit_chance
        for level in LEVELS
    ]
    assert crits == sorted(crits)
    assert crits[0] < crits[-1]
    assert crits[-1] < stat_rules.MAX_CRIT_CHANCE, "потолок сборки не берётся одной удачей"
    steps = [after - before for before, after in itertools.pairwise(crits)]
    assert steps[-1] < steps[0], "поздние очки обязаны стоить дороже ранних"

    dodges = [
        stat_rules.derived_stats(content, grown(content, warrior, level, StatCode.AGI)).dodge
        for level in LEVELS
    ]
    assert dodges == sorted(dodges)
    assert dodges[-1] < stat_rules.MAX_DODGE


def test_softening_never_reaches_its_ceiling() -> None:
    assert stat_rules.softened(0, 100, 50) == 0
    assert stat_rules.softened(-5, 100, 50) == 0
    assert stat_rules.softened(100, 100, 50) == pytest.approx(25)
    assert stat_rules.softened(10**6, 100, 50) < 50


# --- запас -----------------------------------------------------------


def test_a_skill_costs_the_same_share_at_both_ends(
    content: GameContent, warrior: Character
) -> None:
    """Цена умения - доля запаса, поэтому она значит одно и то же на всей полосе."""
    skill = next(one for one in content.skills if one.owner == "class:warrior" and one.cost > 0)
    shares = []
    for level in (1, MAX_LEVEL):
        hero = grown(content, warrior, level, StatCode.END)
        pool = stat_rules.derived_stats(content, hero).max_resource
        price = skill_cost(skill, pool)
        assert price >= 1
        shares.append(price / pool)
    assert shares[0] == pytest.approx(shares[1], abs=0.02)


# --- золото ----------------------------------------------------------


def test_a_fight_pays_less_than_the_level_grows(content: GameContent) -> None:
    """Плата растёт медленнее уровня: боёв на уровень и так становится больше."""
    assert gold_at(1) < gold_at(MAX_LEVEL)
    assert gold_at(MAX_LEVEL) < gold_at(1) * MAX_LEVEL / 10


def test_city_services_are_measured_in_fights(content: GameContent) -> None:
    for level in LEVELS:
        fight = gold_at(level)
        assert economy.inn_price(level) == pytest.approx(fight * economy.INN_FIGHTS, abs=1)
        assert economy.travel_price(level, 1) == pytest.approx(fight * economy.TRAVEL_FIGHTS, abs=1)
        assert economy.mentor_price(level) == pytest.approx(fight * economy.MENTOR_FIGHTS, abs=1)


def test_a_level_of_fighting_buys_about_a_thing_of_its_grade(content: GameContent) -> None:
    """Главный договор экономики: доход и цены не разъезжаются ни на каком уровне."""
    for level in LEVELS:
        tier = gear_procgen.tier_at(content, level)
        assert tier is not None
        item = content.item(gear_procgen.gear_id("sword", tier.level, "common"))
        # На потолке следующего уровня уже нет - меряется последний, который есть.
        counted = min(level, MAX_LEVEL - 1)
        fights = experience_to_next_level(counted) / experience_reward(
            enemy_level=counted, character_level=counted
        )
        earned_gold = fights * gold_at(level)
        things = earned_gold / economy.buy_price(content, item)
        assert 0.5 <= things <= 2.5, f"на {level} уровне за уровень выходит {things:.1f} вещи"
