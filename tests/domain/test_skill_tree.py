"""Дерево умений: цена ранга, ветви, развилки и то, что дерево дороже дохода.

Здесь проверяется единственное обещание ADR 0024: к трёхсотому уровню выучить
всё нельзя, и то, что выучено, - это выбор, а не расписание. Раньше шестьдесят
умений по пять рангов сходились ровно с тремя сотнями очков, и два воина одного
уровня были неразличимы.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities import Character, GameContent, SkillKind, SkillLoadout
from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.stats import skill_point_allowance

# --- цена ------------------------------------------------------------


def test_a_rank_costs_more_the_higher_it_stands(content: GameContent, warrior: Character) -> None:
    skill = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)[0]
    rich = replace(warrior, unspent_skill_points=99)
    costs = []
    for _ in range(content.rules.max_rank):
        costs.append(skill_rules.cost_to_learn(content, rich, skill))
        rich = skill_rules.learn(content, rich, skill)
        assert rich is not None
    assert costs == sorted(costs), costs
    assert costs[-1] > costs[0], "предельный ранг обязан стоить дороже первого"
    assert skill_rules.cost_to_learn(content, rich, skill) == 0
    assert skill_rules.spent_on(content, rich, skill.code) == content.rules.full_rank_cost()


def test_the_tree_costs_more_than_three_hundred_levels_pay(content: GameContent) -> None:
    """Главное число замысла: дерево дороже дохода, поэтому «выучить всё» нельзя."""
    earned = skill_point_allowance(content, content.rules.max_character_level)
    for klass in content.classes:
        owner = f"class:{klass.id}"
        actives = content.skills_of(owner, SkillKind.ACTIVE)
        passives = content.skills_of(owner, SkillKind.PASSIVE)
        # Развилка даёт очкам одно место, а не два.
        places = len({skill.fork or skill.code for skill in actives}) + len(passives)
        full = places * content.rules.full_rank_cost()
        assert full > earned, klass.id
        # И не настолько дороже, чтобы выбор стал незаметным: около половины дерева.
        assert earned / full > 0.4, klass.id


# --- ветви -----------------------------------------------------------


def first_of(content: GameContent, branch: ActionTag, level: int = 300):
    return next(
        skill
        for skill in content.class_skills_up_to("warrior", level, SkillKind.PASSIVE)
        if skill.branch is branch and skill_rules.tier_of(content, skill) == 1
    )


def deep_in(content: GameContent, branch: ActionTag, tier: int = 4):
    return next(
        skill
        for skill in content.class_skills_up_to("warrior", 300, SkillKind.PASSIVE)
        if skill.branch is branch and skill_rules.tier_of(content, skill) == tier
    )


def below_tier(content: GameContent, branch: ActionTag, tier: int):
    """Всё, во что можно вложить очко в этой ветви ниже названной ступени.

    И боевые, и пассивные: гейт считает вложенное в ветвь целиком, поэтому одними
    пассивными его на четвёртой ступени не набрать.
    """
    return [
        skill
        for kind in (SkillKind.ACTIVE, SkillKind.PASSIVE)
        for skill in content.class_skills_up_to("warrior", 300, kind)
        if skill.branch is branch and skill_rules.tier_of(content, skill) < tier
    ]


def spend_to_gate(
    content: GameContent, character: Character, branch: ActionTag, gate: int
) -> Character:
    """Вложить в ветвь ровно столько, чтобы ступень открылась, и ни очком больше.

    Ни очком больше - это важно: вложив вдвое, разобрать одно умение можно было бы
    безнаказанно, и проверка гейта на разборе ничего бы не доказала.
    """
    for skill in below_tier(content, branch, 4):
        while skill_rules.branch_points(content, character)[branch] < gate:
            raised = skill_rules.learn(content, character, skill)
            if raised is None:
                break
            character = raised
        if skill_rules.branch_points(content, character)[branch] >= gate:
            break
    return character


def test_every_class_skill_names_its_branch(content: GameContent) -> None:
    nameless = [
        skill.code
        for skill in content.skills
        if skill.owner_kind.value == "class" and skill.branch is None
    ]
    assert not nameless


def test_a_locked_tier_refuses_the_point(content: GameContent, warrior: Character) -> None:
    veteran = replace(warrior, level=300, unspent_skill_points=99)
    late = deep_in(content, ActionTag.PRESS)
    assert skill_rules.gate_of(content, late) > 0
    assert not skill_rules.gate_met(content, veteran, late)
    assert skill_rules.learn(content, veteran, late) is None


def test_points_spent_in_a_branch_open_it(content: GameContent, warrior: Character) -> None:
    veteran = replace(warrior, level=300, unspent_skill_points=999)
    late = deep_in(content, ActionTag.PRESS)
    gate = skill_rules.gate_of(content, late)

    veteran = spend_to_gate(content, veteran, ActionTag.PRESS, gate)

    assert skill_rules.branch_points(content, veteran)[ActionTag.PRESS] >= gate
    assert skill_rules.gate_met(content, veteran, late)
    opened = skill_rules.learn(content, veteran, late)
    assert opened is not None and skill_rules.is_known(opened, late.code)


def test_a_branch_is_counted_only_by_its_own_skills(
    content: GameContent, warrior: Character
) -> None:
    guard = first_of(content, ActionTag.GUARD)
    student = replace(warrior, level=300, unspent_skill_points=9)
    learned = skill_rules.learn(content, student, guard)
    assert learned is not None
    tally = skill_rules.branch_points(content, learned)
    assert tally[ActionTag.GUARD] == content.rules.rank_cost(1)
    assert tally[ActionTag.PRESS] == 0
    assert tally[ActionTag.PRECISION] == 0


def test_taking_a_branch_apart_below_its_gate_is_refused(
    content: GameContent, warrior: Character
) -> None:
    """Иначе гейт - это пошлина: набрал дешёвых, открыл верх, разобрал дешёвые."""
    veteran = replace(warrior, level=300, unspent_skill_points=999)
    late = deep_in(content, ActionTag.PRESS)
    veteran = spend_to_gate(content, veteran, ActionTag.PRESS, skill_rules.gate_of(content, late))
    veteran = skill_rules.learn(content, veteran, late) or veteran
    assert skill_rules.is_known(veteran, late.code)

    # То, на чём ступень и стоит: без этого умения вложенного в ветвь станет меньше гейта.
    propped = max(
        below_tier(content, ActionTag.PRESS, 4),
        key=lambda skill: skill_rules.spent_on(content, veteran, skill.code),
    )
    assert skill_rules.undercuts_branch(content, veteran, propped)
    assert skill_rules.forget(content, veteran, propped) is None
    assert propped not in skill_rules.forgettable(content, veteran)


# --- развилки --------------------------------------------------------


def forked(content: GameContent):
    return next(
        skill
        for skill in content.class_skills_up_to("warrior", 300, SkillKind.ACTIVE)
        if skill.fork
    )


def test_a_fork_holds_exactly_one_rival(content: GameContent) -> None:
    skill = forked(content)
    rivals = skill_rules.fork_rivals(content, skill)
    assert len(rivals) == 1
    assert rivals[0].level == skill.level
    assert rivals[0].branch is skill.branch


def test_taking_one_side_of_a_fork_closes_the_other(
    content: GameContent, warrior: Character
) -> None:
    skill = forked(content)
    rival = skill_rules.fork_rivals(content, skill)[0]
    veteran = replace(
        warrior,
        level=300,
        unspent_skill_points=999,
        loadout=SkillLoadout(ranks={skill.code: 1}),
    )
    assert skill_rules.fork_taken(content, veteran, rival) is skill
    assert not skill_rules.learnable(content, veteran, rival)
    assert skill_rules.learn(content, veteran, rival) is None
    # А своё умение поднимать по-прежнему можно: развилка закрывает соперника.
    assert skill_rules.learn(content, veteran, skill) is not None


def test_a_skill_outside_a_fork_argues_with_nobody(content: GameContent) -> None:
    plain = next(
        skill
        for skill in content.class_skills_up_to("warrior", 300, SkillKind.ACTIVE)
        if not skill.fork
    )
    assert skill_rules.fork_rivals(content, plain) == ()


# Что делает предельный ранг в бою, проверяет
# ``test_combat.test_mastery_returns_the_skill_a_turn_earlier``: там есть настоящий
# бой, а здесь только дерево.
