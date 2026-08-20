"""Contracts as content: everything they point at has to exist.

A contract that names a missing item, an unreachable level or a chain that never
opens is a dead end a player walks into, so it is caught here rather than in the
group chat.
"""

from __future__ import annotations

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.quest import ObjectiveKind
from tests.content.conftest import FORBIDDEN_WORDS


def test_the_first_act_is_actually_written(content: GameContent) -> None:
    """Levels 1-30 need contracts, or the act exists only in the roadmap."""
    farhold = content.quests_in("farhold")
    assert len(farhold) >= 5
    assert farhold[0].level == 1
    assert max(quest.level for quest in farhold) >= 20


def test_every_contract_belongs_to_a_city_that_exists(content: GameContent) -> None:
    known = {city.id for city in content.cities}
    assert all(quest.city_id in known for quest in content.quests)


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
    for quest in content.quests:
        city = content.city(quest.city_id)
        assert city.unlock_level <= quest.level <= city.level_max, quest.id


def test_a_contract_that_follows_another_comes_later(content: GameContent) -> None:
    for quest in content.quests:
        if quest.follows:
            assert content.quest(quest.follows).level <= quest.level, quest.id


def test_the_price_grows_with_the_level(content: GameContent) -> None:
    farhold = content.quests_in("farhold")
    payments = [quest.reward_gold for quest in farhold]
    assert payments == sorted(payments), "a harder contract must not pay less"


def test_every_contract_names_a_price_and_a_person(content: GameContent) -> None:
    for quest in content.quests:
        assert quest.giver.strip(), quest.id
        assert quest.terms.strip(), quest.id
        assert quest.reward_gold > 0, quest.id
        assert quest.reward_experience > 0, quest.id
        assert quest.target_count >= 1, quest.id


def test_the_terms_are_short_enough_to_hear(content: GameContent) -> None:
    """Two sentences, says Narrative.md; the cap here is the listening budget."""
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
    """Nobody may be asked for a thing no recipe in the game produces.

    Contracts and crafts used to be two games in one bot: nothing on any board
    asked for a made thing, so the work had no customer but the shopkeeper
    (Roadmap, "Риски").
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
