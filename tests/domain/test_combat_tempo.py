"""Intent, trace and breach: the three rules that make a turn a choice.

The tags never add a button, so they have to be exact instead: the intent an
enemy announces is the intent it acts on, a repeat really does hit harder, and
three different tags really do buy a free turn. Thresholds are asserted against
the constants, so moving a number here is a deliberate act.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    ActionTag,
    CombatAction,
    CombatState,
    EnemyState,
    EventKind,
    Trace,
    counter_to,
)
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules.combat import (
    MOMENTUM_DAMAGE_PERCENT,
    MOMENTUM_STREAK,
    TurnTempo,
    enemy_intent,
    resolve_turn,
    start_combat,
)
from mmorpg.domain.rules.skill_effects import spec_for, tag_of

# An enemy's intent on turn 1 is decided by its initiative, so these three
# numbers pick the announcement a test wants to face.
PRESS_INITIATIVE = 8.0
PRECISION_INITIATIVE = 9.0
GUARD_INITIATIVE = 10.0

ATTACK = CombatAction(kind=ActionKind.ATTACK)


def make_enemy(
    *,
    initiative: float = PRESS_INITIATIVE,
    health: int = 4_000,
    damage: int = 40,
    armor: int = 0,
) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name="Волк",
        kind=EnemyKind.BEAST,
        level=5,
        max_health=health,
        damage=damage,
        armor=armor,
        initiative=initiative,
        loot=(),
        gold=10,
    )


@pytest.fixture
def fighter(warrior: Character) -> Character:
    """A warrior who can play all three tags: press, guard and precision."""
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", "warrior_breach", None, None, None),
            passives=(None, None, None),
            racial="race_human_second_wind",
        ),
    )


def seed_for(turn: int) -> bytes:
    return turn.to_bytes(16, "big")


def enemy_health(state: CombatState) -> int:
    return state.enemies[0].health


def hit_landed(state: CombatState) -> bool:
    return any(
        event.kind in {EventKind.DAMAGE, EventKind.CRIT}
        for event in state.events
        if event.target != state.player.name
    )


# --- the circle of tags ----------------------------------------------


def test_every_tag_is_countered_by_exactly_one_other() -> None:
    counters = {tag: counter_to(tag) for tag in ActionTag}
    assert set(counters.values()) == set(ActionTag), "the circle must be closed"
    assert all(tag is not counter for tag, counter in counters.items()), "nothing counters itself"


def test_a_skill_leaves_the_trace_its_effect_implies() -> None:
    assert tag_of(spec_for("damage")) is ActionTag.PRESS
    assert tag_of(spec_for("damage_pierce")) is ActionTag.PRECISION
    assert tag_of(spec_for("damage_execute")) is ActionTag.PRECISION
    assert tag_of(spec_for("debuff_vulnerable")) is ActionTag.PRECISION
    assert tag_of(spec_for("heal")) is ActionTag.GUARD
    assert tag_of(spec_for("shield")) is ActionTag.GUARD
    assert tag_of(spec_for("buff_free_cast")) is ActionTag.GUARD


def test_an_explicit_tag_overrides_the_category() -> None:
    """Pulling a blow onto yourself is a guard, whatever the category says."""
    assert spec_for("taunt").tag is ActionTag.GUARD
    assert tag_of(spec_for("taunt")) is ActionTag.GUARD


def test_every_content_effect_answers_with_a_tag(content: GameContent) -> None:
    for skill in content.skills:
        if skill.effect in {"", None}:
            continue
        try:
            spec = spec_for(skill.effect)
        except KeyError:
            continue  # passives declare modifiers, not effects
        assert tag_of(spec) in set(ActionTag), skill.code


# --- the trace --------------------------------------------------------


def test_a_trace_remembers_only_the_last_three_tags() -> None:
    trace = Trace()
    for tag in (ActionTag.PRESS, ActionTag.GUARD, ActionTag.PRECISION, ActionTag.PRESS):
        trace = trace.push(tag)
    assert trace.tags == (ActionTag.GUARD, ActionTag.PRECISION, ActionTag.PRESS)
    assert trace.last is ActionTag.PRESS


def test_a_streak_counts_only_the_repeats_that_close_the_trace() -> None:
    assert Trace().streak == 0
    assert Trace((ActionTag.PRESS,)).streak == 1
    assert Trace((ActionTag.GUARD, ActionTag.PRESS, ActionTag.PRESS)).streak == 2
    assert Trace((ActionTag.PRESS, ActionTag.PRESS, ActionTag.PRESS)).streak == 3


def test_three_different_tags_are_a_break_and_two_are_not() -> None:
    trace = Trace((ActionTag.PRESS, ActionTag.GUARD))
    assert trace.breaks_with(ActionTag.PRECISION)
    assert not trace.breaks_with(ActionTag.PRESS)
    assert not Trace((ActionTag.PRESS,)).breaks_with(ActionTag.GUARD), "two tags are not three"


# --- what an enemy announces -----------------------------------------


def test_an_intent_is_the_same_every_time_it_is_asked() -> None:
    state = EnemyState.spawn(make_enemy(), index=0)
    assert enemy_intent(state, 7) is enemy_intent(state, 7)


def test_intents_walk_the_circle_from_turn_to_turn() -> None:
    state = EnemyState.spawn(make_enemy(), index=0)
    announced = [enemy_intent(state, turn) for turn in range(1, 4)]
    assert set(announced) == set(ActionTag), "three turns show all three intents"


def test_two_enemies_in_one_fight_announce_apart() -> None:
    first = EnemyState.spawn(make_enemy(), index=0)
    second = EnemyState.spawn(make_enemy(), index=1)
    assert enemy_intent(first, 1) is not enemy_intent(second, 1)


def test_a_wounded_enemy_always_closes_up() -> None:
    hurt = replace(EnemyState.spawn(make_enemy(health=100), index=0), health=25)
    assert all(enemy_intent(hurt, turn) is ActionTag.GUARD for turn in range(1, 7))


def test_an_announcement_holds_even_when_the_blow_lands_first(
    content: GameContent, fighter: Character
) -> None:
    """The intent is read before the player moves, so it cannot be taken back.

    An enemy one hit away from the wounded line would rather guard - but it said
    press, and the promise is what the player chose against.
    """
    beast = make_enemy(initiative=PRESS_INITIATIVE, health=40_000, damage=200)
    state = start_combat(content, fighter, (beast,))
    assert enemy_intent(state.enemies[0], state.turn) is ActionTag.PRESS

    about_to_break = replace(state, enemies=(replace(state.enemies[0], health=10_001),))
    already_wounded = replace(state, enemies=(replace(state.enemies[0], health=9_999),))
    assert enemy_intent(already_wounded.enemies[0], state.turn) is ActionTag.GUARD

    for attempt in range(40):
        seed = seed_for(attempt)
        pressed = resolve_turn(content, fighter, about_to_break, ATTACK, seed)
        guarded = resolve_turn(content, fighter, already_wounded, ATTACK, seed)
        if not hit_landed(pressed) or guarded.player.health == guarded.player.max_health:
            continue
        assert pressed.enemies[0].health < 10_000, "the hit really did wound it"
        assert pressed.player.health < guarded.player.health, "the announced press still landed"
        return
    pytest.fail("no seed both wounded the enemy and let its blow land")


# --- momentum ---------------------------------------------------------


def test_repeating_a_tag_hits_harder(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    repeated = replace(state, trace=Trace((ActionTag.PRESS,)))
    fresh = replace(state, trace=Trace((ActionTag.GUARD,)))

    for attempt in range(40):
        seed = seed_for(attempt)
        with_momentum = resolve_turn(content, fighter, repeated, ATTACK, seed)
        without = resolve_turn(content, fighter, fresh, ATTACK, seed)
        if not hit_landed(without):
            continue
        dealt = 4_000 - enemy_health(with_momentum)
        plain = 4_000 - enemy_health(without)
        assert dealt > plain
        expected = plain * (1.0 + MOMENTUM_DAMAGE_PERCENT / 100.0)
        assert abs(dealt - expected) <= 1, "momentum is the declared percentage, no more"
        assert any(event.kind is EventKind.MOMENTUM for event in with_momentum.events)
        return
    pytest.fail("no seed landed the reference hit")


def test_momentum_needs_the_declared_streak(content: GameContent, fighter: Character) -> None:
    assert MOMENTUM_STREAK == 2
    state = start_combat(content, fighter, (make_enemy(),))
    first = resolve_turn(content, fighter, state, ATTACK, seed_for(1))
    assert not any(event.kind is EventKind.MOMENTUM for event in first.events)
    assert first.trace.streak == 1

    second = resolve_turn(content, fighter, first, ATTACK, seed_for(2))
    assert any(event.kind is EventKind.MOMENTUM for event in second.events)
    assert second.trace.streak == 2


# --- the breakthrough -------------------------------------------------


def three_different_tags(
    content: GameContent, fighter: Character, state: CombatState
) -> CombatState:
    """Press, then guard, then precision: cleave, taunt, breach."""
    for slot, turn in ((0, 1), (1, 2), (2, 3)):
        state = resolve_turn(
            content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=slot), seed_for(turn)
        )
    return state


def test_three_different_tags_buy_a_free_turn(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(damage=400),))
    broken = three_different_tags(content, fighter, state)

    assert any(event.kind is EventKind.BREAKTHROUGH for event in broken.events)
    answered = [
        event
        for event in broken.events
        if event.kind is EventKind.DAMAGE and event.target == broken.player.name
    ]
    assert not answered, "a broken enemy does not answer this turn"


def test_a_breakthrough_spends_the_trace(content: GameContent, fighter: Character) -> None:
    """Otherwise cycling the same three tags would break every turn for free."""
    state = start_combat(content, fighter, (make_enemy(damage=400),))
    broken = three_different_tags(content, fighter, state)
    assert broken.trace.tags == ()

    again = resolve_turn(content, fighter, broken, ATTACK, seed_for(4))
    assert not any(event.kind is EventKind.BREAKTHROUGH for event in again.events)


# --- the breach -------------------------------------------------------


def test_a_counter_tag_opens_a_breach(content: GameContent, fighter: Character) -> None:
    """Precision was announced; a press is inside the reach before it lands."""
    state = start_combat(content, fighter, (make_enemy(initiative=PRECISION_INITIATIVE),))
    after = resolve_turn(content, fighter, state, ATTACK, seed_for(1))
    assert any(event.kind is EventKind.BREACH for event in after.events)


def test_a_matching_tag_opens_nothing(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(initiative=PRESS_INITIATIVE),))
    after = resolve_turn(content, fighter, state, ATTACK, seed_for(1))
    assert not any(event.kind is EventKind.BREACH for event in after.events)


def test_a_breach_takes_the_armour_out_of_the_count(
    content: GameContent, fighter: Character
) -> None:
    armoured = start_combat(
        content, fighter, (make_enemy(initiative=PRECISION_INITIATIVE, armor=300),)
    )
    plated = start_combat(content, fighter, (make_enemy(initiative=GUARD_INITIATIVE, armor=300),))
    for attempt in range(40):
        seed = seed_for(attempt)
        breached = resolve_turn(content, fighter, armoured, ATTACK, seed)
        blocked = resolve_turn(content, fighter, plated, ATTACK, seed)
        if not hit_landed(blocked):
            continue
        assert 4_000 - enemy_health(breached) > 4_000 - enemy_health(blocked)
        return
    pytest.fail("no seed landed the reference hit")


def test_tempo_reads_the_armour_off_the_announcement() -> None:
    tempo = TurnTempo(intents={0: ActionTag.GUARD}, tag=ActionTag.PRECISION)
    assert tempo.breached(0)
    assert tempo.armor_scale(0) == 0.0
    assert tempo.armor_scale(1) == 1.0, "an enemy that announced nothing keeps its armour"

    guarding = TurnTempo(intents={0: ActionTag.GUARD}, tag=ActionTag.PRESS)
    assert not guarding.breached(0)
    assert guarding.armor_scale(0) > 1.0, "an announced guard is worth more armour"


# --- what the intent does to the enemy's own blow ---------------------


def test_a_press_costs_more_than_a_guard(content: GameContent, fighter: Character) -> None:
    pressing = start_combat(content, fighter, (make_enemy(initiative=PRESS_INITIATIVE),))
    guarding = start_combat(content, fighter, (make_enemy(initiative=GUARD_INITIATIVE),))
    for attempt in range(40):
        seed = seed_for(attempt)
        pressed = resolve_turn(content, fighter, pressing, ATTACK, seed)
        guarded = resolve_turn(content, fighter, guarding, ATTACK, seed)
        if guarded.player.health == guarded.player.max_health:
            continue  # the blow was dodged; try another seed
        assert pressed.player.health < guarded.player.health
        return
    pytest.fail("no seed landed an enemy blow")


def test_an_announced_precision_is_not_dodged(content: GameContent, fighter: Character) -> None:
    """A blow aimed in advance is answered or taken, never side-stepped."""
    nimble = ActiveEffect(
        id="test_nimble", name="Проба", modifiers={"dodge_percent": 100.0}, turns_left=5
    )
    precise = start_combat(content, fighter, (make_enemy(initiative=PRECISION_INITIATIVE),))
    precise = replace(
        precise, player=replace(precise.player, effects=precise.player.effects.apply(nimble))
    )
    pressing = start_combat(content, fighter, (make_enemy(initiative=PRESS_INITIATIVE),))
    pressing = replace(
        pressing, player=replace(pressing.player, effects=pressing.player.effects.apply(nimble))
    )

    hit = resolve_turn(content, fighter, precise, ATTACK, seed_for(1))
    dodged = resolve_turn(content, fighter, pressing, ATTACK, seed_for(1))
    assert hit.player.health < hit.player.max_health
    assert any(event.kind is EventKind.DODGE for event in dodged.events)


# --- actions that leave no trace --------------------------------------


def test_a_refused_action_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    empty = resolve_turn(
        content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=4), seed_for(1)
    )
    assert any(event.kind is EventKind.EMPTY_SLOT for event in empty.events)
    assert empty.trace.tags == ()

    broke = replace(state, player=replace(state.player, resource=0))
    poor = resolve_turn(
        content, fighter, broke, CombatAction(kind=ActionKind.SKILL, slot=0), seed_for(1)
    )
    assert any(event.kind is EventKind.NOT_ENOUGH_RESOURCE for event in poor.events)
    assert poor.trace.tags == ()


def test_a_skill_on_cooldown_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    used = resolve_turn(
        content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=1), seed_for(1)
    )
    assert used.trace.tags == (ActionTag.GUARD,)
    blocked = resolve_turn(
        content, fighter, used, CombatAction(kind=ActionKind.SKILL, slot=1), seed_for(2)
    )
    assert any(event.kind is EventKind.ON_COOLDOWN for event in blocked.events)
    assert blocked.trace.tags == (ActionTag.GUARD,), "a refusal is not a move"


def test_running_away_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    pressed = resolve_turn(content, fighter, state, ATTACK, seed_for(1))
    fled = resolve_turn(content, fighter, pressed, CombatAction(kind=ActionKind.FLEE), seed_for(2))
    assert fled.trace.tags == (ActionTag.PRESS,)


def test_a_stunned_player_neither_moves_nor_breaks(
    content: GameContent, fighter: Character
) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    state = replace(
        state,
        player=replace(state.player, stunned=1),
        trace=Trace((ActionTag.PRESS, ActionTag.GUARD)),
    )
    after = resolve_turn(
        content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=2), seed_for(1)
    )
    assert any(event.kind is EventKind.TURN_SKIPPED for event in after.events)
    assert after.trace.tags == (ActionTag.PRESS, ActionTag.GUARD)
    assert not any(event.kind is EventKind.BREAKTHROUGH for event in after.events)


def test_a_potion_leaves_a_guard(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    state = replace(state, player=replace(state.player, health=10))
    after = resolve_turn(
        content,
        fighter,
        state,
        CombatAction(kind=ActionKind.ITEM, item_id="small_healing_potion"),
        seed_for(1),
    )
    assert after.trace.tags == (ActionTag.GUARD,)


# --- determinism ------------------------------------------------------


def test_the_whole_tempo_replays_from_the_seed(content: GameContent, fighter: Character) -> None:
    def play() -> CombatState:
        state = start_combat(content, fighter, (make_enemy(damage=120),))
        for turn, slot in enumerate((0, 1, 2, 0, 0), start=1):
            state = resolve_turn(
                content,
                fighter,
                state,
                CombatAction(kind=ActionKind.SKILL, slot=slot),
                seed_for(turn),
            )
        return state

    first, second = play(), play()
    assert first == second
    assert first.trace.tags == second.trace.tags
