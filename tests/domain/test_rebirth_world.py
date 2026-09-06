"""Мир отвечает на взятые имена, а ремесло просит рук (ADR 0072).

Две вещи, у которых одна причина: содержимое, которое молчит о герое, перестаёт
быть содержимым. Порода крепчает по числу взятых имён, а характеристика ремесла
перестала быть надписью в файле.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.craft import CraftLog, CraftProgress
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.procgen import enemies as enemy_procgen
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import crafts as craft_rules


def hero(*, level: int = 40, remorts: int = 0, **allocated: int) -> Character:
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


def a_wolf(content: GameContent, level: int = 40) -> object:
    return enemy_procgen.generate_enemy(
        derive(b"vellar-test", "pack", 1),
        archetypes=content.enemy_archetypes,
        biome="луга",
        level=level,
        rank=EnemyRank.NORMAL,
        elite_titles=content.elite_titles,
    )


# --- порода крепчает --------------------------------------------------


def test_a_species_hardens_with_every_name(content: GameContent) -> None:
    """Второй проход идёт по тем же противникам человеком, который стал больше."""
    plain = a_wolf(content)
    scaling = content.enemy_scaling
    assert scaling.health_per_rebirth > 0
    assert scaling.damage_per_rebirth > 0

    before = enemy_procgen.hardened(plain, 0, scaling)  # type: ignore[arg-type]
    assert before is plain
    for taken in (1, 2, 3):
        after = enemy_procgen.hardened(plain, taken, scaling)  # type: ignore[arg-type]
        assert after.max_health > plain.max_health  # type: ignore[attr-defined]
        assert after.damage > plain.damage  # type: ignore[attr-defined]


def test_a_hardened_species_never_pays_more(content: GameContent) -> None:
    """Содержимое, подстроившееся под игрока, не платит как свежий вызов (ADR 0019).

    Подстройка, которая платит, была бы способом фармить: взять три имени и
    получать за тех же волков в полтора раза больше.
    """
    plain = a_wolf(content)
    tough = enemy_procgen.hardened(plain, 3, content.enemy_scaling)  # type: ignore[arg-type]
    assert tough.gold == plain.gold  # type: ignore[attr-defined]
    assert tough.stakes == plain.stakes  # type: ignore[attr-defined]
    assert tough.level == plain.level  # type: ignore[attr-defined]
    assert tough.name == plain.name  # type: ignore[attr-defined]


def test_the_world_answers_less_than_the_name_gives(content: GameContent) -> None:
    """Перерождённый обязан остаться сильнее себя прежнего.

    Иначе уход не даёт ничего, а платят за него полутора сотнями уровней.
    """
    scaling = content.enemy_scaling
    for step in content.rebirths:
        answered = scaling.health_per_rebirth * step.rank * 100.0
        assert answered < step.stat_bonus, step.id


def test_hardening_is_the_same_for_everyone_in_one_fight(content: GameContent) -> None:
    """Стая одна, и разной для двоих в одном бою она быть не может (ADR 0065).

    Поэтому крепость берётся одним числом — по лучшему из живых, — а не по
    каждому бойцу отдельно.
    """
    plain = a_wolf(content)
    scaling = content.enemy_scaling
    best = enemy_procgen.hardened(plain, 3, scaling)  # type: ignore[arg-type]
    same = enemy_procgen.hardened(plain, 3, scaling)  # type: ignore[arg-type]
    assert best.max_health == same.max_health  # type: ignore[attr-defined]


# --- ремесло просит рук -----------------------------------------------


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
