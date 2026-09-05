"""Дерево умений: цена ранга, что ранг даёт, развилки и доход против дерева.

Здесь проверяется обещание ADR 0067: ранг стоит одно очко, очко приходит через
уровень, и потому к сто пятидесятому выучить всё нельзя - выученное это выбор, а
не расписание. Ветвей и граней в дереве больше нет: ветви вернутся талантами,
а грани были 256 подписями, за которыми движок ничего не делал.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities import Character, GameContent, SkillKind, SkillLoadout
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.stats import skill_point_allowance

# --- цена ------------------------------------------------------------


def test_every_rank_costs_the_same_single_point(content: GameContent, warrior: Character) -> None:
    """Ранг стоит одно очко, любой: за очко он платит не ценой, а делом."""
    skill = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)[0]
    rich = replace(warrior, unspent_skill_points=99)
    costs = []
    for _ in range(content.rules.max_rank):
        costs.append(skill_rules.cost_to_learn(content, rich, skill))
        rich = skill_rules.learn(content, rich, skill)
        assert rich is not None
    assert costs == [content.rules.rank_cost] * content.rules.max_rank
    assert skill_rules.cost_to_learn(content, rich, skill) == 0
    assert skill_rules.spent_on(content, rich, skill.code) == content.rules.full_rank_cost()


def test_a_point_comes_every_other_level(content: GameContent) -> None:
    """«Каждые два уровня» - это деление, и на нечётных оно не врёт."""
    step = content.rules.levels_per_skill_point
    assert step == 2
    assert skill_point_allowance(content, 1) == 0
    assert skill_point_allowance(content, 2) == 1
    assert skill_point_allowance(content, 3) == 1
    assert skill_point_allowance(content, 4) == 2
    top = content.rules.max_character_level
    assert skill_point_allowance(content, top) == top // step


def test_the_tree_costs_more_than_the_whole_road_pays(content: GameContent) -> None:
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


def test_the_mentor_returns_exactly_what_was_spent(
    content: GameContent, warrior: Character
) -> None:
    skill = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)[0]
    rich = replace(warrior, unspent_skill_points=10)
    for _ in range(3):
        raised = skill_rules.learn(content, rich, skill)
        assert raised is not None
        rich = raised
    left = rich.unspent_skill_points
    forgotten = skill_rules.forget(content, rich, skill)
    assert forgotten is not None
    assert forgotten.unspent_skill_points == left + 3 * content.rules.rank_cost
    assert not skill_rules.is_known(forgotten, skill.code)


# --- что даёт ранг ---------------------------------------------------


def test_the_first_rank_gains_nothing_and_the_last_gains_all_three() -> None:
    """Ранг меняет откат, сроки и цену - иначе очко в него потрачено впустую."""
    first = skill_rules.rank_gain(1)
    assert not first.changes_anything
    assert (first.cooldown_cut, first.duration_bonus, first.cost_factor) == (0, 0, 1.0)

    top = skill_rules.rank_gain(5)
    assert top.cooldown_cut == 2
    assert top.duration_bonus == 2
    assert top.cost_factor < first.cost_factor


def test_rank_gains_never_fall_as_the_rank_rises() -> None:
    steps = [skill_rules.rank_gain(rank) for rank in range(1, 6)]
    assert [one.cooldown_cut for one in steps] == sorted(one.cooldown_cut for one in steps)
    assert [one.duration_bonus for one in steps] == sorted(one.duration_bonus for one in steps)
    assert [one.cost_factor for one in steps] == sorted(
        (one.cost_factor for one in steps), reverse=True
    )


def test_a_cooldown_never_falls_below_nothing(content: GameContent) -> None:
    instant = next(skill for skill in content.skills if skill.cooldown == 0)
    assert skill_rules.cooldown_at_rank(instant, content.rules.max_rank) == 0


def test_rank_stretches_what_the_skill_leaves_but_not_what_takes_a_turn() -> None:
    """Лишний ход оглушения бой не разменивает, а кончает - его ранг не трогает."""
    from mmorpg.domain.entities.statuses import StatusKind
    from mmorpg.domain.rules.skill_effects import spec_for

    burning = spec_for("damage_burn")
    stretched = skill_rules.at_rank(burning, 5)
    assert stretched.dot_turns == burning.dot_turns + 2

    freezing = spec_for("damage_freeze")
    held = skill_rules.at_rank(freezing, 5)
    frozen = next(one for one in held.inflicts if one.kind is StatusKind.FREEZE)
    was = next(one for one in freezing.inflicts if one.kind is StatusKind.FREEZE)
    assert frozen.turns == was.turns


def test_the_first_rank_leaves_the_effect_exactly_as_written() -> None:
    from mmorpg.domain.rules.skill_effects import spec_for

    spec = spec_for("damage_burn")
    assert skill_rules.at_rank(spec, 1) is spec


# --- развилки --------------------------------------------------------


def forked(content: GameContent):
    return next(
        skill
        for skill in content.class_skills_up_to("warrior", 150, SkillKind.ACTIVE)
        if skill.fork
    )


def test_a_fork_holds_exactly_one_rival(content: GameContent) -> None:
    skill = forked(content)
    rivals = skill_rules.fork_rivals(content, skill)
    assert len(rivals) == 1
    assert rivals[0].level == skill.level


def test_taking_one_side_of_a_fork_closes_the_other(
    content: GameContent, warrior: Character
) -> None:
    skill = forked(content)
    rival = skill_rules.fork_rivals(content, skill)[0]
    veteran = replace(
        warrior,
        level=150,
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
        for skill in content.class_skills_up_to("warrior", 150, SkillKind.ACTIVE)
        if not skill.fork
    )
    assert skill_rules.fork_rivals(content, plain) == ()


# Что ранг делает в настоящем бою, проверяет ``test_combat``: там есть очередь,
# откаты и запас, а здесь только дерево.
