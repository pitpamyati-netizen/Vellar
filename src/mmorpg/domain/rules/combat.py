"""The turn-based combat engine.

One call to :func:`resolve_turn` runs the player's action, then every living
enemy's action, then the end-of-turn upkeep, and returns a brand new state with
the list of events that happened. There are no timers anywhere: the state simply
waits (accessibility rule 13).

All randomness comes from an explicit seed passed in by the caller, so a fight can
be replayed exactly - which is what makes it testable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionKind,
    CombatAction,
    CombatEvent,
    CombatOutcome,
    CombatState,
    EnemyState,
    EventKind,
    PlayerState,
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import Enemy
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import tempo as tempo_rules
from mmorpg.domain.rules.progression import experience_reward
from mmorpg.domain.rules.skill_effects import EffectCategory, EffectSpec, spec_for
from mmorpg.domain.rules.stats import DerivedStats, derived_stats, primary_stats
from mmorpg.domain.rules.tempo import Tag

BASIC_ATTACK_POWER = 8.0
STAT_DAMAGE_DIVISOR = 18.0
ARMOR_SOFTENER = 100.0
MIN_HIT_CHANCE = 40.0
MAX_HIT_CHANCE = 97.0
ENEMY_ACCURACY_BASE = 78.0
ENEMY_ACCURACY_PER_LEVEL = 0.15
BASE_FLEE_CHANCE = 45.0
LOW_HEALTH_THRESHOLD = 0.35


@dataclass(frozen=True, slots=True)
class TurnTempo:
    """What the trail and the announced intent are worth for one action.

    ``boost`` is momentum, ``breach`` means the armour of the target is open
    because the action counters what the enemy announced (Roadmap 1.1).
    """

    boost: float = 1.0
    breach: bool = False


# --- starting a fight ------------------------------------------------


def start_combat(
    content: GameContent,
    character: Character,
    enemies: tuple[Enemy, ...],
    *,
    seed: bytes = b"\x00",
) -> CombatState:
    """Build the opening state. The player always acts first on turn 1.

    A fight starts from the health the character walked in with: wounds carry
    over between nodes, and that is what makes a potion and an inn cost money.
    """
    stats = derived_stats(content, character)
    player = PlayerState(
        name=character.name,
        health=character.health_or(stats.max_health),
        max_health=stats.max_health,
        resource=stats.max_resource,
        max_resource=stats.max_resource,
        resource_name=stats.resource_name,
    )
    intent = tempo_rules.roll_intent(random.Random(int.from_bytes(seed, "big")))
    return CombatState(
        player=player,
        enemies=tuple(EnemyState.spawn(enemy, index) for index, enemy in enumerate(enemies)),
        intent=intent.value,
    )


# --- one turn --------------------------------------------------------


def resolve_turn(
    content: GameContent,
    character: Character,
    state: CombatState,
    action: CombatAction,
    seed: bytes,
) -> CombatState:
    """Resolve the player's action and the enemies' answer."""
    if state.is_over:
        return replace(state, events=())

    source = random.Random(int.from_bytes(seed, "big"))
    working = replace(state, events=())

    tag = _tag_for(content, character, action)
    trail = tempo_rules.extended(state.trail, tag)
    breach = bool(state.intent) and tempo_rules.counters(tag, Tag(state.intent))
    momentum = tempo_rules.has_momentum(trail)
    broke = tempo_rules.is_break(trail)
    pace = TurnTempo(boost=tempo_rules.damage_factor(trail), breach=breach)
    working = replace(working, trail=trail)

    if breach:
        working = working.with_events(CombatEvent(kind=EventKind.BREACH, tag=tag.value))
    if momentum:
        working = working.with_events(
            CombatEvent(kind=EventKind.MOMENTUM, tag=tag.value, amount=tempo_rules.streak(trail))
        )

    if working.player.stunned > 0:
        working = working.with_events(
            CombatEvent(kind=EventKind.TURN_SKIPPED, actor=working.player.name)
        )
        working = replace(
            working, player=replace(working.player, stunned=working.player.stunned - 1)
        )
    else:
        working = _player_action(content, character, working, action, source, pace)

    working = _check_outcome(content, character, working)
    if working.is_over:
        return working

    if broke:
        # A break costs the enemy its answer: the trail read three different
        # tags in a row and nobody on the other side kept up.
        working = working.with_events(CombatEvent(kind=EventKind.BREAK))
    else:
        working = _enemy_actions(content, character, working, source)
        working = _check_outcome(content, character, working)
        if working.is_over:
            return working

    return _announce_intent(_end_of_turn(content, character, working), source)


def _tag_for(content: GameContent, character: Character, action: CombatAction) -> Tag:
    """Which tag the chosen action leaves on the trail."""
    match action.kind:
        case ActionKind.SKILL | ActionKind.RACIAL:
            skill = _resolve_skill(content, character, action)
            return tempo_rules.tag_of_spec(spec_for(skill.effect) if skill else None)
        case ActionKind.ITEM | ActionKind.FLEE:
            return Tag.GUARD
        case _:
            return tempo_rules.tag_of_attack()


def _announce_intent(state: CombatState, source: random.Random) -> CombatState:
    """Say out loud what the enemy side prepares for the next turn."""
    if not state.living_enemies:
        return state
    intent = tempo_rules.roll_intent(source)
    return replace(state, intent=intent.value).with_events(
        CombatEvent(kind=EventKind.INTENT, actor=state.living_enemies[0].name, tag=intent.value)
    )


def _player_action(
    content: GameContent,
    character: Character,
    state: CombatState,
    action: CombatAction,
    source: random.Random,
    pace: TurnTempo,
) -> CombatState:
    match action.kind:
        case ActionKind.ATTACK:
            return _basic_attack(content, character, state, action.target, source, pace)
        case ActionKind.SKILL | ActionKind.RACIAL:
            return _use_skill(content, character, state, action, source, pace)
        case ActionKind.ITEM:
            return _use_item(content, state, action)
        case ActionKind.FLEE:
            return _try_flee(content, character, state, source)


# --- basic attack ----------------------------------------------------


def _basic_attack(
    content: GameContent,
    character: Character,
    state: CombatState,
    target_index: int,
    source: random.Random,
    pace: TurnTempo,
) -> CombatState:
    target = state.enemy_at(target_index) or state.first_living()
    if target is None:
        return state
    stats = derived_stats(content, character, state.player.effects)
    primary = primary_stats(content, character, state.player.effects)
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    klass = content.character_class(character.class_id)
    scaling = klass.key_stats[0] if klass.key_stats else None
    stat_value = primary[scaling] if scaling is not None else 0

    return _strike(
        state,
        target=target,
        power=BASIC_ATTACK_POWER + character.level * 1.5,
        stat_value=stat_value,
        stats=stats,
        modifiers=modifiers,
        spec=None,
        skill_name="Атака",
        source=source,
        pace=pace,
    )


# --- skills ----------------------------------------------------------


def _resolve_skill(
    content: GameContent, character: Character, action: CombatAction
) -> Skill | None:
    if action.kind is ActionKind.RACIAL:
        code = character.loadout.racial or content.race(character.race_id).active_code
        return content.skill(code)
    if action.slot is None:
        return None
    actives = character.loadout.actives
    if not 0 <= action.slot < len(actives):
        return None
    slotted = actives[action.slot]
    return content.skill(slotted) if slotted else None


def _use_skill(
    content: GameContent,
    character: Character,
    state: CombatState,
    action: CombatAction,
    source: random.Random,
    pace: TurnTempo,
) -> CombatState:
    skill = _resolve_skill(content, character, action)
    if skill is None:
        return state.with_events(CombatEvent(kind=EventKind.EMPTY_SLOT))

    player = state.player
    if player.cooldown_of(skill.code) > 0:
        return state.with_events(
            CombatEvent(
                kind=EventKind.ON_COOLDOWN,
                skill_name=skill.name,
                turns=player.cooldown_of(skill.code),
            )
        )

    modifiers = mods.collect_modifiers(content, character, player.effects)
    cost = _skill_cost(skill, modifiers, free=player.free_cast)
    cost = round(cost * skill_rules.cost_factor(character, skill))
    if cost > player.resource:
        return state.with_events(
            CombatEvent(kind=EventKind.NOT_ENOUGH_RESOURCE, skill_name=skill.name, amount=cost)
        )

    rank = character.loadout.rank_of(skill.code)
    power = skill.power_at_rank(rank) * skill_rules.power_factor(character, skill)
    spec = spec_for(skill.effect)

    player = replace(player, resource=player.resource - cost, free_cast=False)
    if skill.cooldown:
        # +1 because cooldowns tick down at the end of this same turn, so the skill
        # stays unavailable for exactly `cooldown` further turns.
        player = player.with_cooldown(skill.code, skill.cooldown + 1)
    working = replace(state, player=player)
    return _apply_spec(content, character, working, skill, spec, power, action.target, source, pace)


def _skill_cost(skill: Skill, modifiers: dict[str, float], *, free: bool) -> int:
    if free:
        return 0
    reduction = 1.0 - modifiers.get("cost_reduction_percent", 0.0) / 100.0
    return max(0, round(skill.cost * max(0.1, reduction)))


def _apply_spec(
    content: GameContent,
    character: Character,
    state: CombatState,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    target_index: int,
    source: random.Random,
    pace: TurnTempo,
) -> CombatState:
    stats = derived_stats(content, character, state.player.effects)
    primary = primary_stats(content, character, state.player.effects)
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    stat_value = primary[skill.scaling] if skill.scaling is not None else 0
    working = state

    if spec.special == "avoid_combat":
        if source.uniform(0, 100) < power + stats.crit_chance:
            return replace(working, outcome=CombatOutcome.AVOIDED).with_events(
                CombatEvent(kind=EventKind.AVOIDED, skill_name=skill.name)
            )
        return working.with_events(CombatEvent(kind=EventKind.FLEE_FAILED, skill_name=skill.name))

    if spec.category is EffectCategory.DAMAGE:
        targets = working.living_enemies if spec.aoe else _single_target(working, target_index)
        falloff = 1.0
        for target in targets:
            for _ in range(spec.hits):
                current = working.enemy_at(target.index)
                if current is None:
                    break
                working = _strike(
                    working,
                    target=current,
                    power=power * spec.damage_scale * falloff,
                    stat_value=stat_value,
                    stats=stats,
                    modifiers=modifiers,
                    spec=spec,
                    skill_name=skill.name,
                    source=source,
                    pace=pace,
                )
            falloff *= 1.0 - spec.chain_falloff

    if spec.category is EffectCategory.HEAL or spec.special == "full_heal":
        amount = (
            round(working.player.max_health * power / 100.0)
            if spec.heal_percent
            else round(power * (1.0 + stat_value / STAT_DAMAGE_DIVISOR))
        )
        amount = round(amount * mods.percent(modifiers, "healing_done_percent"))
        player, restored = working.player.healed(amount)
        working = replace(working, player=player).with_events(
            CombatEvent(
                kind=EventKind.HEAL,
                actor=player.name,
                amount=restored,
                skill_name=skill.name,
            )
        )

    if spec.category is EffectCategory.SHIELD:
        shield = round(power * (1.0 + stat_value / STAT_DAMAGE_DIVISOR))
        player = replace(working.player, shield=working.player.shield + shield)
        working = replace(working, player=player).with_events(
            CombatEvent(
                kind=EventKind.SHIELD, actor=player.name, amount=shield, skill_name=skill.name
            )
        )

    if spec.cleanse_count:
        before = len(working.player.effects.penalties())
        cleansed = working.player.effects.cleanse(spec.cleanse_count)
        removed = before - len(cleansed.penalties())
        working = replace(working, player=replace(working.player, effects=cleansed))
        if removed:
            working = working.with_events(
                CombatEvent(kind=EventKind.CLEANSED, amount=removed, skill_name=skill.name)
            )

    working = _apply_modifier_bundles(working, skill, spec, power, target_index)
    return _apply_special(working, skill, spec, power)


def _single_target(state: CombatState, target_index: int) -> tuple[EnemyState, ...]:
    target = state.enemy_at(target_index) or state.first_living()
    return (target,) if target is not None else ()


def _apply_modifier_bundles(
    state: CombatState,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    target_index: int,
) -> CombatState:
    working = state
    if spec.self_modifiers and spec.duration:
        effect = ActiveEffect(
            id=skill.code,
            name=skill.name,
            modifiers={item.key: item.amount(power) for item in spec.self_modifiers},
            turns_left=spec.duration,
            source=skill.code,
            beneficial=True,
        )
        working = replace(
            working, player=replace(working.player, effects=working.player.effects.apply(effect))
        )
        working = working.with_events(
            CombatEvent(
                kind=EventKind.EFFECT_APPLIED,
                actor=working.player.name,
                effect_name=skill.name,
                turns=spec.duration,
            )
        )

    if spec.target_modifiers and spec.duration:
        targets = working.living_enemies if spec.aoe else _single_target(working, target_index)
        for target in targets:
            effect = ActiveEffect(
                id=skill.code,
                name=skill.name,
                modifiers={item.key: item.amount(power) for item in spec.target_modifiers},
                turns_left=spec.duration,
                source=skill.code,
                beneficial=False,
            )
            working = working.replace_enemy(replace(target, effects=target.effects.apply(effect)))
            working = working.with_events(
                CombatEvent(
                    kind=EventKind.EFFECT_APPLIED,
                    target=target.name,
                    effect_name=skill.name,
                    turns=spec.duration,
                )
            )
    return working


def _apply_special(state: CombatState, skill: Skill, spec: EffectSpec, power: float) -> CombatState:
    working = state
    match spec.special:
        case "evade_next":
            working = replace(
                working,
                player=replace(working.player, evade_charges=working.player.evade_charges + 1),
            )
        case "free_cast":
            working = replace(working, player=replace(working.player, free_cast=True))
        case "cooldown_reset":
            working = replace(working, player=replace(working.player, cooldowns={}))
        case "full_heal":
            player, restored = working.player.healed(working.player.max_health)
            working = replace(working, player=player).with_events(
                CombatEvent(
                    kind=EventKind.HEAL,
                    actor=player.name,
                    amount=restored,
                    skill_name=skill.name,
                )
            )
        case "steal_gold":
            working = replace(working, gold=working.gold + round(power))
    return working


# --- striking --------------------------------------------------------


def _strike(
    state: CombatState,
    *,
    target: EnemyState,
    power: float,
    stat_value: int,
    stats: DerivedStats,
    modifiers: dict[str, float],
    spec: EffectSpec | None,
    skill_name: str,
    source: random.Random,
    pace: TurnTempo = TurnTempo(),
) -> CombatState:
    working = state
    enemy = target.enemy

    accuracy_penalty = 15.0 if spec is not None and "inaccurate" in spec.tags else 0.0
    hit_chance = min(
        MAX_HIT_CHANCE,
        max(MIN_HIT_CHANCE, stats.accuracy - enemy.level * 0.5 - accuracy_penalty),
    )
    if source.uniform(0, 100) > hit_chance:
        return working.with_events(
            CombatEvent(kind=EventKind.MISS, target=target.name, skill_name=skill_name)
        )

    raw = power * (1.0 + stat_value / STAT_DAMAGE_DIVISOR)
    raw *= mods.percent(modifiers, "damage_percent")
    raw *= pace.boost
    if spec is not None and spec.execute_scaling:
        missing = 1.0 - target.health / max(1, enemy.max_health)
        raw *= 1.0 + missing * spec.execute_scaling
    if target.effects.modifiers().get("damage_taken_percent"):
        raw *= 1.0 + target.effects.modifiers()["damage_taken_percent"] / 100.0

    # A breach is exactly that: the armour is not softened, it is simply not
    # there for this action.
    pierce = 1.0 if pace.breach else (spec.pierce if spec is not None else 0.0)
    effective_armor = enemy.armor * (1.0 - pierce)
    raw *= ARMOR_SOFTENER / (ARMOR_SOFTENER + effective_armor)

    guaranteed = spec is not None and spec.guaranteed_crit
    crit_chance = stats.crit_chance + (spec.crit_bonus if spec is not None else 0.0)
    is_crit = guaranteed or source.uniform(0, 100) < crit_chance
    if is_crit:
        raw *= stats.crit_damage / 100.0

    amount = max(1, round(raw))
    updated = target.damaged(amount)
    working = working.replace_enemy(updated)
    working = working.with_events(
        CombatEvent(
            kind=EventKind.CRIT if is_crit else EventKind.DAMAGE,
            actor=state.player.name,
            target=target.name,
            amount=amount,
            skill_name=skill_name,
        )
    )

    if spec is not None and spec.stun_turns and updated.alive:
        working = working.replace_enemy(replace(updated, stunned=spec.stun_turns))
        working = working.with_events(
            CombatEvent(kind=EventKind.STUNNED, target=target.name, turns=spec.stun_turns)
        )

    lifesteal = spec.lifesteal if spec is not None else 0.0
    lifesteal += state.player.effects.modifiers().get("lifesteal_percent", 0.0) / 100.0
    if lifesteal:
        player, restored = working.player.healed(round(amount * lifesteal))
        working = replace(working, player=player)
        if restored:
            working = working.with_events(
                CombatEvent(kind=EventKind.HEAL, actor=player.name, amount=restored)
            )

    if not updated.alive:
        working = working.with_events(
            CombatEvent(kind=EventKind.ENEMY_DEFEATED, target=target.name)
        )
    return working


# --- items and fleeing -----------------------------------------------


def _use_item(content: GameContent, state: CombatState, action: CombatAction) -> CombatState:
    if action.item_id is None:
        return state
    item = content.item(action.item_id)
    if item.effect is None:
        return state
    working = state
    match item.effect.kind:
        case "heal_flat":
            player, restored = working.player.healed(round(item.effect.power))
            working = replace(working, player=player).with_events(
                CombatEvent(kind=EventKind.HEAL, actor=player.name, amount=restored)
            )
        case "heal_percent":
            amount = round(working.player.max_health * item.effect.power / 100.0)
            player, restored = working.player.healed(amount)
            working = replace(working, player=player).with_events(
                CombatEvent(kind=EventKind.HEAL, actor=player.name, amount=restored)
            )
        case "restore_resource_percent":
            amount = round(working.player.max_resource * item.effect.power / 100.0)
            player = replace(
                working.player,
                resource=min(working.player.max_resource, working.player.resource + amount),
            )
            working = replace(working, player=player).with_events(
                CombatEvent(kind=EventKind.RESOURCE, actor=player.name, amount=amount)
            )
        case "cleanse":
            cleansed = working.player.effects.cleanse(round(item.effect.power))
            working = replace(working, player=replace(working.player, effects=cleansed))
            working = working.with_events(CombatEvent(kind=EventKind.CLEANSED, amount=1))
        case "buff_damage_percent":
            effect = ActiveEffect(
                id=f"item:{item.id}",
                name=item.name,
                modifiers={"damage_percent": item.effect.power},
                turns_left=max(1, item.effect.turns),
            )
            working = replace(
                working,
                player=replace(working.player, effects=working.player.effects.apply(effect)),
            )
            working = working.with_events(
                CombatEvent(
                    kind=EventKind.EFFECT_APPLIED,
                    effect_name=item.name,
                    turns=max(1, item.effect.turns),
                )
            )
    return working


def _try_flee(
    content: GameContent, character: Character, state: CombatState, source: random.Random
) -> CombatState:
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    chance = BASE_FLEE_CHANCE + modifiers.get("flee_chance_percent", 0.0)
    if source.uniform(0, 100) < chance:
        return replace(state, outcome=CombatOutcome.FLED).with_events(
            CombatEvent(kind=EventKind.FLED, actor=state.player.name)
        )
    return state.with_events(CombatEvent(kind=EventKind.FLEE_FAILED, actor=state.player.name))


# --- enemies ---------------------------------------------------------


def _enemy_actions(
    content: GameContent,
    character: Character,
    state: CombatState,
    source: random.Random,
) -> CombatState:
    stats = derived_stats(content, character, state.player.effects)
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    working = state

    for enemy_state in state.living_enemies:
        current = working.enemy_at(enemy_state.index)
        if current is None or not working.player.alive:
            continue
        if current.stunned > 0:
            working = working.replace_enemy(replace(current, stunned=current.stunned - 1))
            working = working.with_events(
                CombatEvent(kind=EventKind.TURN_SKIPPED, actor=current.name)
            )
            continue

        if working.player.evade_charges > 0:
            working = replace(
                working,
                player=replace(working.player, evade_charges=working.player.evade_charges - 1),
            )
            working = working.with_events(
                CombatEvent(kind=EventKind.DODGE, target=working.player.name, actor=current.name)
            )
            continue

        # The announcement is not decoration: an aim cannot be dodged, a press
        # hits harder, a guard trades damage for holding the line.
        announced = Tag(state.intent) if state.intent else None
        hit_chance = min(
            MAX_HIT_CHANCE,
            max(
                MIN_HIT_CHANCE,
                ENEMY_ACCURACY_BASE + current.enemy.level * ENEMY_ACCURACY_PER_LEVEL - stats.dodge,
            ),
        )
        if announced is Tag.AIM:
            hit_chance = MAX_HIT_CHANCE
        if source.uniform(0, 100) > hit_chance:
            working = working.with_events(
                CombatEvent(kind=EventKind.DODGE, actor=current.name, target=working.player.name)
            )
            continue

        raw = float(current.enemy.damage)
        if announced is Tag.PRESS:
            raw *= tempo_rules.INTENT_PRESS_DAMAGE
        elif announced is Tag.GUARD:
            raw *= 1.0 - tempo_rules.INTENT_GUARD_REDUCTION
        enemy_modifiers = current.effects.modifiers()
        raw *= 1.0 + enemy_modifiers.get("damage_percent", 0.0) / 100.0
        raw *= ARMOR_SOFTENER / (ARMOR_SOFTENER + stats.armor)
        raw *= mods.percent(modifiers, "damage_taken_percent")
        raw *= 1.0 + working.player.effects.modifiers().get("damage_taken_percent", 0.0) / 100.0

        amount = max(1, round(raw))
        player, lost = working.player.damaged(amount)
        working = replace(working, player=player).with_events(
            CombatEvent(
                kind=EventKind.DAMAGE,
                actor=current.name,
                target=player.name,
                amount=amount,
            )
        )
        if lost and not player.alive:
            working = working.with_events(
                CombatEvent(kind=EventKind.PLAYER_DEFEATED, target=player.name)
            )
    return working


# --- upkeep ----------------------------------------------------------


def _end_of_turn(content: GameContent, character: Character, state: CombatState) -> CombatState:
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    stats = derived_stats(content, character, state.player.effects)

    player = state.player.tick_cooldowns()
    player = replace(player, effects=player.effects.tick())

    regen_percent = modifiers.get("regen_per_turn_percent", 0.0)
    if regen_percent:
        player, _ = player.healed(round(player.max_health * regen_percent / 100.0))
    player = replace(
        player,
        resource=min(player.max_resource, player.resource + round(stats.resource_regen)),
    )

    enemies = tuple(replace(enemy, effects=enemy.effects.tick()) for enemy in state.enemies)
    return replace(state, player=player, enemies=enemies, turn=state.turn + 1)


def _check_outcome(content: GameContent, character: Character, state: CombatState) -> CombatState:
    if state.is_over:
        return state
    if not state.player.alive:
        return replace(state, outcome=CombatOutcome.DEFEAT)
    if not state.living_enemies:
        experience = sum(
            experience_reward(enemy_level=enemy.enemy.level, character_level=character.level)
            for enemy in state.enemies
        )
        gold_modifier = mods.collect_modifiers(content, character, state.player.effects).get(
            "gold_percent", 0.0
        )
        gold = round(
            sum(enemy.enemy.gold for enemy in state.enemies) * (1.0 + gold_modifier / 100.0)
        )
        loot = tuple(item for enemy in state.enemies for item in enemy.enemy.loot)
        return replace(
            state,
            outcome=CombatOutcome.VICTORY,
            experience=experience,
            gold=state.gold + gold,
            loot=loot,
        )
    return state


def is_low_health(state: CombatState) -> bool:
    """Used by screens to lead with a warning line."""
    return state.player.health / max(1, state.player.max_health) <= LOW_HEALTH_THRESHOLD
