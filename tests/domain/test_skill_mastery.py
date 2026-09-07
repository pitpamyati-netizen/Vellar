"""Выучка умения: ранг спрашивает, чему умение научилось (ADR 0079, 0083).

Ранг и прежде что-то давал - силу, откат, сроки, цену, - но всё это были числа,
и очко, вложенное в третий ранг, ничем не отличалось от очка, вложенного во
второй. Теперь третий ранг и пятый спрашивают, ЧЕМУ УМЕНИЕ НАУЧИЛОСЬ, и выбор
меняет само действие: удар начинает бить по всем, лечение ложится на отряд,
помеха расходится по стае.

И спрашивают СВОИМ СПИСКОМ. Общего набора выучек в игре нет: у каждого боевого
умения свои четыре, написанные под класс, ветку и то, чем это умение занято
(ADR 0083). Два разных умения никогда не покажут один и тот же выбор.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import skill_mastery as mastery_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for

#: Обычный одноцелевой удар и удар, оставляющий кровотечение, - два края, на
#: которых видно, что выучка написана под своё умение, а не взята из общего
#: списка.
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
    assert passive.masteries == ()
    ready = ranked(hero, passive.code, 5)
    assert skill_rules.mastery_tier_due(content, ready, passive) is None


# --- что предлагают ---------------------------------------------------


def test_every_active_skill_has_its_own_four(content: GameContent) -> None:
    """Всякому боевому умению написаны свои четыре: две на ступень.

    Ранг, который спрашивает и не предлагает ничего, - это очко, потраченное в
    пустоту; из одной выучки не выбирают вовсе.
    """
    thin = []
    for skill in content.skills:
        if not skill.is_active:
            continue
        for tier in (1, 2):
            if len(mastery_rules.offered(skill, tier)) != mastery_rules.MASTERIES_PER_TIER:
                thin.append((skill.code, tier))
    assert not thin, thin


def test_no_two_skills_show_the_same_choice(content: GameContent) -> None:
    """Общего списка нет: два умения не показывают один и тот же выбор.

    Ровно это и отличает выучки от прежнего набора на всю игру: имя выучки
    занято одним умением, и услышав его дважды, игрок вправе ждать, что это одно
    и то же.
    """
    said: dict[str, str] = {}
    for skill in content.skills:
        for one in skill.masteries:
            assert one.name not in said, (one.name, skill.code, said.get(one.name))
            said[one.name] = skill.code
    assert len(said) == 4 * sum(1 for one in content.skills if one.is_active)


def test_healing_is_not_offered_a_blow(content: GameContent) -> None:
    """Приём знает форму: «Пробой» не ляжет на лечение, «Разлив» - на удар."""
    heal = next(
        one
        for one in content.skills
        if one.is_active and spec_for(one.effect).category is EffectCategory.HEAL
    )
    ways = {one.way for one in heal.masteries}
    assert "pierce" not in ways

    blow = content.skill(STRIKE)
    assert "spread" not in {one.way for one in blow.masteries}


def test_a_way_that_does_not_suit_is_refused(content: GameContent) -> None:
    """Приём не той формы не годится умению, чем бы его ни назвали."""
    heal = spec_for("heal")
    pierce = next(one for one in mastery_rules.WAYS if one.code == "pierce")
    assert not pierce.suits(heal)
    spread = next(one for one in mastery_rules.WAYS if one.code == "spread")
    assert spread.suits(heal)


# --- что выучка делает ------------------------------------------------


def test_every_written_mastery_changes_something(content: GameContent) -> None:
    """Выучка, ничего не меняющая в описании, - это подпись, а не механика.

    Так же думает и загрузчик: он не заводит игру с такой выучкой. Здесь то же
    самое сказано ещё раз, потому что это и есть весь смысл ADR 0083.
    """
    idle = []
    for skill in content.skills:
        spec = spec_for(skill.effect) if skill.is_active else None
        if spec is None:
            continue
        for one in skill.masteries:
            if mastery_rules.changed(one, spec) == spec:
                idle.append((skill.code, one.code, one.name))
    assert not idle, idle


def test_every_mastery_says_what_it_does(content: GameContent) -> None:
    """Текст пишет сам приём: своего текста у выучки нет вовсе (ADR 0067)."""
    for skill in content.skills:
        for one in skill.masteries:
            said = mastery_rules.words(one)
            assert said and said.endswith("."), (skill.code, one.code)


def test_a_mastery_changes_the_spec(content: GameContent, hero: Character) -> None:
    """Выучка правит то самое описание, по которому бой и считает."""
    skill = content.skill(STRIKE)
    spec = spec_for(skill.effect)
    assert not spec.aoe

    wide = next(one for one in skill.masteries if one.way == "wide")
    assert mastery_rules.changed(wide, spec).aoe

    pierce = next(one for one in skill.masteries if one.way == "pierce")
    assert mastery_rules.changed(pierce, spec).pierce > spec.pierce


def test_masteries_stack_in_a_fixed_order(content: GameContent) -> None:
    """Две выучки складываются, и порядок их взятия ничего не решает."""
    skill = content.skill(STRIKE)
    spec = spec_for(skill.effect)
    codes = (skill.masteries[0].code, skill.masteries[2].code)
    one = mastery_rules.applied(skill, spec, codes)
    other = mastery_rules.applied(skill, spec, tuple(reversed(codes)))
    assert one == other
    assert one != spec


def test_a_forgotten_mastery_does_not_crash(content: GameContent) -> None:
    """Выучка, которой у умения не осталось, просто не работает (правило 8)."""
    skill = content.skill(STRIKE)
    spec = spec_for(skill.effect)
    assert mastery_rules.applied(skill, spec, ("нет такой",)) == spec


def test_the_cheap_hand_halves_the_cost(content: GameContent) -> None:
    """«Подешевле» - пометка, и цену по ней считает бой."""
    skill = next(one for one in content.skills if any(two.way == "cheap" for two in one.masteries))
    cheap = next(one for one in skill.masteries if one.way == "cheap")
    spec = mastery_rules.applied(skill, spec_for(skill.effect), (cheap.code,))
    assert mastery_rules.MARK_CHEAP in spec.marks
    assert mastery_rules.cost_factor(spec) == pytest.approx(mastery_rules.CHEAP_FACTOR)
    assert mastery_rules.cost_factor(spec_for(skill.effect)) == 1.0


def test_a_long_trail_does_not_stretch_control(content: GameContent) -> None:
    """«Долгий след» тянет всё, кроме того, что отнимает ход.

    Лишний ход оглушения бой не разменивает, а кончает, - то же правило, что у
    ранга (``rules/skills``).
    """
    linger = next(one for one in mastery_rules.WAYS if one.code == "linger")
    stunning = spec_for("debuff_stun")
    assert linger.change(stunning, 2).inflicts[0].turns == stunning.inflicts[0].turns

    burning = spec_for("damage_burn")
    assert linger.change(burning, 2).dot_turns == burning.dot_turns + 2


def test_a_deep_wound_ticks_harder(content: GameContent) -> None:
    """«Глубокая рана» усиливает то, что точит цель, а не сам удар."""
    deepen = next(one for one in mastery_rules.WAYS if one.code == "deepen")
    spec = spec_for("damage_bleed")
    deeper = deepen.change(spec, 60)
    assert deeper.dot_scale > spec.dot_scale
    assert deeper.damage_scale == spec.damage_scale


def test_a_second_bundle_never_overwrites_the_first(content: GameContent) -> None:
    """Прибавку, которую умение и так даёт, приём не трогает.

    Бой складывает прибавки в словарь по ключу: вторая с тем же ключом затёрла
    бы первую, и выучка, обещавшая брони, отняла бы у умения его собственную.
    """
    steel = next(one for one in mastery_rules.WAYS if one.code == "steel")
    armored = spec_for("buff_armor")
    assert steel.change(armored, 30) == armored

    counter = next(one for one in mastery_rules.WAYS if one.code == "counter")
    answering = spec_for("buff_counter")
    assert counter.change(answering, 40) == answering


def test_a_status_is_never_hung_twice(content: GameContent) -> None:
    """Второе молчание на молчащем умении - подпись: в бою слышно одно."""
    quiet = next(one for one in mastery_rules.WAYS if one.code == "quiet")
    silencing = spec_for("debuff_silence")
    assert quiet.change(silencing, 0) == silencing


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


def test_a_mastery_of_another_skill_is_refused(content: GameContent, hero: Character) -> None:
    """Берут только своё: выучка пятой ступени на третьем ранге не достаётся."""
    skill = content.skill(STRIKE)
    ready = ranked(hero, STRIKE, 3)
    second = mastery_rules.offered(skill, 2)[0]
    assert skill_rules.choose_mastery(content, ready, skill, second.code) is None


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
