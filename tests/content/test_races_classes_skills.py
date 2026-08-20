"""Races, classes and the skill catalogue."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import GameContent, SkillKind

RACE_STAT_BUDGET = 3


def test_sixteen_races(content: GameContent) -> None:
    assert len(content.races) == 16


def test_race_stat_budget(content: GameContent) -> None:
    """Positive points are capped at 3, plus one extra per point of penalty."""
    for race in content.races:
        allowance = RACE_STAT_BUDGET + race.bonuses.penalty_total
        assert race.bonuses.positive_total <= allowance, race.id
        assert race.bonuses.total <= RACE_STAT_BUDGET, race.id


def test_every_race_has_one_passive_and_one_active(content: GameContent) -> None:
    for race in content.races:
        assert race.passive.name, race.id
        assert race.passive.text, race.id
        active = content.racial_active(race.id)
        assert active.kind is SkillKind.ACTIVE
        assert active.owner == f"race:{race.id}"
        # Exactly one active per race, so the single racial slot is never ambiguous.
        assert len(content.skills_of(f"race:{race.id}", SkillKind.ACTIVE)) == 1


#: Корень русского названия каждой характеристики - по нему проверяют, что строка
#: ``power`` говорит именно о той характеристике, от которой движок считает удар.
STAT_ROOTS = {
    "STR": "сил",
    "AGI": "ловкост",
    "END": "выносливост",
    "INT": "интеллект",
    "WIS": "мудрост",
    "CHA": "харизм",
    "LCK": "удач",
}


def test_every_class_says_what_its_blow_grows_from(content: GameContent) -> None:
    """``power`` обязан называть ту характеристику, от которой считается удар.

    Экран характеристик показывает эту строку прямо над списком очков, и она
    единственная отвечает на вопрос «почему ключевая — именно эта». Строка,
    разошедшаяся с ``key_stats[0]``, врёт ровно там, где игрок решает, куда
    вкладывать: до этой проверки экран объяснял силу словами «урон в бою, когда
    класс дерётся силой».
    """
    for klass in content.classes:
        assert klass.power, klass.id
        assert klass.key_stats, klass.id
        root = STAT_ROOTS[klass.key_stats[0].value]
        assert root in klass.power.casefold(), (
            f"{klass.id}: удар растёт от {klass.key_stats[0].value}, "
            f"а строка power об этом молчит: {klass.power!r}"
        )


def test_eight_classes(content: GameContent) -> None:
    assert len(content.classes) == 8


def test_each_class_has_eight_actives_and_six_passives(content: GameContent) -> None:
    for klass in content.classes:
        owner = f"class:{klass.id}"
        assert len(content.skills_of(owner, SkillKind.ACTIVE)) == 8, klass.id
        assert len(content.skills_of(owner, SkillKind.PASSIVE)) == 6, klass.id


def test_class_unlock_levels_match_the_rules(content: GameContent) -> None:
    rules = content.rules
    for klass in content.classes:
        owner = f"class:{klass.id}"
        actives = sorted(content.skills_of(owner, SkillKind.ACTIVE), key=lambda s: s.level)
        passives = sorted(content.skills_of(owner, SkillKind.PASSIVE), key=lambda s: s.level)
        assert tuple(skill.level for skill in actives) == rules.active_unlock_levels
        assert tuple(skill.level for skill in passives) == rules.passive_unlock_levels


def test_panel_size_is_fixed(content: GameContent) -> None:
    """The panel never grows: 6 active and 1 racial. See docs/skills.md.

    Постоянных слотов нет вовсе: изученное постоянное умение работает, и выбор
    делается только из боевых.
    """
    rules = content.rules
    assert (rules.active_slots, rules.racial_slots) == (6, 1)
    # There is always a real choice to make: 6 of 8 actives.
    assert rules.active_slots < 8


def test_every_skill_has_exactly_two_edges(content: GameContent) -> None:
    for skill in content.skills:
        assert len(skill.edges) == 2, skill.code
        names = {edge.name for edge in skill.edges}
        assert len(names) == 2, skill.code
        codes = {edge.code for edge in skill.edges}
        assert codes == {f"{skill.code}_a", f"{skill.code}_b"}


def test_skill_codes_and_names_are_unique(content: GameContent) -> None:
    codes = [skill.code for skill in content.skills]
    assert len(codes) == len(set(codes))
    # Names must be unique per owner: the combat screen routes by button text.
    for owner in {skill.owner for skill in content.skills}:
        names = [skill.name for skill in content.skills_of(owner)]
        assert len(names) == len(set(names)), owner


def test_skill_catalogue_size(content: GameContent) -> None:
    """8 classes x 14 skills + 16 racial actives."""
    assert len(content.skills) == 8 * 14 + 16


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_power_grows_with_rank(content: GameContent, rank: int) -> None:
    skill = content.skill("warrior_cleave")
    expected = skill.power * (1 + skill.rank_step * (rank - 1))
    assert skill.power_at_rank(rank) == pytest.approx(expected)
    assert skill.power_at_rank(rank) >= skill.power


def test_active_skills_cost_and_target_are_sane(content: GameContent) -> None:
    for skill in content.skills:
        if skill.kind is not SkillKind.ACTIVE:
            continue
        assert skill.cost >= 0, skill.code
        assert skill.cooldown >= 0, skill.code
        assert skill.target in {"self", "enemy", "all_enemies"}, skill.code
        assert skill.power > 0, skill.code


def test_class_skills_unlock_progressively(content: GameContent) -> None:
    unlocked_at_1 = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)
    unlocked_at_100 = content.class_skills_up_to("warrior", 100, SkillKind.ACTIVE)
    assert len(unlocked_at_1) == 1
    assert len(unlocked_at_100) == 8
