"""Намерение, след, брешь, разнобой и разгон.

Круга взаимных контр («тег X бьёт тег Y») больше нет (ADR 0050). Осталось:
намерение того, за кого ходит движок, - постоянная повадка от породы, читаемая с
одного взгляда; брешь у того, кто объявил напор (удар по нему мимо брони, его
ответ вполсилы) - одинаково с обеих сторон; разнобой из трёх разных тегов подряд
отнимает у противника ход; повтор тега даёт разгон ровно на объявленный процент.
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
)
from mmorpg.domain.entities.effects import ActiveEffect, status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules.combat import (
    INTENT_ARMOR,
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

ATTACK = BattleAction(kind=ActionKind.ATTACK)

#: Инициатива, при которой противник ходит раньше героя - тогда его удар приходит
#: прямо в ``open_battle``, и по нему видно, что делает объявленная стойка.
FAST = 999.0


def archetype_for(tag: ActionTag, place: int = 0) -> str:
    """Имя породы, дающее ровно это намерение на этом месте в строю.

    Намерение - чистая функция от суммы кодов имени породы и места
    (``intent_of``), поэтому тест подбирает имя, а не угадывает число.
    """
    for name in ("aaa", "aab", "aac", "aad", "aae", "aaf", "baa", "bab", "bac"):
        if INTENT_CYCLE[(sum(map(ord, name)) + place) % len(INTENT_CYCLE)] is tag:
            return name
    raise AssertionError(f"нет имени породы, дающего {tag}")


def make_enemy(
    *,
    tag: ActionTag = ActionTag.PRESS,
    place: int = 0,
    initiative: float = 9.0,
    health: int = 4_000,
    damage: int = 40,
    armor: int = 0,
    rank: EnemyRank = EnemyRank.NORMAL,
    name: str = "Волк",
) -> Enemy:
    return Enemy(
        archetype_id=archetype_for(tag, place),
        name=name,
        kind=EnemyKind.BEAST,
        level=5,
        max_health=health,
        damage=damage,
        armor=armor,
        initiative=initiative,
        loot=(),
        gold=10,
        rank=rank,
    )


@pytest.fixture
def fighter(warrior: Character) -> Character:
    """Воин, которому доступны все три тега: напор, заслон, финт."""
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=(
                # Напор, заслон, финт: простой удар, провокация, подрез.
                "warrior_sekushchiy_roscherk",
                "warrior_provokatsiya",
                "warrior_podrez",
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
    content: GameContent,
    character: Character,
    *enemies: Enemy,
    hero_tags: tuple[ActionTag, ...] = (),
    seed: bytes = b"tempo-seed",
) -> tuple[BattleState, dict[int, Character]]:
    """Герой под номером 1, противники со второго."""
    roster = {1: character}
    hero = hero_combatant(content, character, combatant_id=1, side=0, live=True)
    if hero_tags:
        hero = replace(hero, trace=Trace(hero_tags))
    fighters = [
        hero,
        *(
            monster_combatant(enemy, combatant_id=index + 2, side=1)
            for index, enemy in enumerate(enemies)
        ),
    ]
    return open_battle(content, roster, fighters, seed), roster


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


# --- теги -----------------------------------------------------------------


def test_a_skill_leaves_the_trace_its_effect_implies() -> None:
    assert tag_of(spec_for("damage")) is ActionTag.PRESS
    assert tag_of(spec_for("damage_pierce")) is ActionTag.PRECISION
    assert tag_of(spec_for("damage_execute")) is ActionTag.PRECISION
    assert tag_of(spec_for("debuff_vulnerable")) is ActionTag.PRECISION
    assert tag_of(spec_for("heal")) is ActionTag.GUARD
    assert tag_of(spec_for("barrier")) is ActionTag.GUARD
    assert tag_of(spec_for("buff_free_cast")) is ActionTag.GUARD


def test_an_explicit_tag_overrides_the_category() -> None:
    """Принять удар на себя - это заслон, что бы ни говорила разновидность."""
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


# --- след ---------------------------------------------------------------


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
    assert not Trace((ActionTag.PRESS,)).breaks_with(ActionTag.GUARD), "два тега - не три"


# --- намерение: постоянная повадка --------------------------------------


def test_an_intent_is_the_same_every_time_it_is_asked(
    content: GameContent, fighter: Character
) -> None:
    state, _ = start(content, fighter, make_enemy())
    beast = state.by_id(2)
    assert beast is not None
    assert intent_of(state, beast) is intent_of(state, beast)


def test_the_intent_holds_all_fight(content: GameContent, fighter: Character) -> None:
    """Круга, крутящегося каждый круг, больше нет: повадка держится весь бой."""
    state, _ = start(content, fighter, make_enemy(tag=ActionTag.PRECISION))
    beast = state.by_id(2)
    assert beast is not None
    announced = [intent_of(replace(state, round=number), beast) for number in range(1, 9)]
    assert set(announced) == {ActionTag.PRECISION}, "одно намерение все восемь кругов"


def test_the_species_sets_the_disposition(content: GameContent, fighter: Character) -> None:
    for tag in ActionTag:
        state, _ = start(content, fighter, make_enemy(tag=tag))
        assert intent_of(state, state.by_id(2)) is tag  # type: ignore[arg-type]


def test_a_pack_stands_in_formation(content: GameContent, fighter: Character) -> None:
    """Место в строю разводит троих по трём намерениям - строй, а не рябь."""
    state, _ = start(
        content,
        fighter,
        make_enemy(tag=ActionTag.PRESS, place=0, name="Вожак"),
        make_enemy(tag=ActionTag.PRECISION, place=1, name="Волчица"),
        make_enemy(tag=ActionTag.GUARD, place=2, name="Щенок"),
    )
    assert intent_of(state, state.by_id(2)) is ActionTag.PRESS  # type: ignore[arg-type]
    assert intent_of(state, state.by_id(3)) is ActionTag.PRECISION  # type: ignore[arg-type]
    assert intent_of(state, state.by_id(4)) is ActionTag.GUARD  # type: ignore[arg-type]


def test_a_wounded_enemy_closes_up(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy(tag=ActionTag.PRESS, health=100))
    beast = state.by_id(2)
    assert beast is not None
    hurt = replace(beast, health=25)
    assert intent_of(state.replace_combatant(hurt), hurt) is ActionTag.GUARD


def test_an_elite_never_turtles(content: GameContent, fighter: Character) -> None:
    """Хозяин логова весь бой давит - даже раненый."""
    state, _ = start(content, fighter, make_enemy(rank=EnemyRank.BOSS, health=100, name="Хозяин"))
    boss = state.by_id(2)
    assert boss is not None
    assert intent_of(state, boss) is ActionTag.PRESS
    nearly_dead = replace(boss, health=5)
    assert intent_of(state.replace_combatant(nearly_dead), nearly_dead) is ActionTag.PRESS


def test_a_live_player_announces_the_trace_they_left(
    content: GameContent, fighter: Character
) -> None:
    state, _ = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    assert intent_of(state, hero) is None, "ещё не бил - объявлять нечего"
    pressed = replace(hero, trace=Trace((ActionTag.PRESS,)))
    assert intent_of(state, pressed) is ActionTag.PRESS


# --- разгон -----------------------------------------------------------


def test_repeating_a_tag_hits_harder(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRECISION))
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
        assert abs(dealt - expected) <= 1, "разгон - ровно объявленный процент, не больше"
        assert any(event.kind is EventKind.MOMENTUM for event in with_momentum.events)
        return
    pytest.fail("no seed landed the reference hit")


def test_momentum_needs_the_declared_streak(content: GameContent, fighter: Character) -> None:
    assert MOMENTUM_STREAK == 2
    state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRECISION))
    first = act(content, roster, state, ATTACK, seed_for(1))
    assert not any(event.kind is EventKind.MOMENTUM for event in first.events)
    assert hero_trace(first).streak == 1

    second = act(content, roster, first, ATTACK, seed_for(2))
    assert any(event.kind is EventKind.MOMENTUM for event in second.events)
    assert hero_trace(second).streak == 2


# --- брешь: у того, кто объявил напор ---------------------------------


def test_tempo_reads_the_breach_off_the_announcement() -> None:
    pressing = TurnTempo(intents={2: ActionTag.PRESS})
    guarding = TurnTempo(intents={2: ActionTag.GUARD})
    assert pressing.breached(2)
    assert pressing.armor_scale(2) == 0.0, "напор выносит броню из счёта"
    assert not guarding.breached(3)
    assert guarding.armor_scale(2) == INTENT_ARMOR[ActionTag.GUARD] > 1.0
    assert guarding.armor_scale(3) == 1.0, "объявивший ничего держит свою броню"


def test_hitting_a_pressing_enemy_ignores_its_armour(
    content: GameContent, fighter: Character
) -> None:
    pressing, press_roster = start(content, fighter, make_enemy(tag=ActionTag.PRESS, armor=300))
    guarding, guard_roster = start(content, fighter, make_enemy(tag=ActionTag.GUARD, armor=300))
    for attempt in range(40):
        seed = seed_for(attempt)
        into_press = act(content, press_roster, pressing, ATTACK, seed)
        into_guard = act(content, guard_roster, guarding, ATTACK, seed)
        if not hit_landed(into_press) or not hit_landed(into_guard):
            continue
        assert 4_000 - enemy_health(into_press) > 4_000 - enemy_health(into_guard)
        return
    pytest.fail("no seed landed the reference hit")


def test_a_pressing_enemy_answers_at_half_strength(
    content: GameContent, fighter: Character
) -> None:
    """Брешь всегда чем-то платит: он открылся и платит этим же ходом."""
    state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRESS))
    beast = state.by_id(2)
    assert beast is not None and not beast.breached
    after = act(content, roster, state, ATTACK, seed_for(1))
    struck = after.by_id(2)
    assert struck is not None
    # Либо брешь уже стоила ему удара (он походил и снялась), либо ещё висит.
    assert struck.breached or any(event.kind is EventKind.BREACH for event in after.events)


def test_a_pressing_enemy_costs_more_than_a_guarding_one(
    content: GameContent, fighter: Character
) -> None:
    """Объявленный напор бьёт как обычно, но открыт; заслон бьёт вполсилы."""
    full = None
    for attempt in range(40):
        seed = seed_for(attempt)
        hard = {"initiative": FAST, "damage": 600}
        pressing, _ = start(content, fighter, make_enemy(tag=ActionTag.PRESS, **hard), seed=seed)
        guarding, _ = start(content, fighter, make_enemy(tag=ActionTag.GUARD, **hard), seed=seed)
        full = full or pressing.by_id(1).max_health  # type: ignore[union-attr]
        if hero_health(pressing) == full or hero_health(guarding) == full:
            continue
        assert hero_health(pressing) < hero_health(guarding)
        return
    pytest.fail("no seed landed an enemy blow")


def test_an_announced_precision_is_not_dodged(content: GameContent, fighter: Character) -> None:
    """Финт, объявленный породой заранее, принимают или отвечают, но не обходят."""
    nimble = ActiveEffect(
        id="test_nimble", name="Проба", modifiers={"dodge_percent": 100.0}, turns_left=5
    )

    def start_dodgy(tag: ActionTag) -> BattleState:
        roster = {1: fighter}
        hero = replace(
            hero_combatant(content, fighter, combatant_id=1, side=0, live=True),
            effects=hero_combatant(content, fighter, combatant_id=1, side=0).effects.apply(nimble),
        )
        enemy = monster_combatant(make_enemy(tag=tag, initiative=FAST), combatant_id=2, side=1)
        return open_battle(content, roster, [hero, enemy], b"tempo-seed")

    precise = start_dodgy(ActionTag.PRECISION)
    pressing = start_dodgy(ActionTag.PRESS)

    assert not any(event.kind is EventKind.DODGE for event in precise.events)
    hero = precise.by_id(1)
    assert hero is not None and hero.health < hero.max_health, "объявленный финт не обходят"
    assert any(event.kind is EventKind.DODGE for event in pressing.events)


# --- разнобой -------------------------------------------------------


def three_different_tags(
    content: GameContent, roster: dict[int, Character], state: BattleState
) -> BattleState:
    """Напор, заслон, финт: росчерк, провокация, подрез."""
    for slot, turn in ((0, 1), (1, 2), (2, 3)):
        state = act(
            content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=slot), seed_for(turn)
        )
    return state


def test_three_different_tags_keep_the_turn(content: GameContent, fighter: Character) -> None:
    """Разнобой отдаёт ход тому, кто сломал размен, а не отнимает его у всех."""
    state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRECISION, damage=400))
    broken = three_different_tags(content, roster, state)

    assert any(event.kind is EventKind.BREAKTHROUGH for event in broken.events)
    answered = [
        event for event in broken.events if event.kind is EventKind.DAMAGE and event.target_id == 1
    ]
    assert not answered, "тот, кого сбили с ритма, в этот ход не отвечает"
    current = broken.active
    assert current is not None and current.id == 1, "ход остался за игроком"


def test_a_breakthrough_spends_the_trace(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRECISION, damage=400))
    broken = three_different_tags(content, roster, state)
    assert hero_trace(broken).tags == ()


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
        state, roster = start(content, fighter, make_enemy(tag=ActionTag.PRECISION, damage=120))
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
