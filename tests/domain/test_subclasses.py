"""Ступени специализации (ADR 0069).

Проверяется то, ради чего они заведены: ступень стоит чего-то, берётся один раз,
складывается с прежней и по-настоящему меняет числа — а не подписывает их.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.domain.rules.combat import blow_of
from mmorpg.domain.rules.stats import derived_stats


def hero(
    class_id: str = "warrior", *, level: int = 1, remorts: int = 0, **allocated: int
) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        remorts=remorts,
        allocated=StatBlock(**allocated),
    )


# --- содержимое -------------------------------------------------------


def test_every_class_has_all_three_tiers(content: GameContent) -> None:
    """Класс без ступени — это класс, чей игрок на тридцатом не получит ничего."""
    for klass in content.classes:
        assert content.subclass_tiers(klass.id) == (0, 1, 3), klass.id


def test_the_first_tier_is_a_fork_not_a_corridor(content: GameContent) -> None:
    """Ступень с одним подклассом показывает выбор, которого нет."""
    for klass in content.classes:
        assert len(content.subclasses_of(klass.id, 0)) >= 2, klass.id


def test_a_subclass_changes_something(content: GameContent) -> None:
    """Ступень без прибавок и без правки сетки — худшая из кнопок."""
    for one in content.subclasses:
        assert one.modifiers or one.scaling, one.id
        assert set(one.modifiers) <= mods.EFFECTIVE_KEYS, one.id


def test_later_tiers_cost_more(content: GameContent) -> None:
    """Цена ступени растёт: уровень и уходы, а у гибрида — две характеристики."""
    for klass in content.classes:
        first = content.subclasses_of(klass.id, 0)[0]
        hybrid = content.subclasses_of(klass.id, 1)[0]
        crown = content.subclasses_of(klass.id, 3)[0]
        assert first.gate.remorts == 0
        assert hybrid.gate.level > first.gate.level
        assert hybrid.gate.remorts >= 1
        assert crown.gate.level >= hybrid.gate.level
        assert crown.gate.remorts >= 3
        # Гибрид тем и гибрид, что просит две характеристики сразу.
        assert len(hybrid.gate.stats) >= 2, hybrid.id


# --- правила ----------------------------------------------------------


def test_a_tier_is_refused_until_it_is_paid_for(content: GameContent) -> None:
    """Отказ называется целиком и до нажатия."""
    one = content.subclass("warrior_vanguard")
    green = hero(level=10)
    assert subclass_rules.refusal(content, green, one)
    assert subclass_rules.choose(content, green, one.id) is None

    ready = hero(level=30)
    assert subclass_rules.refusal(content, ready, one) == ""
    assert subclass_rules.choose(content, ready, one.id) is not None


def test_a_hybrid_asks_for_a_rebirth_and_two_stats(content: GameContent) -> None:
    """Уровня мало: гибрид просит уход и две собранные характеристики."""
    hybrid = content.subclass("warrior_ironsworn")
    tall = hero(level=150, STR=400, END=400)
    assert "уход" in subclass_rules.refusal(content, tall, hybrid)

    reborn = replace(tall, remorts=1, subclass_ids=("warrior_vanguard",))
    assert subclass_rules.refusal(content, reborn, hybrid) == ""

    thin = replace(reborn, allocated=StatBlock(STR=400))
    assert "END" in subclass_rules.refusal(content, thin, hybrid)


def test_a_tier_is_taken_once(content: GameContent) -> None:
    """Выбранное не меняют: обратимый выбор — не выбор, а настройка."""
    ready = hero(level=30)
    first = subclass_rules.choose(content, ready, "warrior_vanguard")
    assert first is not None
    other = content.subclass("warrior_warden")
    assert subclass_rules.refusal(content, first, other)
    assert subclass_rules.choose(content, first, other.id) is None


def test_tiers_are_taken_in_order(content: GameContent) -> None:
    """Поздний игрок не проскакивает первое решение, не узнав, что оно было."""
    late = hero(level=150, remorts=3, STR=400, END=400)
    assert subclass_rules.open_tier(content, late) == 0
    stepped = subclass_rules.choose(content, late, "warrior_vanguard")
    assert stepped is not None
    assert subclass_rules.open_tier(content, stepped) == 1


def test_a_closed_tier_is_still_shown(content: GameContent) -> None:
    """Закрытая ступень с названной ценой — это цель, а не пустое место."""
    green = hero(level=10)
    offered = subclass_rules.offered(content, green, 0)
    assert offered
    assert all(subclass_rules.refusal(content, green, one) for one in offered)


# --- что ступень делает -----------------------------------------------


def test_a_subclass_rewrites_the_class_grid(content: GameContent) -> None:
    """Берсерком берсерка делает сетка, а не строка в описании (ADR 0068)."""
    plain = hero("barbarian", level=40, STR=200)
    berserk = replace(plain, subclass_ids=("barbarian_berserk",))
    assert subclass_rules.class_of(content, berserk).scaling_of(StatCode.STR).blow > (
        subclass_rules.class_of(content, plain).scaling_of(StatCode.STR).blow
    )
    assert blow_of(content, berserk) > blow_of(content, plain)
    # Ярость взамен брони: то, что подкласс обещает словами, он делает числами.
    assert derived_stats(content, berserk).armor < derived_stats(content, plain).armor


def test_a_subclass_pays_in_modifiers(content: GameContent) -> None:
    """Свёрток подкласса ложится в общий, как техника дома."""
    plain = hero(level=40, STR=200)
    warden = replace(plain, subclass_ids=("warrior_warden",))
    bundle = mods.collect_modifiers(content, warden)
    assert bundle["damage_taken_percent"] < 0
    assert derived_stats(content, warden).armor > derived_stats(content, plain).armor


def test_tiers_stack(content: GameContent) -> None:
    """Дредноут — это передовой, ставший клятым железом, ставший дредноутом."""
    stack = hero(level=150, remorts=3, STR=400, END=400)
    stack = replace(
        stack, subclass_ids=("warrior_vanguard", "warrior_ironsworn", "warrior_dreadnought")
    )
    taken = subclass_rules.taken(content, stack)
    assert [one.tier for one in taken] == [0, 1, 3]
    # Поздняя ступень договаривает раннюю: в сетке остаётся её значение.
    grid = subclass_rules.class_of(content, stack)
    dreadnought = content.subclass("warrior_dreadnought")
    assert grid.scaling_of(StatCode.STR).blow == dreadnought.scaling[StatCode.STR].blow
    # А прибавки всех трёх складываются.
    bundle = subclass_rules.modifiers(content, stack)
    assert bundle["physical_damage_percent"] == pytest.approx(19.0)


def test_content_outlives_the_character(content: GameContent) -> None:
    """Подкласс, которого больше нет, ничего не даёт и ничего не роняет."""
    ghost = replace(hero(level=40), subclass_ids=("nope", "warrior_vanguard"))
    assert [one.id for one in subclass_rules.taken(content, ghost)] == ["warrior_vanguard"]
    stray = replace(hero("mage", level=40), subclass_ids=("warrior_vanguard",))
    assert subclass_rules.taken(content, stray) == ()
    assert subclass_rules.modifiers(content, stray) == {}


def test_a_subclass_adds_no_button_to_the_panel(content: GameContent) -> None:
    """Панель не растёт никогда: подкласс не приносит ни слота, ни умения.

    Проверяется делом, а не обещанием: у ступени просто нет поля, которым можно
    было бы объявить умение, и здесь закреплено, что его не завели.
    """
    for one in content.subclasses:
        assert not hasattr(one, "skills")
        assert not hasattr(one, "active_code")
