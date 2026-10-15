"""Карточка считает подтверждённые ранги дерева, как настоящий бой."""

from __future__ import annotations

import re
from dataclasses import replace

from mmorpg.domain.entities import Character, SkillLoadout
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.rules import talents
from mmorpg.domain.rules.combat import hero_combatant
from mmorpg.presentation.telegram.screens.combat import skill_effect


def test_two_ranks_of_a_talent_are_both_present_in_damage_preview(content):
    character = Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id="warrior",
        level=15,
        loadout=SkillLoadout(actives=("warrior_sekushchiy_roscherk", None, None, None, None, None)),
    )
    once = talents.choose(content, character, "warrior_path_1_1_a")
    assert once is not None
    twice = talents.choose(content, once, "warrior_path_1_1_a")
    assert twice is not None
    assert talents.spent(content, twice) == 2
    # Большая постоянная часть отличает ошибочные 2% от настоящих 4%
    # независимо от округления оружия и первичных характеристик.
    skill = replace(
        content.skill("warrior_sekushchiy_roscherk"),
        dice=Dice(1, 1, 10000),
        power=100,
    )
    actor = hero_combatant(content, character, combatant_id=1, side=0)
    plain = re.search(r"от (\d+) до (\d+)", skill_effect(content, character, actor, skill))
    ranked = re.search(r"от (\d+) до (\d+)", skill_effect(content, twice, actor, skill))
    assert plain is not None and ranked is not None
    assert int(ranked[1]) == round(int(plain[1]) * 1.04)
    assert int(ranked[2]) == round(int(plain[2]) * 1.04)
