"""The intent, the trail and the breach (Roadmap 1.1).

These three rules are the whole difference between "press attack until it dies"
and a fight with a decision in it, so they are pinned down here: the counter
cycle, the two rewards of a trail, and the fact that a fight stays deterministic
with all of it switched on.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, CombatAction, EventKind
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules import tempo
from mmorpg.domain.rules.combat import resolve_turn, start_combat
from mmorpg.domain.rules.skill_effects import spec_for
from mmorpg.domain.rules.tempo import Tag

ATTACK = CombatAction(kind=ActionKind.ATTACK)


@pytest.fixture
def brawler() -> Character:
    return Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=8,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", None, None, None, None),
            racial="race_human_second_wind",
            ranks={"warrior_cleave": 1, "warrior_taunt": 1},
        ),
    )


def an_enemy(*, armor: int = 40, health: int = 900) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=6,
        max_health=health,
        damage=7,
        armor=armor,
        initiative=9.0,
        is_elite=False,
        loot=(),
        gold=10,
    )


# --- the counter cycle ------------------------------------------------


def test_the_cycle_is_closed_and_nothing_counters_itself() -> None:
    for tag in Tag:
        assert tempo.counters(tag, tempo.BEATS[tag])
        assert not tempo.counters(tag, tag)
    assert len({tempo.BEATS[tag] for tag in Tag}) == len(Tag)


def test_every_tag_is_countered_by_exactly_one_other() -> None:
    for intent in Tag:
        breakers = [tag for tag in Tag if tempo.counters(tag, intent)]
        assert breakers == [next(t for t, beaten in tempo.BEATS.items() if beaten is intent)]


# --- reading a skill's tag off what it does ---------------------------


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        ("damage", Tag.PRESS),
        ("damage_pierce", Tag.AIM),
        ("damage_stun", Tag.AIM),
        ("heal", Tag.GUARD),
        ("shield", Tag.GUARD),
        ("buff_damage", Tag.GUARD),
        ("debuff_vulnerable", Tag.AIM),
    ],
)
def test_a_skill_carries_the_tag_of_what_it_actually_does(effect: str, expected: Tag) -> None:
    assert tempo.tag_of_spec(spec_for(effect)) is expected


def test_a_plain_attack_is_a_press() -> None:
    assert tempo.tag_of_attack() is Tag.PRESS
    assert tempo.tag_of_spec(None) is Tag.PRESS


# --- the trail --------------------------------------------------------


def test_two_of_a_kind_build_momentum_and_a_third_does_not_break_it() -> None:
    trail: tuple[str, ...] = ()
    trail = tempo.extended(trail, Tag.PRESS)
    assert not tempo.has_momentum(trail)
    trail = tempo.extended(trail, Tag.PRESS)
    assert tempo.has_momentum(trail)
    assert tempo.damage_factor(trail) > 1.0
    assert tempo.streak(trail) == 2


def test_three_different_tags_are_a_break() -> None:
    trail = tempo.extended(tempo.extended(tempo.extended((), Tag.PRESS), Tag.GUARD), Tag.AIM)
    assert tempo.is_break(trail)
    assert not tempo.has_momentum(trail)


def test_the_trail_only_ever_remembers_three_actions() -> None:
    trail: tuple[str, ...] = ()
    for tag in (Tag.PRESS, Tag.GUARD, Tag.AIM, Tag.PRESS, Tag.PRESS):
        trail = tempo.extended(trail, tag)
    assert len(trail) == tempo.BREAK_AT


def test_an_intent_is_always_one_of_the_three() -> None:
    rolled = {tempo.roll_intent(random.Random(seed)) for seed in range(50)}
    assert rolled <= set(Tag)
    assert len(rolled) == len(Tag), "all three intents must be reachable"


# --- the rules inside a real fight ------------------------------------


def test_a_fight_announces_an_intent_from_the_first_turn(
    content: GameContent, brawler: Character
) -> None:
    state = start_combat(content, brawler, (an_enemy(),), seed=b"intent-seed")
    assert state.intent in {tag.value for tag in Tag}


def test_the_trail_grows_with_every_action(content: GameContent, brawler: Character) -> None:
    state = start_combat(content, brawler, (an_enemy(),), seed=b"trail")
    state = resolve_turn(content, brawler, state, ATTACK, b"one")
    assert state.trail == (Tag.PRESS.value,)
    state = resolve_turn(content, brawler, state, ATTACK, b"two")
    assert state.trail == (Tag.PRESS.value, Tag.PRESS.value)
    assert any(event.kind is EventKind.MOMENTUM for event in state.events)


def test_a_break_costs_the_enemy_its_answer(content: GameContent, brawler: Character) -> None:
    """Press, guard, aim: three different tags, and nobody hits back."""
    state = start_combat(content, brawler, (an_enemy(),), seed=b"break")
    state = replace(state, trail=(Tag.GUARD.value, Tag.AIM.value))
    before = state.player.health
    state = resolve_turn(content, brawler, state, ATTACK, b"third")
    assert any(event.kind is EventKind.BREAK for event in state.events)
    assert state.player.health == before


def test_countering_the_announced_intent_opens_the_armour(
    content: GameContent, brawler: Character
) -> None:
    """The same hit, twice: once into a countered intent, once into another."""
    armoured = an_enemy(armor=200)
    opened = start_combat(content, brawler, (armoured,), seed=b"a")
    # A press counters an aim, and nothing else does.
    opened = replace(opened, intent=Tag.AIM.value)
    closed = replace(opened, intent=Tag.GUARD.value)

    after_breach = resolve_turn(content, brawler, opened, ATTACK, b"same-seed")
    after_plain = resolve_turn(content, brawler, closed, ATTACK, b"same-seed")

    assert any(event.kind is EventKind.BREACH for event in after_breach.events)
    assert after_breach.enemies[0].health < after_plain.enemies[0].health


def test_the_whole_fight_stays_reproducible(content: GameContent, brawler: Character) -> None:
    def play() -> tuple[int, int, str]:
        state = start_combat(content, brawler, (an_enemy(),), seed=b"same")
        for turn in range(6):
            state = resolve_turn(content, brawler, state, ATTACK, f"turn-{turn}".encode())
        return state.player.health, state.enemies[0].health, state.intent

    assert play() == play()
