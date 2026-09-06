"""Новое имя и наследие (ADR 0070).

Проверяется то, ради чего уход переписан: он теперь ОТБИРАЕТ — раздачу очков, —
платит за это процентом навсегда, и наследие есть ответ ровно на ту потерю,
которую уход наносит.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import milestones as milestone_rules
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.domain.rules.stats import primary_stats


def hero(*, level: int = 1, remorts: int = 0, **allocated: int) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id="warrior",
        level=level,
        remorts=remorts,
        allocated=StatBlock(**allocated),
    )


# --- содержимое -------------------------------------------------------


def test_three_steps_that_cost_more_each_time(content: GameContent) -> None:
    """Уровень кончается трижды, и каждый раз дороже предыдущего."""
    steps = content.rebirths
    assert [one.rank for one in steps] == [1, 2, 3]
    assert [one.level for one in steps] == [75, 100, 150]
    for earlier, later in itertools.pairwise(steps):
        assert later.stat_bonus > earlier.stat_bonus
        assert later.legacy_slots >= earlier.legacy_slots
        assert later.stat_points > earlier.stat_points


def test_a_step_that_gives_nothing_is_refused_by_the_loader(content: GameContent) -> None:
    """Уход стирает полторы сотни уровней и обязан за это платить."""
    for one in content.rebirths:
        assert one.stat_bonus > 0, one.id
        assert one.text, one.id


def test_the_unlock_list_matches_what_the_subclasses_ask(content: GameContent) -> None:
    """Два места говорят об одном и не расходятся: это сверяет загрузчик."""
    for step in content.rebirths:
        promised = {one.id for one in content.subclasses if one.gate.remorts == step.rank}
        assert set(step.unlocks) == promised, step.id


# --- уход -------------------------------------------------------------


def test_the_first_name_is_asked_for_at_seventy_five(content: GameContent) -> None:
    """Отказ называет и порог, и нынешний уровень."""
    green = hero(level=40)
    refused = turning_rules.refusal(content, green)
    assert "75" in refused and "40" in refused
    assert turning_rules.become(content, green) is None

    ready = hero(level=75)
    assert turning_rules.refusal(content, ready) == ""
    assert turning_rules.become(content, ready) is not None


def test_a_name_resets_the_allocation_and_pays_for_it(content: GameContent) -> None:
    """Розданное возвращается нерозданным — вот что делает уход уходом."""
    grown = hero(level=75, STR=200, END=100)
    reborn = turning_rules.become(content, grown)
    assert reborn is not None
    after = reborn.character
    assert after.level == 1
    assert after.experience == 0
    assert after.allocated == StatBlock()
    step = content.rebirth_at(1)
    assert step is not None
    assert after.unspent_stat_points == content.rules.free_points_at_creation + step.stat_points
    assert after.remorts == 1


def test_what_a_name_never_takes(content: GameContent) -> None:
    """Золото, вещи, дерево умений, ремёсла и ступени остаются при игроке."""
    rich = replace(
        hero(level=75, STR=200),
        gold=5000,
        bank_gold=1000,
        unspent_skill_points=12,
        trait_ids=("berserker",),
        house_id="stone",
        subclass_ids=("warrior_vanguard",),
    )
    reborn = turning_rules.become(content, rich)
    assert reborn is not None
    after = reborn.character
    assert after.gold == 5000
    assert after.bank_gold == 1000
    assert after.unspent_skill_points == 12
    assert after.trait_ids == ("berserker",)
    assert after.house_id == "stone"
    assert after.subclass_ids == ("warrior_vanguard",)
    assert after.loadout == rich.loadout


def test_the_bonus_is_the_step_you_stand_on(content: GameContent) -> None:
    """Проценты не складываются: у второй ступени написано, каким ты стал после неё."""
    assert turning_rules.stat_bonus(content, hero()) == 0
    first = content.rebirth_at(1)
    second = content.rebirth_at(2)
    assert first is not None and second is not None
    assert turning_rules.stat_bonus(content, hero(remorts=1)) == first.stat_bonus
    assert turning_rules.stat_bonus(content, hero(remorts=2)) == second.stat_bonus
    # Не сумма: второе имя стоит своих процентов, а не первых плюс своих.
    assert turning_rules.stat_bonus(content, hero(remorts=2)) < first.stat_bonus + second.stat_bonus


def test_the_bonus_lifts_the_stats_themselves(content: GameContent) -> None:
    """Прибавка входит и в итоговые числа, и в вехи — её считает одно место."""
    plain = hero(level=40, STR=100)
    reborn = replace(plain, remorts=1)
    assert (
        primary_stats(content, reborn)[StatCode.STR] > primary_stats(content, plain)[StatCode.STR]
    )
    assert (
        milestone_rules.invested_stats(content, reborn)[StatCode.STR]
        > milestone_rules.invested_stats(content, plain)[StatCode.STR]
    )


def test_the_bonus_reaches_a_milestone_the_first_run_could_not(content: GameContent) -> None:
    """Второй проход дотягивается туда, куда первый не дотянулся, — в этом и смысл."""
    # Ровно под порогом на первом проходе: сотня очков силы не доносит до сотой вехи.
    short = hero(level=40, STR=60, END=60)
    first = {one.name for one in milestone_rules.reached(content, short)}
    reborn = {one.name for one in milestone_rules.reached(content, replace(short, remorts=2))}
    assert first < reborn, f"{sorted(first)} against {sorted(reborn)}"


def test_titles_grow_and_then_stop(content: GameContent) -> None:
    assert turning_rules.title(content, 0) == ""
    assert turning_rules.title(content, 1) == content.rebirth_titles[0]
    assert turning_rules.title(content, 99) == content.rebirth_titles[-1]


def test_the_last_step_closes_the_book(content: GameContent) -> None:
    """Пройдя все ступени, игрок слышит об этом, а не видит молчащую кнопку."""
    done = hero(level=150, remorts=len(content.rebirths))
    assert turning_rules.next_rebirth(content, done) is None
    assert "прошли все ступени" in turning_rules.refusal(content, done)
    assert turning_rules.become(content, done) is None


def test_a_character_who_outlived_the_content_does_not_fall(content: GameContent) -> None:
    """Уходивший больше раз, чем ступеней в файлах, не роняет экран."""
    stranger = hero(level=150, remorts=99)
    assert turning_rules.current_rebirth(content, stranger) is None
    assert turning_rules.stat_bonus(content, stranger) == 0
    assert turning_rules.legacy_slots(content, stranger) == 0


# --- наследие ---------------------------------------------------------


def test_legacy_carries_only_milestones_you_actually_took(content: GameContent) -> None:
    """Назвать можно взятое, и только его."""
    strong = hero(level=75, STR=300)
    may = turning_rules.may_carry(content, strong)
    assert may
    assert turning_rules.carry(content, strong, "nope") is None
    carried = turning_rules.carry(content, strong, may[0])
    assert carried is not None
    assert carried.legacy_ids == (may[0],)
    # Дважды одну и ту же — нельзя.
    assert turning_rules.carry(content, carried, may[0]) is None


def test_legacy_fits_the_slots_of_the_step_ahead(content: GameContent) -> None:
    """Слотов столько, сколько даёт следующая ступень, и ни одним больше."""
    strong = hero(level=75, STR=300, END=200)
    slots = turning_rules.legacy_slots(content, strong)
    assert slots == 1
    filled = strong
    for trait_id in turning_rules.may_carry(content, strong)[: slots + 1]:
        stepped = turning_rules.carry(content, filled, trait_id)
        if stepped is not None:
            filled = stepped
    assert len(filled.legacy_ids) == slots


def test_a_named_milestone_keeps_working_after_the_reset(content: GameContent) -> None:
    """Ровно то, ради чего наследие заведено."""
    strong = hero(level=75, STR=300)
    kept = turning_rules.may_carry(content, strong)[0]
    named = turning_rules.carry(content, strong, kept)
    assert named is not None

    reborn = turning_rules.become(content, named)
    assert reborn is not None
    after = reborn.character
    # Порогом она больше не держится: розданное вернулось.
    assert kept not in milestone_rules.trait_ids(content, after)
    # А наследием — держится, и прибавки её в общем свёртке.
    assert kept in turning_rules.legacy_trait_ids(content, after)
    bundle = mods.collect_modifiers(content, after)
    for key, value in content.trait(kept).modifiers.items():
        assert bundle.get(key, 0.0) != 0.0 or value == 0.0


def test_a_milestone_is_never_counted_twice(content: GameContent) -> None:
    """Веха, которая держится и порогом, и наследием, прибавляет один раз."""
    strong = hero(level=75, STR=300)
    kept = turning_rules.may_carry(content, strong)[0]
    named = replace(strong, legacy_ids=(kept,))
    assert kept in milestone_rules.trait_ids(content, named)
    assert kept not in turning_rules.legacy_trait_ids(content, named)


def test_legacy_can_be_taken_back_before_the_name(content: GameContent) -> None:
    """Пока уход не совершён, названное можно переменить."""
    strong = hero(level=75, STR=300)
    kept = turning_rules.may_carry(content, strong)[0]
    named = turning_rules.carry(content, strong, kept)
    assert named is not None
    dropped = turning_rules.drop(named, kept)
    assert dropped is not None
    assert dropped.legacy_ids == ()
    assert turning_rules.drop(dropped, kept) is None


def test_legacy_is_trimmed_to_the_slots_of_the_step_taken(content: GameContent) -> None:
    """Названное сверх слотов не уходит: содержимое могло поправиться."""
    strong = replace(
        hero(level=75, STR=300, END=200),
        legacy_ids=("ms_warrior_grip", "ms_warrior_breach", "ms_warrior_footing"),
    )
    reborn = turning_rules.become(content, strong)
    assert reborn is not None
    assert len(reborn.character.legacy_ids) == reborn.step.legacy_slots
    assert reborn.carried == reborn.step.legacy_slots


def test_a_legacy_whose_trait_is_gone_gives_nothing(content: GameContent) -> None:
    """Содержимое переживает персонажа (``Claude.md``, правило 8)."""
    ghost = replace(hero(level=75), legacy_ids=("nope",))
    assert turning_rules.legacy_trait_ids(content, ghost) == ()
    assert mods.legacy_modifiers(content, ghost) == {}


@pytest.mark.parametrize("remorts", [0, 1, 2, 3])
def test_the_bonus_never_makes_a_stat_smaller(content: GameContent, remorts: int) -> None:
    plain = hero(level=60, STR=120, END=80)
    lifted = milestone_rules.invested_stats(content, replace(plain, remorts=remorts))
    base = milestone_rules.invested_stats(content, plain)
    for code, value in base:
        assert lifted[code] >= value
