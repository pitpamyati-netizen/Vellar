"""Чего стоит вылазка: награды, раны, цена поражения, цена постели.

Кто выиграл, решает движок. Здесь речь о том, *ради чего* эта победа, - и об
одном правиле, на которое опирается вся экономика игры: бой оставляет следы, за
сведение которых платят деньгами.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, QuestLog
from mmorpg.domain.entities.combat import (
    BattleOutcome,
    BattleState,
    Combatant,
    CombatantKind,
)
from mmorpg.domain.entities.content import ItemKind
from mmorpg.domain.entities.location import (
    Enemy,
    EnemyKind,
    EnemyRank,
    LocationNode,
    NodeKind,
)
from mmorpg.domain.rules import adventure, progression
from mmorpg.domain.rules.economy import inn_price, mentor_price
from mmorpg.domain.rules.stats import derived_stats


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=6,
        gold=500,
    )


def a_wolf(*, elite: bool = False) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=5,
        max_health=60,
        damage=6,
        armor=2,
        initiative=8.0,
        rank=EnemyRank.ELITE if elite else EnemyRank.NORMAL,
        loot=("wolf_pelt",),
        gold=20,
    )


def a_won_fight(content: GameContent, hero: Character, *, health: int = 30) -> BattleState:
    """Бой, который уже кончился победой: герой ранен, волк повержен."""
    stats = derived_stats(content, hero)
    wolf = a_wolf()
    return BattleState(
        combatants=(
            Combatant(
                id=1,
                side=0,
                kind=CombatantKind.HERO,
                name=hero.name,
                level=hero.level,
                max_health=stats.max_health,
                health=health,
                max_resource=stats.max_resource,
                resource=10,
                resource_name=stats.resource_name,
                live=True,
                character_id=hero.id,
            ),
            Combatant(
                id=2,
                side=1,
                kind=CombatantKind.MONSTER,
                name=wolf.name,
                level=wolf.level,
                max_health=wolf.max_health,
                health=0,
                enemy=wolf,
            ),
        ),
        order=(1,),
        outcome=BattleOutcome.DECIDED,
        winner=0,
        experience=400,
        gold=25,
        loot=("wolf_pelt",),
    )


def a_node(kind: NodeKind, level: int = 5) -> LocationNode:
    return LocationNode(index=3, kind=kind, name="Тайник", level=level, links=(0,))


# --- после боя --------------------------------------------------------


def test_a_won_fight_pays_and_can_raise_a_level(content: GameContent, hero: Character) -> None:
    result = adventure.resolve_victory(content, hero, a_won_fight(content, hero), 1)
    assert result.gold == 25
    assert result.character.gold == hero.gold + 25
    # Названо то, что записано: у человека расовая прибавка к опыту, и отчёт о
    # бое обязан называть уже её (``progression.earned``).
    assert result.experience == progression.earned(content, hero, 400)
    assert result.experience > 400
    assert result.character.experience == hero.experience + result.experience
    assert result.loot == ("wolf_pelt",)
    assert result.character.level >= hero.level


def test_a_won_fight_reports_the_experience_it_actually_gave(
    content: GameContent, hero: Character
) -> None:
    """Раса, которая обещает больше опыта, обещает его и в отчёте.

    Отчёт печатал число до прибавки, а записывал число после неё: «400 опыта» на
    экране и 420 в базе - это та же ложь, что и прибавка, которой никто не
    считает (``Claude.md``, правило 7)."""
    plain = replace(hero, race_id="dwarf")
    result = adventure.resolve_victory(content, plain, a_won_fight(content, plain), 1)
    assert result.experience == 400
    assert result.character.experience == plain.experience + 400


def test_wounds_are_carried_out_of_the_fight(content: GameContent, hero: Character) -> None:
    result = adventure.resolve_victory(content, hero, a_won_fight(content, hero, health=17), 1)
    assert result.character.health == 17


def test_a_won_fight_moves_the_contract_counter(content: GameContent, hero: Character) -> None:
    hunting = replace(hero, quests=QuestLog(taken={"farhold_meadow_teeth": 0}))
    result = adventure.resolve_victory(content, hunting, a_won_fight(content, hunting), 1)
    assert result.character.quests.progress("farhold_meadow_teeth") == 1
    assert result.quest_steps and result.quest_steps[0].progress == 1


def test_losing_costs_a_tenth_of_what_is_on_you(content: GameContent, hero: Character) -> None:
    result = adventure.resolve_defeat(content, hero)
    assert result.gold_lost == 50
    assert result.character.gold == 450
    assert 0 < result.character.health < derived_stats(content, hero).max_health


def test_losing_never_touches_the_strongbox(content: GameContent, hero: Character) -> None:
    saver = replace(hero, bank_gold=10_000)
    assert adventure.resolve_defeat(content, saver).character.bank_gold == 10_000


def test_losing_with_nothing_on_you_is_still_survivable(
    content: GameContent, hero: Character
) -> None:
    broke = replace(hero, gold=0)
    result = adventure.resolve_defeat(content, broke)
    assert result.gold_lost == 0
    assert result.character.health >= 1


# --- тихие узлы -------------------------------------------------------


def test_a_cache_pays_gold_and_is_deterministic(content: GameContent, hero: Character) -> None:
    first = adventure.resolve_search(content, hero, a_node(NodeKind.CACHE), b"seed")
    second = adventure.resolve_search(content, hero, a_node(NodeKind.CACHE), b"seed")
    assert first.gold > 0
    assert (first.gold, first.item_id) == (second.gold, second.item_id)
    assert first.character.gold == hero.gold + first.gold


def test_a_different_node_gives_a_different_find(content: GameContent, hero: Character) -> None:
    here = adventure.resolve_search(content, hero, a_node(NodeKind.CACHE), b"one")
    there = adventure.resolve_search(content, hero, a_node(NodeKind.CACHE), b"two")
    assert (here.gold, here.item_id) != (there.gold, there.item_id)


def test_a_shrine_heals_instead_of_paying(content: GameContent, hero: Character) -> None:
    hurt = replace(hero, health=5)
    result = adventure.resolve_search(content, hurt, a_node(NodeKind.SHRINE), b"shrine")
    assert result.healed > 0
    assert result.character.health > 5
    assert result.gold == 0


def test_a_shrine_at_full_health_heals_nothing(content: GameContent, hero: Character) -> None:
    result = adventure.resolve_search(content, hero, a_node(NodeKind.SHRINE), b"shrine")
    assert result.healed == 0


def test_gathering_brings_materials_and_never_gold(content: GameContent, hero: Character) -> None:
    result = adventure.resolve_search(content, hero, a_node(NodeKind.GATHER), b"herbs")
    assert result.gold == 0
    if result.item_id:
        assert content.item(result.item_id).kind.value == "material"


def test_every_quiet_node_pays_experience(content: GameContent, hero: Character) -> None:
    for kind in (NodeKind.CACHE, NodeKind.GATHER, NodeKind.SHRINE, NodeKind.EVENT):
        result = adventure.resolve_search(content, hero, a_node(kind), b"node")
        assert result.experience > 0
        assert result.character.experience > hero.experience


def test_searching_moves_a_search_contract(content: GameContent, hero: Character) -> None:
    counting = replace(hero, quests=QuestLog(taken={"farhold_tallies": 0}))
    result = adventure.resolve_search(content, counting, a_node(NodeKind.CACHE), b"seed")
    assert result.character.quests.progress("farhold_tallies") == 1


# --- город ----------------------------------------------------------


def test_a_paid_bed_heals_everything_and_costs_by_level(
    content: GameContent, hero: Character
) -> None:
    hurt = replace(hero, health=1)
    result = adventure.rest(content, hurt, paid=True)
    assert result.cost == inn_price(hero.level)
    assert result.character.health == derived_stats(content, hero).max_health
    assert result.character.gold == hero.gold - result.cost


def test_straw_is_free_and_only_partial(content: GameContent, hero: Character) -> None:
    hurt = replace(hero, health=1)
    result = adventure.rest(content, hurt, paid=False)
    assert result.cost == 0
    assert result.character.gold == hero.gold
    assert 1 < result.character.health < derived_stats(content, hero).max_health


def test_a_broke_character_is_never_stuck(content: GameContent, hero: Character) -> None:
    """Бесплатная постель - причина, по которой ход есть даже на одной единице здоровья."""
    penniless = replace(hero, gold=0, health=1)
    refused = adventure.rest(content, penniless, paid=True)
    assert refused.refused == "poor"
    assert refused.character == penniless
    assert adventure.rest(content, penniless, paid=False).character.health > 1


def test_nobody_pays_for_a_bed_they_do_not_need(content: GameContent, hero: Character) -> None:
    result = adventure.rest(content, hero, paid=True)
    assert result.refused == "whole"
    assert result.character.gold == hero.gold


def test_a_potion_heals_outside_a_fight_too(content: GameContent, hero: Character) -> None:
    hurt = replace(hero, health=3)
    healed, restored = adventure.use_consumable(content, hurt, "small_healing_potion")
    assert restored > 0
    assert healed.health == 3 + restored


def test_a_potion_at_full_health_is_not_wasted(content: GameContent, hero: Character) -> None:
    healed, restored = adventure.use_consumable(content, hero, "small_healing_potion")
    assert restored == 0
    assert healed == hero


def test_what_is_not_a_potion_does_nothing_out_here(content: GameContent, hero: Character) -> None:
    hurt = replace(hero, health=3)
    healed, restored = adventure.use_consumable(content, hurt, "whetstone")
    assert (healed, restored) == (hurt, 0)


def test_the_city_charges_more_the_higher_you_climb() -> None:
    assert inn_price(1) < inn_price(50) < inn_price(300)
    assert mentor_price(1) < mentor_price(50) < mentor_price(300)


# --- дно спуска -------------------------------------------------------


def test_the_bottom_of_a_descent_pays_for_the_walk_down(
    content: GameContent, hero: Character
) -> None:
    """Три боя подряд когда-то стоили ровно трёх боёв: эпический противник
    был единственным, что там, внизу, было.
    """
    prize = adventure.descent_prize(content, hero, level=hero.level + 1, seed=b"bottom")

    assert prize.gold == adventure.descent_gold(hero.level + 1)
    assert prize.gold > 0
    assert prize.experience > 0
    assert prize.character.gold == hero.gold + prize.gold
    assert prize.character.experience == hero.experience + prize.experience
    # Что-то, что можно вынести, и никогда не горсть трав из-под пола.
    assert prize.item_id
    assert content.item(prize.item_id).kind is not ItemKind.MATERIAL


def test_a_deeper_descent_is_worth_more(content: GameContent, hero: Character) -> None:
    shallow = adventure.descent_prize(content, hero, level=5, seed=b"bottom")
    deep = adventure.descent_prize(content, hero, level=40, seed=b"bottom")
    assert deep.gold > shallow.gold
    assert deep.experience > shallow.experience


def test_the_same_descent_pays_the_same_thing_twice(content: GameContent, hero: Character) -> None:
    """Из сида, как и всё прочее: награда принадлежит вылазке, а не тому мгновению,
    когда лёг последний удар.
    """
    first = adventure.descent_prize(content, hero, level=8, seed=b"one-run")
    second = adventure.descent_prize(content, hero, level=8, seed=b"one-run")
    assert (first.gold, first.item_id) == (second.gold, second.item_id)


def test_a_gathering_node_gives_what_it_is_named_after(content: GameContent) -> None:
    """«Полезные травы», отдающие железный лом, — это та же ошибка, что волчья
    шкура с кабана."""
    from mmorpg.domain.entities.location import LocationNode, NodeKind
    from mmorpg.domain.rules.adventure import GATHER_SOURCES, resolve_search

    hero = Character(id=1, user_id=1, name="Аргус", race_id="human", class_id="warrior", level=5)
    for name, wanted in GATHER_SOURCES.items():
        node = LocationNode(index=1, kind=NodeKind.GATHER, name=name, level=4, links=(0,))
        found = {
            resolve_search(content, hero, node, seed=f"try-{name}-{attempt}".encode()).item_id
            for attempt in range(40)
        }
        for item_id in found:
            if not item_id:
                continue
            assert content.item(item_id).source == wanted, f"{name} отдал {item_id}"
