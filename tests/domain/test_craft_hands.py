"""Ремесло просит рук: чем работа платит характеристике (ADR 0072).

Характеристика ремесла перестала быть надписью в файле: ранг открывает рецепт,
а руки решают, выйдет ли работа и сколько вынесут со сбора.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.rules import crafts as craft_rules


def hero(*, level: int = 40, **allocated: int) -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id="warrior",
        level=level,
        allocated=StatBlock(**allocated),
    )


def _with_rank(content: GameContent, craft_id: str, rank: int) -> CraftLog:
    """Ремесленный журнал, дошедший до этого ранга."""
    earned = craft_rules.earned_at(content.craft_rules, rank)
    return CraftLog(MappingProxyType({craft_id: CraftProgress(experience=earned)}))


def test_a_workshop_asks_for_the_stat_it_names(content: GameContent) -> None:
    """Характеристика ремесла перестала быть надписью в файле (ADR 0018)."""
    rules = content.craft_rules
    assert rules.stat_base > 0
    assert rules.stat_per_rank > 0
    assert rules.asks_at(1) < rules.asks_at(rules.max_rank)


def test_rank_alone_is_no_longer_enough(content: GameContent) -> None:
    """Дошедший до ранга одной работой упирается в руки, а не в сырьё."""
    recipe = next(
        (one for one in content.recipes if one.rank >= 5 and content.craft(one.craft_id).kind),
        None,
    )
    assert recipe is not None
    craft = content.craft(recipe.craft_id)

    green = replace(
        hero(level=60), crafts=_with_rank(content, recipe.craft_id, content.craft_rules.max_rank)
    )
    short = craft_rules.stat_shortfall(content, green, recipe.craft_id, recipe.rank)
    handy = replace(green, allocated=StatBlock(**{craft.stat.value: 400}))
    assert craft_rules.stat_shortfall(content, handy, recipe.craft_id, recipe.rank) == 0
    if short:
        owned = {need.item_id: need.count for need in recipe.inputs}
        refused = craft_rules.can_make(content, green, recipe, owned)
        assert craft.stat.value in refused
        assert craft_rules.can_make(content, handy, recipe, owned) == ""


def test_the_refusal_names_what_is_missing(content: GameContent) -> None:
    """Отказ называет руки, а не сырьё: иначе игрок понесёт в цех ещё руды."""
    recipe = next(one for one in content.recipes if one.rank >= 5)
    green = replace(
        hero(level=60), crafts=_with_rank(content, recipe.craft_id, content.craft_rules.max_rank)
    )
    owned = {need.item_id: need.count for need in recipe.inputs}
    refused = craft_rules.can_make(content, green, recipe, owned)
    if refused:
        assert "не сырья" in refused


def test_gathering_is_not_gated_but_rewarded(content: GameContent) -> None:
    """За жилой стоят руки, а не разрешение: сбор порогом не закрыт."""
    gathering = next(one for one in content.crafts if one.gathers)
    plain = replace(hero(level=60), crafts=_with_rank(content, gathering.id, 3))
    handy = replace(plain, allocated=StatBlock(**{gathering.stat.value: 300}))
    assert craft_rules.gather_amount(content, plain, gathering.id) >= 1
    assert craft_rules.gather_amount(content, handy, gathering.id) > craft_rules.gather_amount(
        content, plain, gathering.id
    )


def test_a_craft_stat_is_counted_from_what_was_invested(content: GameContent) -> None:
    """Кольцо не должно делать кузнеца кузнецом (ADR 0068)."""
    gathering = next(one for one in content.crafts if one.gathers)
    bare = replace(hero(level=60), crafts=_with_rank(content, gathering.id, 3))
    # Прибавка к характеристике от вещей в этот счёт не входит вовсе: он собран
    # без обращения к свёртку прибавок.
    assert craft_rules.gather_amount(content, bare, gathering.id) == craft_rules.gather_amount(
        content, replace(bare, trait_ids=("berserker",)), gathering.id
    )


@pytest.mark.parametrize("rank", [1, 5, 10])
def test_what_a_rank_asks_climbs(content: GameContent, rank: int) -> None:
    rules = content.craft_rules
    assert rules.asks_at(rank) == rules.stat_base + rules.stat_per_rank * (rank - 1)
