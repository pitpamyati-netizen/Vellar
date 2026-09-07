"""Выучка умения: ранг спрашивает, чему умение научилось (ADR 0079).

Ранг и прежде что-то давал - силу, откат, сроки, цену, - но всё это были числа,
и очко, вложенное в третий ранг, ничем не отличалось от очка, вложенного во
второй. Теперь третий ранг и пятый спрашивают, ЧЕМУ УМЕНИЕ НАУЧИЛОСЬ, и выбор
меняет само действие: удар начинает бить по всем, лечение ложится на отряд,
помеха расходится по стае.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import skill_mastery as mastery_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for

#: Обычный одноцелевой удар и лечение отряда - два края, на которых видно, что
#: выучки предлагаются по форме умения, а не всем подряд.
STRIKE = "warrior_sekushchiy_roscherk"
BLEED = "warrior_rassechenie"


@pytest.fixture
def hero(warrior: Character) -> Character:
    return replace(warrior, level=100, unspent_skill_points=10)


def ranked(character: Character, code: str, rank: int) -> Character:
    return replace(character, loadout=character.loadout.with_rank(code, rank))


# --- когда спрашивают -------------------------------------------------


def test_the_first_ranks_ask_nothing(content: GameContent, hero: Character) -> None:
    """Первый и второй ранг ничего не спрашивают: игрок ещё копит."""
    skill = content.skill(STRIKE)
    for rank in (1, 2):
        assert skill_rules.mastery_tier_due(content, ranked(hero, STRIKE, rank), skill) is None


def test_the_third_and_the_fifth_rank_ask(content: GameContent, hero: Character) -> None:
    """Третий ранг спрашивает первую ступень, пятый - вторую."""
    skill = content.skill(STRIKE)
    third = ranked(hero, STRIKE, 3)
    assert skill_rules.mastery_tier_due(content, third, skill) == 1

    taught = skill_rules.choose_mastery(
        content, third, skill, skill_rules.mastery_choices(content, third, skill)[0].code
    )
    assert taught is not None
    assert skill_rules.mastery_tier_due(content, taught, skill) is None

    fifth = ranked(taught, STRIKE, 5)
    assert skill_rules.mastery_tier_due(content, fifth, skill) == 2


def test_an_unknown_skill_is_not_asked(content: GameContent, hero: Character) -> None:
    """Неизученному умению выучек не предлагают: учить нечего."""
    assert skill_rules.mastery_tier_due(content, hero, content.skill(STRIKE)) is None


def test_a_passive_is_never_asked(content: GameContent, hero: Character) -> None:
    """У пассивки нет ни хода, ни цели: менять в её действии нечего (ADR 0073)."""
    passive = next(one for one in content.skills if not one.is_active)
    ready = ranked(hero, passive.code, 5)
    assert skill_rules.mastery_tier_due(content, ready, passive) is None


# --- что предлагают ---------------------------------------------------


def test_every_active_skill_has_something_to_learn(content: GameContent) -> None:
    """Всякому боевому умению есть что предложить на обеих ступенях.

    Ранг, который спрашивает и не предлагает ничего, - это очко, потраченное в
    пустоту: выучка обязана найтись у каждого умения.
    """
    empty = []
    for skill in content.skills:
        if not skill.is_active:
            continue
        spec = spec_for(skill.effect)
        for tier in (1, 2):
            if not mastery_rules.offered(spec, tier):
                empty.append((skill.code, tier))
    assert not empty, empty


def test_a_choice_is_a_choice(content: GameContent) -> None:
    """Из одной выучки не выбирают: на каждой ступени их хотя бы две."""
    thin = []
    for skill in content.skills:
        if not skill.is_active:
            continue
        spec = spec_for(skill.effect)
        for tier in (1, 2):
            if len(mastery_rules.offered(spec, tier)) < 2:
                thin.append((skill.code, tier))
    assert not thin, thin


def test_healing_is_not_offered_a_blow(content: GameContent) -> None:
    """«Пробой» не предлагают лечению, «Разлив» - удару: выучка знает форму."""
    heal = next(
        one
        for one in content.skills
        if one.is_active and spec_for(one.effect).category is EffectCategory.HEAL
    )
    codes = {
        one.code for tier in (1, 2) for one in mastery_rules.offered(spec_for(heal.effect), tier)
    }
    assert "pierce" not in codes
    assert "spread" in codes

    blow = spec_for(content.skill(STRIKE).effect)
    assert "spread" not in {one.code for one in mastery_rules.offered(blow, 2)}
    assert "pierce" in {one.code for one in mastery_rules.offered(blow, 1)}


# --- что выучка делает ------------------------------------------------


def test_a_mastery_changes_the_spec(content: GameContent) -> None:
    """Выучка правит то самое описание, по которому бой и считает."""
    spec = spec_for(content.skill(STRIKE).effect)
    assert not spec.aoe

    wide = mastery_rules.applied(spec, ("wide",))
    assert wide.aoe
    assert wide.damage_scale < spec.damage_scale

    deep = mastery_rules.applied(spec, ("pierce",))
    assert deep.pierce == pytest.approx(mastery_rules.PIERCE_SHARE)


def test_masteries_stack_in_a_fixed_order(content: GameContent) -> None:
    """Две выучки складываются, и порядок их взятия ничего не решает."""
    spec = spec_for(content.skill(STRIKE).effect)
    one = mastery_rules.applied(spec, ("pierce", "wide"))
    other = mastery_rules.applied(spec, ("wide", "pierce"))
    assert one == other
    assert one.aoe and one.pierce


def test_a_forgotten_mastery_does_not_crash(content: GameContent) -> None:
    """Выучка, которой в игре не осталось, просто не работает (правило 8)."""
    spec = spec_for(content.skill(STRIKE).effect)
    assert mastery_rules.applied(spec, ("нет такой",)) == spec


def test_the_cheap_hand_halves_the_cost(content: GameContent) -> None:
    """«Лёгкая рука» - пометка, и цену по ней считает бой."""
    spec = mastery_rules.applied(spec_for(content.skill(STRIKE).effect), ("cheap",))
    assert mastery_rules.MARK_CHEAP in spec.marks
    assert mastery_rules.cost_factor(spec) == pytest.approx(mastery_rules.CHEAP_FACTOR)
    assert mastery_rules.cost_factor(spec_for(content.skill(STRIKE).effect)) == 1.0


def test_the_long_trail_does_not_stretch_control(content: GameContent) -> None:
    """«Долгий след» тянет всё, кроме того, что отнимает ход.

    Лишний ход оглушения бой не разменивает, а кончает, - то же правило, что у
    ранга (``rules/skills``).
    """
    stunning = spec_for("debuff_stun")
    stretched = mastery_rules.applied(stunning, ("linger",))
    assert stretched.inflicts[0].turns == stunning.inflicts[0].turns

    burning = spec_for("damage_burn")
    longer = mastery_rules.applied(burning, ("linger",))
    assert longer.dot_turns == burning.dot_turns + mastery_rules.LINGER_TURNS


def test_a_deep_wound_ticks_harder(content: GameContent) -> None:
    """«Глубокая рана» усиливает то, что точит цель, а не сам удар."""
    spec = spec_for("damage_bleed")
    deeper = mastery_rules.applied(spec, ("deepen",))
    assert deeper.dot_scale == pytest.approx(spec.dot_scale * mastery_rules.DEEPEN_SCALE)
    assert deeper.damage_scale == spec.damage_scale


# --- как её берут -----------------------------------------------------


def test_a_mastery_is_taken_once_and_kept(content: GameContent, hero: Character) -> None:
    """Взятое не меняют: выбор ветки и выучки одного рода (ADR 0074)."""
    skill = content.skill(BLEED)
    ready = ranked(hero, BLEED, 3)
    first = skill_rules.mastery_choices(content, ready, skill)[0]
    taught = skill_rules.choose_mastery(content, ready, skill, first.code)
    assert taught is not None
    assert skill_rules.masteries_of(taught, skill) == (first.code,)

    # Второй раз ту же ступень не выбирают: выбирать уже нечего.
    assert skill_rules.mastery_choices(content, taught, skill) == ()
    assert skill_rules.choose_mastery(content, taught, skill, first.code) is None


def test_a_mastery_outside_the_offer_is_refused(content: GameContent, hero: Character) -> None:
    """Берут только из предложенного: «Разлив» удару не достаётся."""
    skill = content.skill(STRIKE)
    ready = ranked(hero, STRIKE, 3)
    assert skill_rules.choose_mastery(content, ready, skill, "spread") is None


def test_forgetting_a_skill_forgets_its_mastery(content: GameContent, hero: Character) -> None:
    """Разобрав умение, наставник забирает и то, чему оно было научено."""
    skill = content.skill(BLEED)
    ready = ranked(hero, BLEED, 3)
    taught = skill_rules.choose_mastery(
        content, ready, skill, skill_rules.mastery_choices(content, ready, skill)[0].code
    )
    assert taught is not None
    forgotten = skill_rules.forget(content, taught, skill)
    assert forgotten is not None
    assert skill_rules.masteries_of(forgotten, skill) == ()


def test_waiting_skills_are_named(content: GameContent, hero: Character) -> None:
    """Ранг, купленный и не потраченный на выбор, называется вслух."""
    ready = ranked(hero, BLEED, 3)
    waiting = skill_rules.waiting_for_mastery(content, ready)
    assert [one.code for one in waiting] == [BLEED]


def test_every_mastery_actually_changes_something(content: GameContent) -> None:
    """Выучка, ничего не меняющая в описании, — это подпись, а не механика.

    Каждая проверяется на том умении, которому она подходит: правка либо видна в
    числах описания, либо оставляет пометку, которую читает бой.
    """
    specs = {skill.effect: spec_for(skill.effect) for skill in content.skills if skill.is_active}
    for one in mastery_rules.MASTERIES:
        suited = [spec for spec in specs.values() if one.suits(spec)]
        assert suited, one.code
        assert any(one.change(spec) != spec for spec in suited), one.code
