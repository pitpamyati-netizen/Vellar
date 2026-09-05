"""Расы, классы и справочник умений."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import GameContent, SkillKind
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS
from mmorpg.domain.rules.progression import MAX_LEVEL
from mmorpg.domain.rules.skill_effects import (
    cleansed_count,
    spec_for,
)
from mmorpg.infrastructure.content.loader import (
    ACTIVES_PER_CLASS,
    FORKS_PER_CLASS,
    PASSIVES_PER_CLASS,
)

RACE_STAT_BUDGET = 3


def test_sixteen_races(content: GameContent) -> None:
    assert len(content.races) == 16


def test_race_stat_budget(content: GameContent) -> None:
    """Положительных очков не больше 3, плюс по одному за каждое очко штрафа."""
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
        # Ровно одно активное умение на расу, чтобы единственный расовый слот никогда не
        # был двусмысленным.
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


def test_each_class_has_its_actives_and_passives(content: GameContent) -> None:
    """Сорок четыре умения на класс, и на четырёх уровнях из двадцати - развилка.

    Боевых написано 24, а изучить можно 20: четыре пары стоят на одном месте
    каждая, и взявший одно закрывает второе (ADR 0024). Пассивных двадцать -
    вчетверо меньше прежнего и вдвое весомее каждое: сорок строк «плюс четыре
    процента» были не развитием, а налогом на очко.
    """
    for klass in content.classes:
        owner = f"class:{klass.id}"
        actives = content.skills_of(owner, SkillKind.ACTIVE)
        passives = content.skills_of(owner, SkillKind.PASSIVE)
        assert len(actives) == ACTIVES_PER_CLASS, klass.id
        assert len(passives) == PASSIVES_PER_CLASS, klass.id
        forks = {skill.fork for skill in actives if skill.fork}
        assert len(forks) == FORKS_PER_CLASS, klass.id
        for fork in forks:
            pair = [skill for skill in actives if skill.fork == fork]
            assert len(pair) == 2, fork
            assert pair[0].level == pair[1].level, fork


def test_class_unlock_levels_match_the_rules(content: GameContent) -> None:
    rules = content.rules
    for klass in content.classes:
        owner = f"class:{klass.id}"
        actives = sorted(content.skills_of(owner, SkillKind.ACTIVE), key=lambda s: s.level)
        passives = sorted(content.skills_of(owner, SkillKind.PASSIVE), key=lambda s: s.level)
        expected = tuple(sorted((*rules.active_unlock_levels, *rules.fork_levels)))
        assert tuple(skill.level for skill in actives) == expected
        assert tuple(skill.level for skill in passives) == rules.passive_unlock_levels


def test_panel_size_is_fixed(content: GameContent) -> None:
    """Панель не растёт никогда: 6 боевых и 1 расовый. См. docs/skills.md.

    Пассивных слотов нет вовсе: изученное пассивное умение работает, и выбор
    делается только из боевых.
    """
    rules = content.rules
    assert (rules.active_slots, rules.racial_slots) == (6, 1)
    # Выбор есть всегда: шесть слотов из двадцати боевых умений.
    assert rules.active_slots < ACTIVES_PER_CLASS


def test_skill_codes_and_names_are_unique(content: GameContent) -> None:
    codes = [skill.code for skill in content.skills]
    assert len(codes) == len(set(codes))
    # Имена не повторяются внутри владельца: боевой экран ведёт по тексту кнопки.
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
    """Боевое умение на каждые пятнадцать уровней, а на уровне развилки - пара."""
    rules = content.rules
    unlocked_at_1 = content.class_skills_up_to("warrior", 1, SkillKind.ACTIVE)
    assert len(unlocked_at_1) == 1
    seen = 0
    for level in rules.active_unlock_levels:
        seen += 2 if level in rules.fork_levels else 1
        assert len(content.class_skills_up_to("warrior", level, SkillKind.ACTIVE)) == seen
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
    """Очко, вложенное в ранг, обязано что-то менять - и не одним числом.

    Прежде ранг прибавлял пятнадцатую долю силы и больше ничего, а у умений «да
    или нет» - «Исчезновение», «Юркость», «Отсрочка», «По памяти» - силе было
    некуда лечь вовсе. Теперь предельный ранг у каждого боевого умения короче
    откатом, длиннее сроками и дешевле разом (ADR 0067).
    """
    top_rank = content.rules.max_rank
    gain = skill_rules.rank_gain(top_rank)
    assert gain.changes_anything
    assert gain.cooldown_cut >= 1
    assert gain.duration_bonus >= 1
    assert gain.cost_factor < 1.0

    # И сила: у боевого умения доля роста объявлена в содержимом, и она не ноль.
    # У «Очищения» сила ложится в число снятых бед, и его считают отдельно.
    idle = [
        skill.code
        for skill in content.skills
        if skill.is_active
        and skill.power_at_rank(1) == skill.power_at_rank(top_rank)
        and cleansed_count(spec_for(skill.effect), skill.power_at_rank(1))
        == cleansed_count(spec_for(skill.effect), skill.power_at_rank(top_rank))
    ]
    assert not idle, idle


def test_a_rank_costs_one_point_and_the_tree_costs_more_than_the_road(
    content: GameContent,
) -> None:
    """Дерево дороже дохода: выучить всё нельзя, и «что взять» - вопрос.

    Ранг стоит одно очко, очко приходит через уровень (ADR 0067), и этих двух
    чисел довольно, чтобы за всю полосу не набралось даже на половину дерева.
    """
    rules = content.rules
    assert rules.rank_cost == 1
    assert rules.levels_per_skill_point == 2
    income = rules.skill_points_at(MAX_LEVEL)
    # Сорок изучаемых мест: двадцать боевых и двадцать пассивных.
    places = (ACTIVES_PER_CLASS - FORKS_PER_CLASS) + PASSIVES_PER_CLASS
    assert income < places * rules.full_rank_cost() / 2
