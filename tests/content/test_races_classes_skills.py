"""Races, classes and the skill catalogue."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import GameContent, SkillKind
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS
from mmorpg.domain.rules.skill_effects import (
    EffectCategory,
    cleansed_count,
    recharged,
    spec_for,
)
from mmorpg.infrastructure.content.loader import ACTIVES_PER_CLASS, PASSIVES_PER_CLASS

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


def test_each_class_has_twenty_actives_and_forty_passives(content: GameContent) -> None:
    """Шестьдесят умений на класс, и пассивных вдвое больше боевых.

    Пассивных большинство нарочно: боевое умение - это кнопка, а кнопок в панели
    шесть, и они не растут (``docs/skills.md``). Всё остальное развитие идёт в
    то, что работает изученным.
    """
    for klass in content.classes:
        owner = f"class:{klass.id}"
        actives = content.skills_of(owner, SkillKind.ACTIVE)
        passives = content.skills_of(owner, SkillKind.PASSIVE)
        assert len(actives) == ACTIVES_PER_CLASS, klass.id
        assert len(passives) == PASSIVES_PER_CLASS, klass.id
        assert len(passives) == 2 * len(actives), klass.id


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

    Пассивных слотов нет вовсе: изученное пассивное умение работает, и выбор
    делается только из боевых.
    """
    rules = content.rules
    assert (rules.active_slots, rules.racial_slots) == (6, 1)
    # Выбор есть всегда: шесть слотов из двадцати боевых умений.
    assert rules.active_slots < ACTIVES_PER_CLASS


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
    """8 классов по 60 умений плюс 16 расовых боевых."""
    assert len(content.skills) == 8 * (ACTIVES_PER_CLASS + PASSIVES_PER_CLASS) + 16


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_power_grows_with_rank(content: GameContent, rank: int) -> None:
    skill = content.skill("warrior_rassechenie")
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
    """Ровно по одному боевому умению на каждые пятнадцать уровней."""
    rules = content.rules
    unlocked_at_1 = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)
    assert len(unlocked_at_1) == 1
    for count, level in enumerate(rules.active_unlock_levels, start=1):
        assert len(content.class_skills_up_to("warrior", level, SkillKind.ACTIVE)) == count
    top = content.class_skills_up_to("warrior", 300, SkillKind.ACTIVE)
    assert len(top) == ACTIVES_PER_CLASS


def test_every_passive_points_at_a_modifier_the_engine_reads(content: GameContent) -> None:
    """Пассивное умение обязано что-то делать, а не что-то обещать.

    Пятнадцать из сорока восьми классовых пассивных умений полгода называли
    ключ, которого не считал никто: «физический урон выше», «урон по зверям
    выше», «часть полученного урона возвращается обидчику». Игрок вкладывал в них
    очко и получал строку на экране. Словарь ``traits.toml`` шире того, что
    движок читает, и потому сверяться нужно с ``EFFECTIVE_KEYS``, а не с ним.
    """
    promised = [
        (skill.code, skill.effect)
        for skill in content.skills
        if skill.kind is SkillKind.PASSIVE and skill.effect not in EFFECTIVE_KEYS
    ]
    assert not promised, promised


def test_every_edge_points_at_a_modifier_the_engine_reads(content: GameContent) -> None:
    """И грань тоже: её текст читает игрок, а получает он числа."""
    promised = [
        (skill.code, edge.name, key)
        for skill in content.skills
        for edge in skill.edges
        for bundle in (edge.effect.self_modifiers, edge.effect.target_modifiers)
        for key in bundle
        if key not in EFFECTIVE_KEYS
    ]
    assert not promised, promised


def test_every_racial_passive_does_something(content: GameContent) -> None:
    """Шестнадцать рас, шестнадцать способностей — и все они считаются.

    ``RacePassive`` был идентификатором, именем и текстом: игрок выбирал расу по
    строке, которую движок нигде не читал (``Roadmap.md``, «Что осталось»).
    """
    empty = [race.id for race in content.races if not race.passive.modifiers]
    assert not empty, empty

    promised = [
        (race.id, key)
        for race in content.races
        for key in race.passive.modifiers
        if key not in EFFECTIVE_KEYS
    ]
    assert not promised, promised


def test_no_trait_promises_a_modifier_nobody_counts(content: GameContent) -> None:
    """И у особенностей не осталось ни одного мёртвого ключа.

    Их было десять на всю игру: опыт, плата за задания, находки на событиях,
    доброе имя, уцелевшие материалы, засады, ловушки и три стихийных
    сопротивления. Семь из них теперь считаются, засада и ловушка ушли из
    словаря вместе с механикой, которой у них не было.
    """
    promised = [
        (trait.id, key)
        for trait in content.traits
        for key in trait.modifiers
        if key not in EFFECTIVE_KEYS
    ]
    assert not promised, promised


def test_a_rank_always_changes_something(content: GameContent) -> None:
    """Очко, вложенное в ранг, обязано что-то менять.

    У четырёх умений оно не меняло ничего: «Исчезновение», «Юркость»,
    «Отсрочка» и «По памяти» устроены как «да или нет», и силе ранга там было
    некуда лечь (``Roadmap.md``, «Что осталось»). Теперь ранг у таких умений
    возвращает умение быстрее, а у «Очищения» снимает больше.
    """
    idle: list[str] = []
    for skill in content.skills:
        if not skill.is_active:
            continue
        spec = spec_for(skill.effect)
        first, top = skill.power_at_rank(1), skill.power_at_rank(content.rules.max_rank)
        if spec.recharges:
            if recharged(skill.cooldown, spec, first) == recharged(skill.cooldown, spec, top):
                idle.append(skill.code)
            continue
        if spec.category is EffectCategory.CLEANSE:
            if cleansed_count(spec, first) == cleansed_count(spec, top):
                idle.append(skill.code)
            continue
        if first == top:
            idle.append(skill.code)
    assert not idle, idle
