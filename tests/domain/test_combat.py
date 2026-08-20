"""Combat engine behaviour and the content/engine contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillKind, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    CombatAction,
    CombatOutcome,
    CombatState,
    EventKind,
)
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules.combat import resolve_turn, start_combat
from mmorpg.domain.rules.skill_effects import known_effects, spec_for

SEED = b"combat-seed-0001"


def make_enemy(level: int = 3, health: int = 60, damage: int = 8, name: str = "Волк") -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=level,
        max_health=health,
        damage=damage,
        armor=2,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=12,
    )


@pytest.fixture
def fighter(warrior: Character) -> Character:
    return replace(
        warrior,
        level=10,
        loadout=SkillLoadout(
            actives=("warrior_cleave", "warrior_taunt", "warrior_shield_bash", None, None, None),
            racial="race_human_second_wind",
            # Постоянное умение слота не занимает: изучено - значит работает.
            ranks={"warrior_toughness": 1},
        ),
    )


def seed_for(turn: int) -> bytes:
    """A distinct seed per turn, the way the application layer supplies them."""
    return turn.to_bytes(16, "big")


def attack(content: GameContent, fighter: Character, state: CombatState, seed: bytes = SEED):
    return resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.ATTACK), seed)


def act_until_hit(
    content: GameContent,
    fighter: Character,
    state: CombatState,
    action: CombatAction,
) -> CombatState:
    """Resolve the action with successive seeds until the attack actually lands.

    Attacks can miss, and every seed is deterministic, so a test that needs a
    landed hit walks seeds instead of hoping the fixed one rolls well.
    """
    for attempt in range(50):
        after = resolve_turn(content, fighter, state, action, seed_for(attempt))
        if any(
            event.kind in {EventKind.DAMAGE, EventKind.CRIT}
            for event in after.events
            if event.target != after.player.name
        ):
            return after
    raise AssertionError("no seed produced a landed hit")


# --- the content contract -------------------------------------------


def test_every_content_effect_has_an_implementation(content: GameContent) -> None:
    """Content may add skills freely, but not behaviours the engine cannot run."""
    used = {skill.effect for skill in content.skills if skill.kind is SkillKind.ACTIVE}
    missing = sorted(used - known_effects())
    assert not missing, f"effects declared in content but not implemented: {missing}"


def test_every_implemented_effect_is_used_by_content(content: GameContent) -> None:
    """The other direction: no dead specs drifting out of sync with the game."""
    used = {skill.effect for skill in content.skills if skill.kind is SkillKind.ACTIVE}
    assert not sorted(known_effects() - used)


def test_spec_lookup_fails_loudly() -> None:
    with pytest.raises(KeyError, match="no implementation"):
        spec_for("no_such_effect")


# --- starting a fight ------------------------------------------------


def test_combat_starts_at_full_health(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    assert state.turn == 1
    assert state.outcome is CombatOutcome.ONGOING
    assert state.player.health == state.player.max_health
    assert state.player.resource == state.player.max_resource
    assert len(state.living_enemies) == 1


def test_group_fights_are_supported(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(), make_enemy(name="Волчица")))
    assert len(state.enemies) == 2
    assert {enemy.index for enemy in state.enemies} == {0, 1}


# --- determinism -----------------------------------------------------


def test_a_turn_is_reproducible(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(),))
    assert attack(content, fighter, state) == attack(content, fighter, state)


def test_a_different_seed_can_change_the_roll(content: GameContent, fighter: Character) -> None:
    """Against an opponent well above your level the roll matters again.

    At or below your own level accuracy is capped and criticals are rare, so forty
    seeds legitimately produce one number - that is the point of measuring
    accuracy by the level gap. The randomness has to be found where it lives.
    """
    state = start_combat(content, fighter, (make_enemy(level=fighter.level + 15, health=400),))
    results = {
        attack(content, fighter, state, seed=seed_for(index)).enemies[0].health
        for index in range(40)
    }
    assert len(results) > 1


# --- basic flow ------------------------------------------------------


def test_attack_damages_the_enemy(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=500),))
    after = act_until_hit(content, fighter, state, CombatAction(kind=ActionKind.ATTACK))
    assert after.enemies[0].health < 500
    assert after.turn == 2


def test_attacks_can_miss(content: GameContent, fighter: Character) -> None:
    """Accuracy matters: over many seeds some attacks must fail to land."""
    state = start_combat(content, fighter, (make_enemy(health=5_000),))
    kinds = {
        event.kind
        for attempt in range(60)
        for event in attack(content, fighter, state, seed_for(attempt)).events
    }
    assert EventKind.MISS in kinds
    assert EventKind.DAMAGE in kinds


def test_enemies_answer_in_the_same_turn(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=500, damage=40),))
    after = attack(content, fighter, state)
    assert after.player.health < after.player.max_health or any(
        event.kind is EventKind.DODGE for event in after.events
    )


def test_defeating_the_last_enemy_ends_the_fight(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=1),))
    for turn in range(12):
        state = attack(content, fighter, state, seed_for(turn))
        if state.is_over:
            break
    assert state.outcome is CombatOutcome.VICTORY
    assert state.experience > 0
    assert state.gold > 0
    assert "wolf_pelt" in state.loot


def test_losing_ends_the_fight(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(level=200, health=90_000, damage=9_000),))
    for turn in range(30):
        state = attack(content, fighter, state, seed_for(turn))
        if state.is_over:
            break
    assert state.outcome is CombatOutcome.DEFEAT
    assert state.player.health == 0


def test_a_finished_fight_ignores_further_turns(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=1),))
    turn = 0
    while not state.is_over:
        state = attack(content, fighter, state, seed_for(turn))
        turn += 1
    frozen = attack(content, fighter, state)
    assert frozen.turn == state.turn
    assert frozen.outcome is state.outcome


# --- skills ----------------------------------------------------------


def test_using_a_skill_spends_the_resource(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=500),))
    after = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert after.player.resource < state.player.resource


def test_an_empty_slot_answers_instead_of_failing(content: GameContent, fighter: Character) -> None:
    """Empty slots stay on screen, so pressing one must produce a sentence, not a crash."""
    state = start_combat(content, fighter, (make_enemy(),))
    after = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=4), SEED)
    assert any(event.kind is EventKind.EMPTY_SLOT for event in after.events)
    assert after.outcome is CombatOutcome.ONGOING


def test_a_skill_on_cooldown_is_refused_politely(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=5_000),))
    state = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=2), SEED)
    assert state.player.cooldown_of("warrior_shield_bash") > 0
    blocked = resolve_turn(
        content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=2), SEED
    )
    assert any(event.kind is EventKind.ON_COOLDOWN for event in blocked.events)


def test_cooldowns_tick_down_and_expire(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=5_000),))
    state = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=2), SEED)
    remaining = state.player.cooldown_of("warrior_shield_bash")
    assert remaining == 3
    for _ in range(remaining):
        state = attack(content, fighter, state)
    assert state.player.cooldown_of("warrior_shield_bash") == 0


def test_not_enough_resource_is_refused_politely(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=5_000),))
    state = replace(state, player=replace(state.player, resource=0))
    after = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert any(event.kind is EventKind.NOT_ENOUGH_RESOURCE for event in after.events)


def test_rank_makes_a_skill_stronger(content: GameContent, fighter: Character) -> None:
    ranked = replace(fighter, loadout=fighter.loadout.with_rank("warrior_cleave", 5))
    enemies = (make_enemy(health=5_000),)
    cleave = CombatAction(kind=ActionKind.SKILL, slot=0)
    # The same seed lands the same hit or miss for both, so any difference in the
    # damage dealt comes from the rank alone.
    seed = seed_for(1)
    weak = resolve_turn(content, fighter, start_combat(content, fighter, enemies), cleave, seed)
    strong = resolve_turn(content, ranked, start_combat(content, ranked, enemies), cleave, seed)
    assert weak.enemies[0].health < 5_000, "the reference hit must land"
    assert strong.enemies[0].health < weak.enemies[0].health


def test_area_skills_hit_every_enemy(content: GameContent, fighter: Character) -> None:
    aoe = replace(
        fighter,
        level=20,
        # Вихрь клинков просит клинок: умение с требованием к оружию без оружия
        # не срабатывает, и это проверяет отдельный тест.
        equipment=fighter.equipment.equip("weapon", "sword@1#common"),
        loadout=replace(
            fighter.loadout, actives=("warrior_blade_whirl", None, None, None, None, None)
        ),
    )
    state = start_combat(
        content, aoe, (make_enemy(health=900), make_enemy(health=900, name="Волчица"))
    )
    whirl = CombatAction(kind=ActionKind.SKILL, slot=0)
    for attempt in range(50):
        after = resolve_turn(content, aoe, state, whirl, seed_for(attempt))
        if all(enemy.health < 900 for enemy in after.enemies):
            return
    raise AssertionError("an area skill should be able to hit both enemies")


def test_racial_skill_uses_the_separate_slot(content: GameContent, fighter: Character) -> None:
    """The racial active never competes with the six class slots."""
    state = start_combat(content, fighter, (make_enemy(health=900, damage=30),))
    state = replace(state, player=replace(state.player, health=state.player.max_health // 2))
    after = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.RACIAL), SEED)
    assert any(event.kind is EventKind.HEAL for event in after.events)


def test_debuffs_land_on_the_enemy(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=900),))
    after = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=1), SEED)
    assert len(after.enemies[0].effects) == 1
    assert after.enemies[0].effects.modifiers()["damage_percent"] < 0


# --- items and fleeing -----------------------------------------------


def test_potions_heal_without_taking_a_skill_slot(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=900),))
    state = replace(state, player=replace(state.player, health=10))
    after = resolve_turn(
        content,
        fighter,
        state,
        CombatAction(kind=ActionKind.ITEM, item_id="small_healing_potion"),
        SEED,
    )
    assert after.player.health > 10


def test_fleeing_can_end_the_fight(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=900),))
    outcomes = {
        resolve_turn(
            content, fighter, state, CombatAction(kind=ActionKind.FLEE), bytes([index] * 16)
        ).outcome
        for index in range(30)
    }
    assert CombatOutcome.FLED in outcomes
    assert CombatOutcome.ONGOING in outcomes


# --- upkeep ----------------------------------------------------------


def test_resource_regenerates_each_turn(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=5_000),))
    drained = replace(state, player=replace(state.player, resource=0))
    after = attack(content, fighter, drained)
    assert after.player.resource > 0


def test_effects_expire_over_time(content: GameContent, fighter: Character) -> None:
    state = start_combat(content, fighter, (make_enemy(health=9_000),))
    state = resolve_turn(content, fighter, state, CombatAction(kind=ActionKind.SKILL, slot=1), SEED)
    assert len(state.enemies[0].effects) == 1
    for _ in range(3):
        state = attack(content, fighter, state)
    assert len(state.enemies[0].effects) == 0


def test_combat_never_uses_a_wall_clock(content: GameContent, fighter: Character) -> None:
    """Rule 13: combat waits for the player as long as they need."""
    import inspect

    from mmorpg.domain.rules import combat

    source = inspect.getsource(combat)
    for forbidden in ("time.time", "datetime", "sleep", "timeout"):
        assert forbidden not in source
