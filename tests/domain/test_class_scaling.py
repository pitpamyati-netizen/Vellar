"""Классовая сетка и вехи характеристик (ADR 0068).

Проверяется то, ради чего сетка заведена: очко характеристики перестало быть
одинаковым для всех восьми классов, а порог перестал быть просто числом.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import milestones as milestone_rules
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules.combat import blow_of
from mmorpg.domain.rules.stats import derived_stats


def hero(class_id: str, *, level: int = 1, **allocated: int) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        allocated=StatBlock(**allocated),
    )


# --- сетка ------------------------------------------------------------


def test_every_class_says_something_about_every_stat(content: GameContent) -> None:
    """Молчание неотличимо от нуля, а ноль обязан быть сказан вслух.

    Класс, промолчавший про удачу, обещает игроку, что удача ему что-то даёт:
    экран характеристик всё равно её напечатает.
    """
    for klass in content.classes:
        missing = [code.value for code in StatCode if code not in klass.scaling]
        assert not missing, f"{klass.id} says nothing about {missing}"


def test_the_same_point_is_worth_different_things_to_different_classes(
    content: GameContent,
) -> None:
    """Сила воина и сила мага - разные вещи, и это написано числами.

    Это и есть строгая классовость: прежде обе были одним и тем же
    коэффициентом, а класс отличался только словом в ``key_stats``.
    """
    warrior = content.character_class("warrior").scaling_of(StatCode.STR).blow
    mage = content.character_class("mage").scaling_of(StatCode.STR).blow
    assert warrior > mage * 5, f"warrior {warrior} against mage {mage}"

    mage_int = content.character_class("mage").scaling_of(StatCode.INT).blow
    warrior_int = content.character_class("warrior").scaling_of(StatCode.INT).blow
    assert mage_int > warrior_int


@pytest.mark.parametrize("class_id", ["warrior", "mage", "rogue", "cleric"])
def test_a_point_in_the_leading_stat_moves_the_blow(content: GameContent, class_id: str) -> None:
    """Очко в ведущей характеристике обязано быть видно в ударе."""
    klass = content.character_class(class_id)
    lead = klass.key_stats[0]
    plain = hero(class_id, level=20)
    invested = replace(plain, allocated=plain.allocated.with_change(lead, 40))
    assert blow_of(content, invested) > blow_of(content, plain)


def test_an_off_stat_is_nearly_worthless_to_the_wrong_class(content: GameContent) -> None:
    """Сорок очков силы у мага двигают удар меньше, чем у воина.

    Не «не двигают вовсе»: сетка не запрещает вкладывать куда угодно, она
    называет цену такого вложения.
    """

    def gain(class_id: str) -> float:
        plain = hero(class_id, level=20)
        strong = replace(plain, allocated=plain.allocated.with_change(StatCode.STR, 40))
        return blow_of(content, strong) - blow_of(content, plain)

    assert gain("mage") * 4 < gain("warrior")


def test_health_and_armour_come_from_the_grid(content: GameContent) -> None:
    """Выносливость держит здоровье и броню - столько, сколько сказал класс."""
    klass = content.character_class("warrior")
    plain = hero("warrior", level=20)
    tough = replace(plain, allocated=plain.allocated.with_change(StatCode.END, 50))
    before = derived_stats(content, plain)
    after = derived_stats(content, tough)
    assert after.max_health > before.max_health
    assert after.armor > before.armor
    # Здоровье выросло примерно на то, что обещала сетка: точное число двигают
    # проценты, поэтому сравнивается порядок, а не равенство.
    promised = klass.scaling_of(StatCode.END).health * 50
    assert after.max_health - before.max_health >= promised * 0.8


def test_a_class_that_heals_gets_it_from_its_own_stats(content: GameContent) -> None:
    """Лечение жреца растёт от его мудрости, и растёт с убывающей отдачей.

    Прямая линия обещала бы мудрости под семьсот сорок с лишним процентов сверх
    всего прочего - ровно та же ошибка, что число вместо доли (ADR 0058).
    """
    plain = hero("cleric", level=20)
    wise = replace(plain, allocated=plain.allocated.with_change(StatCode.WIS, 100))
    wiser = replace(plain, allocated=plain.allocated.with_change(StatCode.WIS, 400))

    def healing(character: Character) -> float:
        return mods.scaling_modifiers(content, character).get("healing_done_percent", 0.0)

    assert healing(wise) > healing(plain)
    assert healing(wiser) > healing(wise)
    assert healing(wiser) < mods.HEALING_CEILING
    # Убывающая отдача: вчетверо больше очков - меньше чем вчетверо больше выгоды.
    assert healing(wiser) < healing(wise) * 4


def test_a_class_that_does_not_heal_promises_no_healing(content: GameContent) -> None:
    """Варвар не лечит, и сетка не обещает ему лечения ни на каком вложении."""
    brute = hero("barbarian", level=50, STR=200, WIS=200)
    assert "healing_done_percent" not in mods.scaling_modifiers(content, brute)


# --- вехи -------------------------------------------------------------


def test_a_milestone_is_taken_by_investment_only(content: GameContent) -> None:
    """Порог берут вложением; ниже порога вехи нет."""
    klass = content.character_class("warrior")
    first = min(item.threshold for item in klass.milestones if item.stat is StatCode.STR)
    invested = milestone_rules.invested_stats(content, hero("warrior", level=1))
    short = first - invested[StatCode.STR]

    just_under = hero("warrior", level=1, STR=short - 1)
    exactly = hero("warrior", level=1, STR=short)
    assert not milestone_rules.reached(content, just_under)
    assert milestone_rules.reached(content, exactly)


def test_a_milestone_pays_in_modifiers(content: GameContent) -> None:
    """Веха платит обычной чертой: тем же словарём и тем же свёртком."""
    plain = hero("warrior", level=1)
    strong = hero("warrior", level=1, STR=300)
    before = mods.milestone_modifiers(content, plain)
    after = mods.milestone_modifiers(content, strong)
    assert not before
    assert after
    assert set(after) <= mods.EFFECTIVE_KEYS, sorted(set(after) - mods.EFFECTIVE_KEYS)


def test_a_milestone_does_not_flicker_with_the_gloves(content: GameContent) -> None:
    """Веха считается от вложенного, а не от надетого.

    Порог, который берут и теряют, снимая перчатки, - это не веха, а мерцание.
    """
    strong = hero("warrior", level=1, STR=300)
    invested = milestone_rules.invested_stats(content, strong)
    # Прибавка к характеристике от вещей в этот счёт не входит вовсе: он собран
    # без обращения к свёртку прибавок.
    assert invested[StatCode.STR] == content.rules.base_stat_value + 300 + 2 + 1


def test_the_nearest_milestone_is_named_with_its_price(content: GameContent) -> None:
    """Игроку говорят, сколько очков осталось до ближайшей вехи."""
    plain = hero("warrior", level=1)
    pending = milestone_rules.pending(content, plain)
    assert pending
    for item, short in pending:
        assert short > 0
        assert item.name


def test_a_milestone_whose_trait_is_gone_gives_nothing(content: GameContent) -> None:
    """Содержимое переживает персонажа: вычеркнутая черта не роняет экран."""
    klass = content.character_class("warrior")
    broken = replace(
        klass,
        milestones=tuple(replace(item, trait_id="nope") for item in klass.milestones),
    )
    patched = replace(
        content,
        classes=tuple(broken if one.id == "warrior" else one for one in content.classes),
        _classes_by_id={**content._classes_by_id, "warrior": broken},
    )
    strong = hero("warrior", level=1, STR=300)
    assert milestone_rules.reached(patched, strong)
    assert milestone_rules.trait_ids(patched, strong) == ()


def test_milestone_traits_are_never_offered_at_creation(content: GameContent) -> None:
    """Выбрать нельзя то, что зарабатывают."""
    granted = [trait for trait in content.traits if content.is_granted_category(trait.category)]
    assert granted
    for trait in granted:
        assert trait.category in content.granted_trait_categories


def test_focus_beats_spread(content: GameContent) -> None:
    """Собранная характеристика сильнее размазанной - за это и держатся пороги.

    Очков поровну; разница только в том, собраны они в одну характеристику или
    разложены по двум.
    """
    points = 240
    focused = hero("warrior", level=60, STR=points)
    spread = hero("warrior", level=60, STR=points // 2, AGI=points // 2)
    assert len(milestone_rules.reached(content, focused)) > len(
        milestone_rules.reached(content, spread)
    )
    assert blow_of(content, focused) > blow_of(content, spread)
