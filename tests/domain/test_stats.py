"""Счёт итоговых характеристик: чистый, воспроизводимый, никогда не двойной."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import (
    ActiveEffect,
    Character,
    EffectStack,
    Equipment,
    GameContent,
    SkillLoadout,
    StatBlock,
    StatCode,
)
from mmorpg.domain.rules.stats import (
    derived_stats,
    primary_stats,
    skill_point_allowance,
    stat_allowance,
)


def test_primary_stats_sum_every_source(content: GameContent, warrior: Character) -> None:
    stats = primary_stats(content, warrior)
    base = content.rules.base_stat_value
    race = content.race("human").bonuses
    klass = content.character_class("warrior").bonuses
    assert stats[StatCode.STR] == base + race.STR + klass.STR
    assert stats[StatCode.LCK] == base + race.LCK + klass.LCK


def test_allocated_points_are_added(content: GameContent, warrior: Character) -> None:
    before = primary_stats(content, warrior)[StatCode.STR]
    stronger = replace(warrior, allocated=StatBlock(STR=5))
    assert primary_stats(content, stronger)[StatCode.STR] == before + 5


def test_traits_contribute_flat_stat_bonuses(content: GameContent, warrior: Character) -> None:
    before = primary_stats(content, warrior)[StatCode.LCK]
    lucky = replace(warrior, trait_ids=("born_lucky",))
    after = primary_stats(content, lucky)
    assert after[StatCode.LCK] == before + 2
    # born_lucky стоит ещё и очка выносливости.
    assert after[StatCode.END] == primary_stats(content, warrior)[StatCode.END] - 1


def test_computation_is_pure(content: GameContent, warrior: Character) -> None:
    first = derived_stats(content, warrior)
    second = derived_stats(content, warrior)
    assert first == second


def test_effects_do_not_double_apply(content: GameContent, warrior: Character) -> None:
    """Главное правило идемпотентности: заново применённый эффект не удваивает прибавку."""
    effect = ActiveEffect(
        id="rally", name="Клич сплочения", modifiers={"health_percent": 20.0}, turns_left=3
    )
    once = EffectStack().apply(effect)
    twice = once.apply(effect)
    assert len(twice) == 1
    assert derived_stats(content, warrior, once) == derived_stats(content, warrior, twice)


def test_reapplying_refreshes_the_duration(content: GameContent, warrior: Character) -> None:
    effect = ActiveEffect(id="rally", name="Клич", modifiers={"damage_percent": 25.0}, turns_left=3)
    stack = EffectStack().apply(effect).tick()
    refreshed = stack.apply(effect)
    assert next(iter(refreshed)).turns_left == 3


def test_health_percent_effect_raises_max_health(content: GameContent, warrior: Character) -> None:
    plain = derived_stats(content, warrior)
    buffed = derived_stats(
        content,
        warrior,
        EffectStack().apply(
            ActiveEffect(id="b", name="b", modifiers={"health_percent": 50.0}, turns_left=2)
        ),
    )
    assert buffed.max_health > plain.max_health
    assert buffed.max_health == pytest.approx(round(plain.max_health * 1.5), abs=1)


def test_equipment_modifiers_apply(content: GameContent, warrior: Character) -> None:
    # Взят уровень, чтобы несколько процентов брони были больше, чем след округления.
    veteran = replace(warrior, level=50, allocated=StatBlock(END=40))
    plain = derived_stats(content, veteran)
    armored = derived_stats(
        content, replace(veteran, equipment=Equipment({"body": "cloth_body@1#common"}))
    )
    assert armored.armor > plain.armor


def test_known_passives_apply_at_their_rank(content: GameContent, warrior: Character) -> None:
    veteran = replace(warrior, level=50, allocated=StatBlock(END=40))
    plain = derived_stats(content, veteran)
    with_passive = replace(
        veteran,
        loadout=SkillLoadout(ranks={"warrior_privychka_k_latam": 1}),
    )
    ranked = replace(
        veteran,
        loadout=SkillLoadout(ranks={"warrior_privychka_k_latam": 5}),
    )
    assert derived_stats(content, with_passive).armor > plain.armor
    assert derived_stats(content, ranked).armor > derived_stats(content, with_passive).armor


def test_every_known_passive_counts(content: GameContent, warrior: Character) -> None:
    """Изучено - значит работает: слотов у пассивных умений больше нет.

    Три слота на шесть изученных означали, что половина потраченных очков не
    считалась ни в одном бою, и узнать об этом было неоткуда.
    """
    # На пятидесятом: у первого уровня броня десять, и три процента от неё
    # округляются в ту же десятку - проверять было бы нечего.
    veteran = replace(warrior, level=50, allocated=StatBlock(END=40))
    plain = derived_stats(content, veteran)
    one = replace(veteran, loadout=SkillLoadout(ranks={"warrior_zakalka": 5}))
    every = replace(
        veteran,
        loadout=SkillLoadout(ranks={"warrior_zakalka": 5, "warrior_privychka_k_latam": 5}),
    )
    assert derived_stats(content, one).max_health > plain.max_health
    assert derived_stats(content, every).armor > derived_stats(content, one).armor


def test_level_raises_health_and_resource(content: GameContent, warrior: Character) -> None:
    low = derived_stats(content, warrior)
    high = derived_stats(content, replace(warrior, level=50))
    assert high.max_health > low.max_health
    assert high.max_resource > low.max_resource


def test_resource_name_comes_from_the_class(content: GameContent, warrior: Character) -> None:
    assert derived_stats(content, warrior).resource_name == "Отвага"
    mage = Character(id=2, user_id=1, name="М", race_id="high_elf", class_id="mage")
    assert derived_stats(content, mage).resource_name == "Чары"


def test_dodge_and_crit_are_capped(content: GameContent) -> None:
    """Ни одна сборка не доходит до 100 процентов уклонения; потолок держит бой разрешимым."""
    halfling = Character(
        id=3,
        user_id=1,
        name="Х",
        race_id="halfling",
        class_id="rogue",
        level=300,
        allocated=StatBlock(AGI=600, LCK=600),
        trait_ids=("evasive", "born_lucky"),
    )
    stats = derived_stats(content, halfling)
    assert stats.dodge <= 75.0
    assert stats.crit_chance <= 75.0


def test_point_allowances(content: GameContent) -> None:
    rules = content.rules
    assert stat_allowance(content, 1) == rules.free_points_at_creation
    assert (
        stat_allowance(content, 10)
        == rules.free_points_at_creation + rules.stat_points_per_level * 9
    )
    assert skill_point_allowance(content, 1) == 0
    assert skill_point_allowance(content, 10) == rules.skill_point_per_level * 9
