"""Задания как содержимое: всё, на что они показывают, обязано существовать.

Задание, называющее пропавшую вещь, недостижимый уровень или цепочку, которая
никогда не откроется, - это тупик, в который входит игрок, поэтому его ловят
здесь, а не в групповом чате.
"""

from __future__ import annotations

import itertools

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.quest import ObjectiveKind
from tests.content.conftest import FORBIDDEN_WORDS


def contracts(content: GameContent) -> tuple:
    """Городская работа: всё, что выдаёт доска.

    Испытание ветки - тоже задание движка, но не работа города (ADR 0074): у
    него нет ни города, ни платы золотом, и спрашивать с него городское нечего.
    """
    return tuple(quest for quest in content.quests if not quest.is_trial)


def trials(content: GameContent) -> tuple:
    """Задания испытаний веток, все до одного."""
    return tuple(quest for quest in content.quests if quest.is_trial)


def test_the_first_act_is_actually_written(content: GameContent) -> None:
    """Уровням 1-30 нужны задания, иначе акт существует только в дорожной карте."""
    farhold = content.quests_in("farhold")
    assert len(farhold) >= 5
    assert farhold[0].level == 1
    assert max(quest.level for quest in farhold) >= 10


def test_every_contract_belongs_to_a_city_that_exists(content: GameContent) -> None:
    known = {city.id for city in content.cities}
    assert all(quest.city_id in known for quest in contracts(content))


def test_every_reward_item_exists(content: GameContent) -> None:
    for quest in content.quests:
        assert not quest.reward_item or content.has_item(quest.reward_item), quest.id


def test_the_chain_never_loops_and_always_starts_somewhere(content: GameContent) -> None:
    for city in content.cities:
        quests = content.quests_in(city.id)
        if not quests:
            continue
        assert any(not quest.follows for quest in quests), city.id
        for quest in quests:
            seen = {quest.id}
            step = quest
            while step.follows:
                assert step.follows not in seen, f"{quest.id} chains into a loop"
                seen.add(step.follows)
                step = content.quest(step.follows)


def test_a_contract_never_asks_before_its_city_opens(content: GameContent) -> None:
    for quest in contracts(content):
        city = content.city(quest.city_id)
        assert city.unlock_level <= quest.level <= city.level_max, quest.id


def test_a_contract_that_follows_another_comes_later(content: GameContent) -> None:
    for quest in contracts(content):
        if quest.follows:
            assert content.quest(quest.follows).level <= quest.level, quest.id


def test_the_price_grows_with_the_level(content: GameContent) -> None:
    farhold = content.quests_in("farhold")
    payments = [quest.reward_gold for quest in farhold]
    assert payments == sorted(payments), "a harder contract must not pay less"


def test_every_contract_names_a_price_and_a_person(content: GameContent) -> None:
    for quest in contracts(content):
        assert quest.giver.strip(), quest.id
        assert quest.terms.strip(), quest.id
        assert quest.reward_gold > 0, quest.id
        assert quest.reward_experience > 0, quest.id
        assert quest.target_count >= 1, quest.id


def test_the_terms_are_short_enough_to_hear(content: GameContent) -> None:
    """Две фразы, говорит Narrative.md; потолок здесь - это бюджет слушателя."""
    for quest in content.quests:
        assert len(quest.terms) <= 200, quest.id
        assert len(quest.intro) <= 140, quest.id


def test_no_contract_speaks_the_forbidden_vocabulary(content: GameContent) -> None:
    for quest in content.quests:
        text = f"{quest.name} {quest.intro} {quest.terms}".casefold()
        for word in FORBIDDEN_WORDS:
            assert word not in text, f"{quest.id} says {word!r}"


def test_a_narrowed_target_is_one_the_game_can_count(content: GameContent) -> None:
    enemy_kinds = {archetype.kind.value for archetype in content.enemy_archetypes}
    nodes = {"gather", "cache", "shrine", "event"}
    items = {item.id for item in content.items}
    for quest in content.quests:
        if not quest.target_kind:
            continue
        match quest.objective:
            case ObjectiveKind.SEARCH:
                allowed = nodes
            case ObjectiveKind.CRAFT:
                allowed = items
            case _:
                allowed = enemy_kinds
        assert quest.target_kind in allowed, quest.id


def test_a_contract_for_made_goods_asks_for_something_makeable(content: GameContent) -> None:
    """Никого нельзя просить о вещи, которой не делает ни один рецепт в игре.

    Задания и ремёсла когда-то были двумя играми в одном боте: ни одна доска не
    просила сделанной вещи, и у работы не было заказчика, кроме лавочника.
    """
    made = {recipe.output_id for recipe in content.recipes}
    asked = [quest for quest in content.quests if quest.objective is ObjectiveKind.CRAFT]
    assert asked, "no city asks anybody to make anything"
    for quest in asked:
        assert quest.target_kind in made, f"{quest.id} asks for something no recipe makes"


def test_a_contract_sends_the_player_to_a_place_that_exists(content: GameContent) -> None:
    """Куда идти — часть задания. Задание в несуществующее место хуже, чем никакое."""
    for quest in content.quests:
        if not quest.location_slot:
            continue
        city = content.city(quest.city_id)
        assert city.has_location(quest.location_slot), quest.id
        assert quest.objective is not ObjectiveKind.CRAFT, f"{quest.id} makes things, not walks"


def test_the_first_contract_of_the_act_says_where_to_go(content: GameContent) -> None:
    """Первое задание игроки не понимали — им не говорили ни куда, ни что нажимать."""
    first = content.quests_in("farhold")[0]
    assert first.location_slot, "первое задание обязано называть локацию"
    location = content.city("farhold").location(first.location_slot)
    assert location.name in first.terms, "наниматель называет место своими словами"


# --- испытания веток (ADR 0074) ---------------------------------------


def test_every_trial_belongs_to_a_branch_and_to_no_city(content: GameContent) -> None:
    """У испытания есть ветка и нет города: доска его не покажет никогда."""
    for quest in trials(content):
        assert content.has_subclass(quest.trial_for), quest.id
        assert not quest.city_id, quest.id
        assert quest.level == content.subclass(quest.trial_for).level


def test_every_branch_has_a_trial_chained_in_order(content: GameContent) -> None:
    """Три задания подряд, и каждое следующее ждёт предыдущего."""
    for one in content.subclasses:
        steps = [content.quest(quest_id) for quest_id in one.trial_ids]
        assert len(steps) == 3, one.id
        assert not steps[0].follows
        for earlier, later in itertools.pairwise(steps):
            assert later.follows == earlier.id, one.id


def test_a_trial_pays_with_the_branch_and_not_with_gold(content: GameContent) -> None:
    """Плата за дорогу названа в конце дороги, а не по шагам."""
    for quest in trials(content):
        assert quest.reward_gold == 0, quest.id
        assert quest.reward_experience == 0, quest.id
        assert quest.terms.strip(), quest.id
        assert quest.name.strip(), quest.id
