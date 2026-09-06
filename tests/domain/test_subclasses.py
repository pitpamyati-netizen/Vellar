"""Дерево специализации (ADR 0074).

Проверяется то, ради чего оно заведено: дерево ветвится трижды и остаётся
деревом, ветку оплачивают цепочкой заданий и ничем сверх, взятое складывается,
а вместе с веткой приходит умение, которого у соседа по классу нет.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.content import OwnerKind
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.domain.rules.combat import blow_of
from mmorpg.domain.rules.stats import derived_stats

TIERS = (1, 2, 3)
LEVELS = {1: 30, 2: 75, 3: 150}


def hero(class_id: str = "warrior", *, level: int = 1, **allocated: int) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        allocated=StatBlock(**allocated),
    )


def passed(content: GameContent, character: Character, *subclass_ids: str) -> Character:
    """Тот, у кого испытания названных веток пройдены целиком."""
    done = tuple(quest_id for one in subclass_ids for quest_id in content.subclass(one).trial_ids)
    return replace(character, quests=QuestLog(done=(*character.quests.done, *done)))


# --- содержимое: дерево остаётся деревом ------------------------------


def test_every_class_branches_three_times(content: GameContent) -> None:
    """Класс без ступени - это класс, чей игрок на тридцатом не получит ничего."""
    for klass in content.classes:
        assert content.subclass_tiers(klass.id) == TIERS, klass.id


def test_every_fork_has_two_branches(content: GameContent) -> None:
    """Развилка с одной веткой - коридор с надписью, а не выбор."""
    for klass in content.classes:
        assert len(content.subclass_roots(klass.id)) == 2, klass.id
        for one in content.subclasses_of(klass.id):
            children = content.subclass_children(one.id)
            assert len(children) == (0 if one.tier == 3 else 2), one.id


def test_the_tree_is_the_size_it_promises(content: GameContent) -> None:
    """Восемь корней надвое трижды: 16, 32 и 64."""
    counts = {tier: len([one for one in content.subclasses if one.tier == tier]) for tier in TIERS}
    assert counts == {1: 16, 2: 32, 3: 64}


def test_a_branch_grows_from_a_branch_of_its_own_class(content: GameContent) -> None:
    for one in content.subclasses:
        assert one.level == LEVELS[one.tier], one.id
        if one.tier == 1:
            assert not one.parent, one.id
            continue
        parent = content.subclass(one.parent)
        assert parent.class_id == one.class_id, one.id
        assert parent.tier == one.tier - 1, one.id


def test_every_name_in_the_tree_is_its_own(content: GameContent) -> None:
    """Две ветки под одним именем - это выбор, который игрок не сможет назвать."""
    names = [one.name for one in content.subclasses]
    assert len(names) == len(set(names))


def test_a_branch_changes_something(content: GameContent) -> None:
    """Ветка без прибавок и без правки сетки - худшая из кнопок."""
    for one in content.subclasses:
        assert one.modifiers or one.scaling, one.id
        assert set(one.modifiers) <= mods.EFFECTIVE_KEYS, one.id


def test_a_branch_asks_for_no_stat_at_all(content: GameContent) -> None:
    """Порог характеристики отвечает на «сколько вложил», а ветка - на «кем стал»."""
    for one in content.subclasses:
        assert not hasattr(one, "gate")
        assert len(one.trial) == 3, one.id
        assert len(one.trial_ids) == 3, one.id


# --- вход: уровень и цепочка заданий ----------------------------------


def test_a_branch_is_refused_until_the_trial_is_done(content: GameContent) -> None:
    gladiator = content.subclass("warrior_gladiator")
    young = hero(level=10)
    assert "30 уровня" in subclass_rules.refusal(content, young, gladiator)

    grown = hero(level=40)
    assert "Испытание не пройдено" in subclass_rules.refusal(content, grown, gladiator)
    assert subclass_rules.choose(content, grown, gladiator.id) is None

    ready = passed(content, grown, gladiator.id)
    assert subclass_rules.refusal(content, ready, gladiator) == ""
    assert subclass_rules.choose(content, ready, gladiator.id) is not None


def test_the_trial_is_walked_step_by_step(content: GameContent) -> None:
    one = content.subclass("warrior_knight")
    grown = hero(level=40)
    first = subclass_rules.trial_step(content, grown, one)
    assert first is not None and first.id == one.trial_ids[0]
    assert subclass_rules.trial_progress(content, grown, one) == (0, 3)

    halfway = replace(grown, quests=QuestLog(done=one.trial_ids[:2]))
    step = subclass_rules.trial_step(content, halfway, one)
    assert step is not None and step.id == one.trial_ids[2]
    assert subclass_rules.trial_progress(content, halfway, one) == (2, 3)

    ready = passed(content, grown, one.id)
    assert subclass_rules.trial_step(content, ready, one) is None
    assert subclass_rules.trial_done(content, ready, one)


def test_a_branch_is_taken_once(content: GameContent) -> None:
    ready = passed(content, hero(level=40), "warrior_gladiator")
    taken = subclass_rules.choose(content, ready, "warrior_gladiator")
    assert taken is not None
    assert subclass_rules.choose(content, taken, "warrior_gladiator") is None
    twice = passed(content, taken, "warrior_knight")
    assert "уже определились" in subclass_rules.refusal(
        content, twice, content.subclass("warrior_knight")
    )


def test_a_branch_only_grows_from_the_one_that_was_taken(content: GameContent) -> None:
    """Дуэлянт приходит из Гладиатора и ниоткуда больше."""
    ready = passed(content, hero(level=150), "warrior_knight", "warrior_duelist")
    knight = subclass_rules.choose(content, ready, "warrior_knight")
    assert knight is not None

    offered = subclass_rules.offered(content, knight, 2)
    assert {one.id for one in offered} == {"warrior_sentinel", "warrior_blackknight"}
    duelist = content.subclass("warrior_duelist")
    assert "растёт не из вашей" in subclass_rules.refusal(content, knight, duelist)
    assert subclass_rules.choose(content, knight, "warrior_duelist") is None


def test_tiers_are_taken_in_order(content: GameContent) -> None:
    """Пока не выбрана первая ветвь, вторая не предлагается."""
    grown = hero(level=150)
    assert subclass_rules.open_tier(content, grown) == 1
    taken = subclass_rules.choose(
        content, passed(content, grown, "warrior_gladiator"), "warrior_gladiator"
    )
    assert taken is not None
    assert subclass_rules.open_tier(content, taken) == 2


def test_a_closed_branch_is_still_shown(content: GameContent) -> None:
    """Закрытая ветка с названной ценой - это цель."""
    offered = subclass_rules.offered(content, hero(level=1), 1)
    assert len(offered) == 2
    assert all(subclass_rules.refusal(content, hero(level=1), one) for one in offered)


# --- что ветка даёт ---------------------------------------------------


def test_a_branch_teaches_a_skill_the_neighbour_never_gets(content: GameContent) -> None:
    """Ветка кладёт своё умение в список изучаемых, и только взятая (ADR 0074)."""
    plain = hero(level=150, STR=80)
    codes = {skill.code for skill in skill_rules.teachable(content, plain)}
    assert "warrior_gladiator_vihr_areny" not in codes

    ready = passed(content, plain, "warrior_gladiator")
    taken = subclass_rules.choose(content, ready, "warrior_gladiator")
    assert taken is not None
    grown = {skill.code for skill in skill_rules.teachable(content, taken)}
    assert "warrior_gladiator_vihr_areny" in grown
    assert codes < grown

    skill = content.skill("warrior_gladiator_vihr_areny")
    assert skill.owner_kind is OwnerKind.SUBCLASS
    learned = skill_rules.learn(content, replace(taken, unspent_skill_points=1), skill)
    assert learned is not None
    assert skill_rules.is_known(learned, skill.code)
    assert skill in skill_rules.equippable(content, learned)


def test_a_branch_rewrites_the_class_grid(content: GameContent) -> None:
    """Гладиатором гладиатора делает сетка, а не строка в описании."""
    plain = hero(level=150, STR=100)
    ready = passed(content, plain, "warrior_gladiator")
    taken = subclass_rules.choose(content, ready, "warrior_gladiator")
    assert taken is not None

    before = content.character_class("warrior").scaling_of(StatCode.STR).blow
    after = subclass_rules.class_of(content, taken).scaling_of(StatCode.STR).blow
    assert after > before
    assert blow_of(content, taken) > blow_of(content, plain)


def test_a_branch_pays_in_modifiers(content: GameContent) -> None:
    plain = hero(level=150, STR=100, END=60)
    ready = passed(content, plain, "warrior_knight")
    taken = subclass_rules.choose(content, ready, "warrior_knight")
    assert taken is not None

    assert subclass_rules.modifiers(content, taken)["armor_percent"] == pytest.approx(10.0)
    assert derived_stats(content, taken).armor > derived_stats(content, plain).armor


def test_branches_stack(content: GameContent) -> None:
    """Мастер клинка - это Гладиатор, ставший Дуэлянтом, ставший Мастером клинка."""
    road = ("warrior_gladiator", "warrior_duelist", "warrior_blademaster")
    walked = hero(level=150, STR=200)
    for one in road:
        walked = passed(content, walked, one)
        chosen = subclass_rules.choose(content, walked, one)
        assert chosen is not None, one
        walked = chosen

    assert [one.id for one in subclass_rules.taken(content, walked)] == list(road)
    assert subclass_rules.open_tier(content, walked) is None
    # Поздняя ступень договаривает раннюю: сетка силы дошла до последней правки.
    assert subclass_rules.class_of(content, walked).scaling_of(StatCode.STR).blow == pytest.approx(
        2.9
    )
    # И все три умения дороги лежат в списке изучаемых.
    codes = {skill.code for skill in subclass_rules.skills(content, walked)}
    assert len(codes) == 3


def test_content_outlives_the_character(content: GameContent) -> None:
    """Ветки, которой в файлах больше нет, для персонажа просто не существует."""
    ghost = replace(hero(level=150), subclass_ids=("no_such_branch", "mage_psion"))
    assert subclass_rules.taken(content, ghost) == ()
    assert subclass_rules.skills(content, ghost) == ()
