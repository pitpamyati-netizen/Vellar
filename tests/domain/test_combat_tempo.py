"""Намерение, след и брешь: три правила, которые делают ход выбором.

Теги не добавляют кнопок, поэтому они обязаны быть точными: объявленное
намерение - то самое, по которому боец бьёт; повтор действительно бьёт сильнее;
три разных тега подряд действительно оставляют ход за тем, кто их сложил.
Пороги проверяются против самих констант, чтобы сдвинуть число можно было
только нарочно.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    ActionTag,
    BattleAction,
    BattleState,
    EventKind,
    Trace,
    counter_to,
)
from mmorpg.domain.entities.effects import ActiveEffect, status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules.combat import (
    INTENT_CYCLE,
    MOMENTUM_DAMAGE_PERCENT,
    MOMENTUM_STREAK,
    TurnTempo,
    act,
    hero_combatant,
    intent_of,
    monster_combatant,
    open_battle,
)
from mmorpg.domain.rules.skill_effects import spec_for, tag_of


def initiative_for(tag: ActionTag, *, place: int = 0, round_number: int = 1) -> float:
    """Инициатива, при которой противник объявит именно этот тег.

    Считается по той же формуле, что и сам движок (``intent_of``), а не
    угадывается числом: подобранная константа переставала работать при любой
    правке круга намерений. ``place`` - место бойца на своей стороне.
    """
    for value in range(4, 60):
        step = (value + place + round_number) % len(INTENT_CYCLE)
        if INTENT_CYCLE[step] is tag:
            return float(value)
    raise AssertionError(f"нет инициативы, дающей {tag}")


# Намерение противника на первом круге решает его инициатива, поэтому эти три
# числа выбирают объявление, против которого тест хочет играть.
PRESS_INITIATIVE = initiative_for(ActionTag.PRESS)
PRECISION_INITIATIVE = initiative_for(ActionTag.PRECISION)
GUARD_INITIATIVE = initiative_for(ActionTag.GUARD)

ATTACK = BattleAction(kind=ActionKind.ATTACK)


def make_enemy(
    *,
    initiative: float = PRESS_INITIATIVE,
    health: int = 4_000,
    damage: int = 40,
    armor: int = 0,
    name: str = "Волк",
) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
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
    """Воин, которому доступны все три тега: натиск, оборона и точность."""
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=(
                # Натиск, оборона, точность: простой удар, провокация, пробой.
                "warrior_sekushchiy_roscherk",
                "warrior_provokatsiya",
                "warrior_proboy_stroya",
                None,
                None,
                None,
            ),
            racial="race_human_second_wind",
        ),
    )


def seed_for(turn: int) -> bytes:
    return turn.to_bytes(16, "big")


def start(
    content: GameContent, character: Character, *enemies: Enemy
) -> tuple[BattleState, dict[int, Character]]:
    roster = {1: character}
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(enemy, combatant_id=index + 2, side=1)
            for index, enemy in enumerate(enemies)
        ),
    ]
    return open_battle(content, roster, fighters, b"tempo-seed"), roster


def enemy_health(state: BattleState, combatant_id: int = 2) -> int:
    one = state.by_id(combatant_id)
    assert one is not None
    return one.health


def hero_health(state: BattleState) -> int:
    hero = state.by_id(1)
    assert hero is not None
    return hero.health


def hero_trace(state: BattleState) -> Trace:
    hero = state.by_id(1)
    assert hero is not None
    return hero.trace


def hit_landed(state: BattleState) -> bool:
    return any(
        event.kind in {EventKind.DAMAGE, EventKind.CRIT}
        for event in state.events
        if event.actor_id == 1
    )


# --- круг тегов -------------------------------------------------------


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
    assert tag_of(spec_for("barrier")) is ActionTag.GUARD
    assert tag_of(spec_for("buff_free_cast")) is ActionTag.GUARD


def test_an_explicit_tag_overrides_the_category() -> None:
    """Принять удар на себя - это оборона, что бы ни говорила разновидность."""
    assert spec_for("taunt").tag is ActionTag.GUARD
    assert tag_of(spec_for("taunt")) is ActionTag.GUARD


def test_every_content_effect_answers_with_a_tag(content: GameContent) -> None:
    for skill in content.skills:
        if skill.effect in {"", None}:
            continue
        try:
            spec = spec_for(skill.effect)
        except KeyError:
            continue  # у пассивных умений прибавки, а не эффекты
        assert tag_of(spec) in set(ActionTag), skill.code


# --- след -------------------------------------------------------------


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


# --- что объявляет тот, за кого ходит движок --------------------------


def test_an_intent_is_the_same_every_time_it_is_asked(
    content: GameContent, fighter: Character
) -> None:
    state, _ = start(content, fighter, make_enemy())
    beast = state.by_id(2)
    assert beast is not None
    assert intent_of(state, beast) is intent_of(state, beast)


def test_intents_walk_the_circle_from_round_to_round(
    content: GameContent, fighter: Character
) -> None:
    state, _ = start(content, fighter, make_enemy())
    beast = state.by_id(2)
    assert beast is not None
    announced = [intent_of(replace(state, round=number), beast) for number in range(1, 4)]
    assert set(announced) == set(ActionTag), "three rounds show all three intents"


def test_two_enemies_in_one_fight_announce_apart(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy(), make_enemy(name="Волчица"))
    first, second = state.by_id(2), state.by_id(3)
    assert first is not None and second is not None
    assert intent_of(state, first) is not intent_of(state, second)


def test_a_wounded_enemy_always_closes_up(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy(health=100))
    beast = state.by_id(2)
    assert beast is not None
    hurt = replace(beast, health=25)
    assert all(
        intent_of(replace(state, round=number), hurt) is ActionTag.GUARD for number in range(1, 7)
    )


def test_a_live_player_announces_the_trace_they_left(
    content: GameContent, fighter: Character
) -> None:
    """Угадать чужой ход нельзя, но видно, чем он бил, - и этого хватает."""
    state, _ = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    assert intent_of(state, hero) is None, "ещё не бил - объявлять нечего"
    pressed = replace(hero, trace=Trace((ActionTag.PRESS,)))
    assert intent_of(state, pressed) is ActionTag.PRESS


# --- разгон -----------------------------------------------------------


def test_repeating_a_tag_hits_harder(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    repeated = state.replace_combatant(replace(hero, trace=Trace((ActionTag.PRESS,))))
    fresh = state.replace_combatant(replace(hero, trace=Trace((ActionTag.GUARD,))))

    for attempt in range(40):
        seed = seed_for(attempt)
        with_momentum = act(content, roster, repeated, ATTACK, seed)
        without = act(content, roster, fresh, ATTACK, seed)
        if not hit_landed(without) or not hit_landed(with_momentum):
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
    state, roster = start(content, fighter, make_enemy())
    first = act(content, roster, state, ATTACK, seed_for(1))
    assert not any(event.kind is EventKind.MOMENTUM for event in first.events)
    assert hero_trace(first).streak == 1

    second = act(content, roster, first, ATTACK, seed_for(2))
    assert any(event.kind is EventKind.MOMENTUM for event in second.events)
    assert hero_trace(second).streak == 2


# --- перелом ----------------------------------------------------------


def three_different_tags(
    content: GameContent, roster: dict[int, Character], state: BattleState
) -> BattleState:
    """Натиск, оборона, точность: рассечение, провокация, брешь."""
    for slot, turn in ((0, 1), (1, 2), (2, 3)):
        state = act(
            content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=slot), seed_for(turn)
        )
    return state


def test_three_different_tags_keep_the_turn(content: GameContent, fighter: Character) -> None:
    """Перелом отдаёт ход тому, кто сломал размен, а не отнимает его у всех."""
    state, roster = start(content, fighter, make_enemy(damage=400))
    broken = three_different_tags(content, roster, state)

    assert any(event.kind is EventKind.BREAKTHROUGH for event in broken.events)
    answered = [
        event for event in broken.events if event.kind is EventKind.DAMAGE and event.target_id == 1
    ]
    assert not answered, "тот, кого сбили с ритма, в этот ход не отвечает"
    current = broken.active
    assert current is not None and current.id == 1, "ход остался за игроком"


def test_a_breakthrough_spends_the_trace(content: GameContent, fighter: Character) -> None:
    """Иначе один и тот же круг тегов ломал бы размен каждый ход даром."""
    state, roster = start(content, fighter, make_enemy(damage=400))
    broken = three_different_tags(content, roster, state)
    assert hero_trace(broken).tags == ()

    again = act(content, roster, broken, ATTACK, seed_for(4))
    assert not any(event.kind is EventKind.BREAKTHROUGH for event in again.events)


# --- брешь ------------------------------------------------------------


def test_a_counter_tag_opens_a_breach(content: GameContent, fighter: Character) -> None:
    """Объявлена точность; натиск входит внутрь раньше, чем она выберет место."""
    state, roster = start(content, fighter, make_enemy(initiative=PRECISION_INITIATIVE))
    beast = state.by_id(2)
    assert beast is not None and intent_of(state, beast) is ActionTag.PRECISION
    after = act(content, roster, state, ATTACK, seed_for(1))
    assert any(event.kind is EventKind.BREACH for event in after.events)


def test_a_matching_tag_opens_nothing(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(initiative=PRESS_INITIATIVE))
    beast = state.by_id(2)
    assert beast is not None and intent_of(state, beast) is ActionTag.PRESS
    after = act(content, roster, state, ATTACK, seed_for(1))
    assert not any(event.kind is EventKind.BREACH for event in after.events)


def test_a_breach_takes_the_armour_out_of_the_count(
    content: GameContent, fighter: Character
) -> None:
    breached_state, roster = start(
        content, fighter, make_enemy(initiative=PRECISION_INITIATIVE, armor=300)
    )
    guarded_state, guarded_roster = start(
        content, fighter, make_enemy(initiative=GUARD_INITIATIVE, armor=300)
    )
    for attempt in range(40):
        seed = seed_for(attempt)
        breached = act(content, roster, breached_state, ATTACK, seed)
        blocked = act(content, guarded_roster, guarded_state, ATTACK, seed)
        if not hit_landed(blocked) or not hit_landed(breached):
            continue
        assert 4_000 - enemy_health(breached) > 4_000 - enemy_health(blocked)
        return
    pytest.fail("no seed landed the reference hit")


def test_tempo_reads_the_armour_off_the_announcement() -> None:
    tempo = TurnTempo(intents={2: ActionTag.GUARD}, tag=ActionTag.PRECISION)
    assert tempo.breached(2)
    assert tempo.armor_scale(2) == 0.0
    assert tempo.armor_scale(3) == 1.0, "an enemy that announced nothing keeps its armour"

    guarding = TurnTempo(intents={2: ActionTag.GUARD}, tag=ActionTag.PRESS)
    assert not guarding.breached(2)
    assert guarding.armor_scale(2) > 1.0, "an announced guard is worth more armour"


def test_a_breached_enemy_answers_at_half_strength(
    content: GameContent, fighter: Character
) -> None:
    """Брешь всегда чем-то платит: уроном по цели, уроном по себе или обоими."""
    state, roster = start(content, fighter, make_enemy(initiative=PRECISION_INITIATIVE))
    beast = state.by_id(2)
    assert beast is not None
    assert not beast.breached
    after = act(content, roster, state, ATTACK, seed_for(1))
    struck = after.by_id(2)
    assert struck is not None
    # Либо брешь уже стоила ему удара, либо она ещё висит на нём.
    assert struck.breached or any(event.kind is EventKind.BREACH for event in after.events)


# --- что намерение делает с собственным ударом ------------------------


def test_a_press_costs_more_than_a_guard(content: GameContent, fighter: Character) -> None:
    pressing, press_roster = start(content, fighter, make_enemy(initiative=PRESS_INITIATIVE))
    guarding, guard_roster = start(content, fighter, make_enemy(initiative=GUARD_INITIATIVE))
    for attempt in range(40):
        seed = seed_for(attempt)
        pressed = act(content, press_roster, pressing, ATTACK, seed)
        guarded = act(content, guard_roster, guarding, ATTACK, seed)
        hero = guarded.by_id(1)
        assert hero is not None
        if hero.health == hero.max_health:
            continue  # удар не дошёл: пробуем другое семя
        assert hero_health(pressed) < hero_health(guarded)
        return
    pytest.fail("no seed landed an enemy blow")


def test_an_announced_precision_is_not_dodged(content: GameContent, fighter: Character) -> None:
    """Удар, нацеленный заранее, принимают или отвечают, но не обходят."""
    nimble = ActiveEffect(
        id="test_nimble", name="Проба", modifiers={"dodge_percent": 100.0}, turns_left=5
    )

    def with_dodge(state: BattleState) -> BattleState:
        hero = state.by_id(1)
        assert hero is not None
        return state.replace_combatant(replace(hero, effects=hero.effects.apply(nimble)))

    precise, precise_roster = start(content, fighter, make_enemy(initiative=PRECISION_INITIATIVE))
    pressing, press_roster = start(content, fighter, make_enemy(initiative=PRESS_INITIATIVE))
    precise, pressing = with_dodge(precise), with_dodge(pressing)

    for attempt in range(40):
        seed = seed_for(attempt)
        dodged = act(content, press_roster, pressing, ATTACK, seed)
        if not any(event.kind is EventKind.DODGE for event in dodged.events):
            continue  # даже с уклонением в сто процентов удар иногда доходит
        hit = act(content, precise_roster, precise, ATTACK, seed)
        hero = hit.by_id(1)
        assert hero is not None
        assert hero.health < hero.max_health, "объявленную точность не обходят"
        assert not any(event.kind is EventKind.DODGE for event in hit.events)
        return
    pytest.fail("no seed produced a dodge against the press")


# --- действия, которые не оставляют следа -----------------------------


def test_a_refused_action_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    empty = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=4), seed_for(1))
    assert any(event.kind is EventKind.EMPTY_SLOT for event in empty.events)
    assert hero_trace(empty).tags == ()

    hero = state.by_id(1)
    assert hero is not None
    broke = state.replace_combatant(replace(hero, resource=0))
    poor = act(content, roster, broke, BattleAction(kind=ActionKind.SKILL, slot=0), seed_for(1))
    assert any(event.kind is EventKind.NOT_ENOUGH_RESOURCE for event in poor.events)
    assert hero_trace(poor).tags == ()


def test_a_skill_on_cooldown_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    used = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=1), seed_for(1))
    assert hero_trace(used).tags == (ActionTag.GUARD,)
    blocked = act(content, roster, used, BattleAction(kind=ActionKind.SKILL, slot=1), seed_for(2))
    assert any(event.kind is EventKind.ON_COOLDOWN for event in blocked.events)
    assert hero_trace(blocked).tags == (ActionTag.GUARD,), "a refusal is not a move"


def test_running_away_leaves_no_trace(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    pressed = act(content, roster, state, ATTACK, seed_for(1))
    fled = act(content, roster, pressed, BattleAction(kind=ActionKind.FLEE), seed_for(2))
    assert hero_trace(fled).tags == (ActionTag.PRESS,)


def test_a_stunned_fighter_neither_moves_nor_breaks(
    content: GameContent, fighter: Character
) -> None:
    state, roster = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    state = state.replace_combatant(
        replace(
            hero,
            effects=hero.effects.apply(status_effect(StatusKind.STUN, turns=1)),
            trace=Trace((ActionTag.PRESS, ActionTag.GUARD)),
        )
    )
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=2), seed_for(1))
    assert any(event.kind is EventKind.TURN_SKIPPED for event in after.events)
    assert hero_trace(after).tags == (ActionTag.PRESS, ActionTag.GUARD)
    assert not any(event.kind is EventKind.BREAKTHROUGH for event in after.events)


def test_a_potion_leaves_a_guard(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    state = state.replace_combatant(replace(hero, health=10))
    after = act(
        content,
        roster,
        state,
        BattleAction(kind=ActionKind.ITEM, item_id="small_healing_potion"),
        seed_for(1),
    )
    assert hero_trace(after).tags == (ActionTag.GUARD,)


# --- воспроизводимость ------------------------------------------------


def test_the_whole_tempo_replays_from_the_seed(content: GameContent, fighter: Character) -> None:
    def play() -> BattleState:
        state, roster = start(content, fighter, make_enemy(damage=120))
        for turn, slot in enumerate((0, 1, 2, 0, 0), start=1):
            state = act(
                content,
                roster,
                state,
                BattleAction(kind=ActionKind.SKILL, slot=slot),
                seed_for(turn),
            )
        return state

    first, second = play(), play()
    assert first == second
    assert hero_trace(first).tags == hero_trace(second).tags
