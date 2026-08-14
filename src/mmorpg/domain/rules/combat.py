"""The turn-based combat engine.

One call to :func:`resolve_turn` runs the player's action, then every living
enemy's action, then the end-of-turn upkeep, and returns a brand new state with
the list of events that happened. There are no timers anywhere: the state simply
waits (accessibility rule 13).

Three rules make a fight more than a damage race, and none of them adds a button:

- **intent** - every enemy says in advance which of the three tags its next move
  carries, so the player chooses against something, not into the dark;
- **trace** - the player's own moves carry tags too. A repeat builds *momentum*
  and hits harder with every repeat; three different tags in a row are a
  *breakthrough* and the enemies do not get to answer that turn;
- **breach** - a tag that counters the announced intent takes that enemy's armour
  out of the count *and* halves the blow it answers with.

Every magnitude - a blow, a skill, a heal - is stated as a percentage of
something that grows on its own: damage of the character's standard blow, healing
of maximum health. Content therefore never has to be rewritten as levels climb,
and a level-1 skill and a level-100 skill are read on the same scale (ADR 0007).

All randomness comes from an explicit seed passed in by the caller, so a fight can
be replayed exactly - which is what makes it testable. Intents carry no randomness
at all: they are a pure function of the enemy and the turn, so the screen and the
engine always name the same one.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionKind,
    ActionTag,
    CombatAction,
    CombatEvent,
    CombatOutcome,
    CombatState,
    EnemyState,
    EventKind,
    PlayerState,
    Trace,
    counter_to,
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack
from mmorpg.domain.entities.location import Enemy
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen.enemies import RANK_FACTORS
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.progression import experience_reward
from mmorpg.domain.rules.skill_effects import (
    EffectCategory,
    EffectSpec,
    spec_for,
    tag_of_skill,
)
from mmorpg.domain.rules.stats import DerivedStats, derived_stats, primary_stats

# --- one scale for every number --------------------------------------
#
# Everything a skill does is measured in *standard blows*. A character's blow is
# a function of the one stat their class scales on, so damage has exactly one
# source of growth; content states a skill's power as a percentage of it, where
# 100 is a plain attack. Before this, a skill's power was a flat content number
# while the plain attack grew with level, and every skill past level 30 was
# strictly worse than pressing "Атака" - see ADR 0007.
#
# Level carries most of the curve and the scaling stat carries the spread. If the
# stat carried it alone, a class with one key stat would pour every point into it
# and hit twice as hard as a class with two - the difference would not be a build,
# it would be a bug.
BLOW_BASE = 6.0
BLOW_PER_LEVEL = 2.2
BLOW_PER_STAT = 0.6
BASIC_ATTACK_PERCENT = 100.0

MIN_HIT_CHANCE = 40.0
MAX_HIT_CHANCE = 97.0
ENEMY_ACCURACY_BASE = 78.0
BASE_FLEE_CHANCE = 45.0
LOW_HEALTH_THRESHOLD = 0.35

# Accuracy is answered by the *difference* in levels, never by the absolute one.
# Measured absolutely, a hero of level 300 missed three blows in five while the
# enemies never missed at all, and every high-level fight became a coin flip.
# Measured by the gap, a fight at your own level is even and being out of your
# depth is what costs you.
ACCURACY_PER_LEVEL_GAP = 1.5
ENEMY_ACCURACY_PER_LEVEL_GAP = 1.0

# Armour is softened against the defender's own level. Both armour and damage
# grow linearly with level, so an unnormalised softener would let armour win the
# race outright: at level 300 an ordinary blow would land for a quarter of itself.
ARMOR_SOFTENER_BASE = 55.0
ARMOR_SOFTENER_PER_LEVEL = 3.2


def standard_blow(level: int, stat_value: int) -> float:
    """What one plain attack of this character is worth."""
    return BLOW_BASE + BLOW_PER_LEVEL * level + BLOW_PER_STAT * stat_value


def armor_factor(armor: float, level: int) -> float:
    """The share of a blow that survives armour, between 0 and 1."""
    softener = ARMOR_SOFTENER_BASE + ARMOR_SOFTENER_PER_LEVEL * level
    return softener / (softener + max(0.0, armor))


# --- tempo: intent, trace, breach ------------------------------------

#: Enemies walk this circle, offset by their own initiative, so two enemies in
#: the same fight rarely announce the same thing.
INTENT_CYCLE = (ActionTag.PRESS, ActionTag.PRECISION, ActionTag.GUARD)
#: A quarter of health left and the beast stops trading blows.
WOUNDED_RATIO = 0.25
#: What the announced intent does to the enemy's armour when the player strikes.
INTENT_ARMOR: dict[ActionTag, float] = {
    ActionTag.PRESS: 0.75,
    ActionTag.PRECISION: 1.0,
    ActionTag.GUARD: 2.0,
}
#: And to the damage of the enemy's own blow.
INTENT_DAMAGE: dict[ActionTag, float] = {
    ActionTag.PRESS: 1.4,
    ActionTag.PRECISION: 1.0,
    ActionTag.GUARD: 0.5,
}
#: Two identical tags in a row are momentum; three different ones break the guard.
MOMENTUM_STREAK = 2
#: Added per repeat beyond the first, so a third identical tag is worth +50%.
MOMENTUM_DAMAGE_PERCENT = 25.0
#: A breached enemy has been caught mid-move: its own blow lands for half.
BREACH_ANSWER_SCALE = 0.5


def enemy_intent(enemy: EnemyState, turn: int) -> ActionTag:
    """What this enemy announces for the given turn.

    Pure and deterministic: the screen calls it to print the announcement, the
    engine calls it to keep the promise. A wounded enemy always closes up, which
    is both readable and the reason a fight does not end the way it started.
    """
    if enemy.health * 4 <= enemy.enemy.max_health:
        return ActionTag.GUARD
    step = int(enemy.enemy.initiative) + enemy.index + turn
    return INTENT_CYCLE[step % len(INTENT_CYCLE)]


@dataclass(frozen=True, slots=True)
class TurnTempo:
    """Everything the tag rules decide about one turn, worked out before it runs.

    It has to be settled up front: momentum changes the damage of the very action
    that earns it, and a breakthrough decides whether the enemies answer at all.
    """

    intents: Mapping[int, ActionTag]
    tag: ActionTag | None = None
    streak: int = 0
    breakthrough: bool = False

    @property
    def momentum(self) -> bool:
        return self.streak >= MOMENTUM_STREAK

    def breached(self, index: int) -> bool:
        """Whether the player's tag counters what this enemy announced."""
        intent = self.intents.get(index)
        return intent is not None and self.tag is counter_to(intent)

    def armor_scale(self, index: int) -> float:
        if self.breached(index):
            return 0.0
        intent = self.intents.get(index)
        return INTENT_ARMOR[intent] if intent is not None else 1.0

    def answer_scale(self, index: int) -> float:
        """What is left of a breached enemy's own blow.

        Without this a breach would be worth nothing against an announced press:
        the tag that counters a press is a guard, and a guard deals no damage, so
        the "armour out of the count" reward had nothing to apply to. A breach now
        always pays - in damage dealt, in damage taken, or in both.
        """
        return BREACH_ANSWER_SCALE if self.breached(index) else 1.0

    @property
    def damage_scale(self) -> float:
        return 1.0 + MOMENTUM_DAMAGE_PERCENT * max(0, self.streak - 1) / 100.0


# --- starting a fight ------------------------------------------------


def start_combat(
    content: GameContent, character: Character, enemies: tuple[Enemy, ...]
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
    return CombatState(
        player=player,
        enemies=tuple(EnemyState.spawn(enemy, index) for index, enemy in enumerate(enemies)),
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
    tempo = _tempo(content, character, working, action)

    if working.player.stunned > 0:
        working = working.with_events(
            CombatEvent(kind=EventKind.TURN_SKIPPED, actor=working.player.name)
        )
        working = replace(
            working, player=replace(working.player, stunned=working.player.stunned - 1)
        )
    else:
        working = _announce_tempo(working, tempo)
        working = _player_action(content, character, working, action, tempo, source)
        working = replace(working, trace=_advanced_trace(state.trace, tempo))

    working = _check_outcome(content, character, working)
    if working.is_over:
        return working

    if tempo.breakthrough:
        # The exchange is broken: the enemies spend the turn finding their feet.
        working = working.with_events(
            CombatEvent(kind=EventKind.BREAKTHROUGH, actor=working.player.name)
        )
    else:
        working = _enemy_actions(content, character, working, tempo, source)
        working = _check_outcome(content, character, working)
        if working.is_over:
            return working

    return _end_of_turn(content, character, working)


def _tempo(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> TurnTempo:
    """Work out the intents, the player's tag and what the trace makes of it."""
    intents = MappingProxyType(
        {enemy.index: enemy_intent(enemy, state.turn) for enemy in state.living_enemies}
    )
    tag = _action_tag(content, character, state, action)
    if tag is None or state.player.stunned > 0:
        return TurnTempo(intents=intents)

    trace = state.trace
    return TurnTempo(
        intents=intents,
        tag=tag,
        streak=trace.streak + 1 if trace.last is tag else 1,
        breakthrough=trace.breaks_with(tag),
    )


def _action_tag(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> ActionTag | None:
    """The tag this action will leave, or ``None`` when it leaves none.

    An action that cannot happen - an empty slot, a skill on cooldown or one the
    player cannot pay for - leaves no trace, and neither does running away.
    """
    match action.kind:
        case ActionKind.ATTACK:
            return ActionTag.PRESS
        case ActionKind.ITEM:
            return ActionTag.GUARD if action.item_id is not None else None
        case ActionKind.FLEE:
            return None
        case ActionKind.SKILL | ActionKind.RACIAL:
            attempt = _attempt_skill(content, character, state, action)
            return None if isinstance(attempt, CombatEvent) else tag_of_skill(attempt[0])


def _announce_tempo(state: CombatState, tempo: TurnTempo) -> CombatState:
    working = state
    if tempo.momentum:
        working = working.with_events(
            CombatEvent(
                kind=EventKind.MOMENTUM,
                actor=working.player.name,
                amount=tempo.streak,
            )
        )
    for enemy in working.living_enemies:
        if tempo.breached(enemy.index):
            working = working.with_events(CombatEvent(kind=EventKind.BREACH, target=enemy.name))
    return working


def _advanced_trace(trace: Trace, tempo: TurnTempo) -> Trace:
    """A breakthrough spends the trace; anything else lengthens it."""
    if tempo.tag is None:
        return trace
    return Trace() if tempo.breakthrough else trace.push(tempo.tag)


def _player_action(
    content: GameContent,
    character: Character,
    state: CombatState,
    action: CombatAction,
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    match action.kind:
        case ActionKind.ATTACK:
            return _basic_attack(content, character, state, action.target, tempo, source)
        case ActionKind.SKILL | ActionKind.RACIAL:
            return _use_skill(content, character, state, action, tempo, source)
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
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    target = state.enemy_at(target_index) or state.first_living()
    if target is None:
        return state
    stats = derived_stats(content, character, state.player.effects)
    modifiers = mods.collect_modifiers(content, character, state.player.effects)

    return _strike(
        state,
        target=target,
        power=blow_of(content, character, state.player.effects) * BASIC_ATTACK_PERCENT / 100.0,
        character_level=character.level,
        stats=stats,
        modifiers=modifiers,
        spec=None,
        skill_name="Атака",
        tempo=tempo,
        source=source,
    )


def blow_of(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
    scaling: StatCode | None = None,
) -> float:
    """This character's standard blow - the unit every skill power is a percent of.

    The stat is the skill's own when it names one and the class's first key stat
    otherwise, so a warrior's blow follows strength and a mage's follows intellect
    without either being written down twice.
    """
    if scaling is None:
        klass = content.character_class(character.class_id)
        scaling = klass.key_stats[0] if klass.key_stats else None
    primary = primary_stats(content, character, effects)
    return standard_blow(character.level, primary[scaling] if scaling is not None else 0)


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


def _attempt_skill(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> tuple[Skill, int] | CombatEvent:
    """The skill and what it costs, or the event that says why it cannot be used.

    Asked twice per turn - once to work out the tag before anything happens, once
    to actually run the skill - so it stays pure and cheap.
    """
    skill = _resolve_skill(content, character, action)
    if skill is None:
        return CombatEvent(kind=EventKind.EMPTY_SLOT)

    player = state.player
    cooldown = player.cooldown_of(skill.code)
    if cooldown > 0:
        return CombatEvent(kind=EventKind.ON_COOLDOWN, skill_name=skill.name, turns=cooldown)

    modifiers = mods.collect_modifiers(content, character, player.effects)
    # The rank-3 edge is a discount or a gain, never both: see ``rules.skills``.
    cost = round(
        _skill_cost(skill, modifiers, free=player.free_cast)
        * skill_rules.cost_factor(character, skill)
    )
    if cost > player.resource:
        return CombatEvent(kind=EventKind.NOT_ENOUGH_RESOURCE, skill_name=skill.name, amount=cost)
    return skill, cost


def _use_skill(
    content: GameContent,
    character: Character,
    state: CombatState,
    action: CombatAction,
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    attempt = _attempt_skill(content, character, state, action)
    if isinstance(attempt, CombatEvent):
        return state.with_events(attempt)

    skill, cost = attempt
    rank = character.loadout.rank_of(skill.code)
    power = skill.power_at_rank(rank) * skill_rules.power_factor(character, skill)
    spec = spec_for(skill.effect)

    player = replace(state.player, resource=state.player.resource - cost, free_cast=False)
    if skill.cooldown:
        # +1 because cooldowns tick down at the end of this same turn, so the skill
        # stays unavailable for exactly `cooldown` further turns.
        player = player.with_cooldown(skill.code, skill.cooldown + 1)
    working = replace(state, player=player)
    return _apply_spec(
        content, character, working, skill, spec, power, action.target, tempo, source
    )


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
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    stats = derived_stats(content, character, state.player.effects)
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    blow = blow_of(content, character, state.player.effects, skill.scaling)
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
                    power=blow * power / 100.0 * spec.damage_scale * falloff,
                    character_level=character.level,
                    stats=stats,
                    modifiers=modifiers,
                    spec=spec,
                    skill_name=skill.name,
                    tempo=tempo,
                    source=source,
                )
            falloff *= 1.0 - spec.chain_falloff

    if spec.category is EffectCategory.HEAL or spec.special == "full_heal":
        # Healing and shields are percentages of maximum health, not of a blow:
        # health grows five times faster than a blow does, so a heal priced in
        # blows would be worth nothing by level 40.
        amount = round(working.player.max_health * power / 100.0)
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
        shield = round(working.player.max_health * power / 100.0)
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
    character_level: int,
    stats: DerivedStats,
    modifiers: dict[str, float],
    spec: EffectSpec | None,
    skill_name: str,
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    working = state
    enemy = target.enemy

    accuracy_penalty = 15.0 if spec is not None and "inaccurate" in spec.tags else 0.0
    gap = enemy.level - character_level
    hit_chance = min(
        MAX_HIT_CHANCE,
        max(
            MIN_HIT_CHANCE,
            stats.accuracy - gap * ACCURACY_PER_LEVEL_GAP - accuracy_penalty,
        ),
    )
    if source.uniform(0, 100) > hit_chance:
        return working.with_events(
            CombatEvent(kind=EventKind.MISS, target=target.name, skill_name=skill_name)
        )

    raw = power
    raw *= mods.percent(modifiers, "damage_percent")
    raw *= tempo.damage_scale
    if spec is not None and spec.execute_scaling:
        missing = 1.0 - target.health / max(1, enemy.max_health)
        raw *= 1.0 + missing * spec.execute_scaling
    if target.effects.modifiers().get("damage_taken_percent"):
        raw *= 1.0 + target.effects.modifiers()["damage_taken_percent"] / 100.0

    pierce = spec.pierce if spec is not None else 0.0
    # A breach takes the armour out of the count entirely; an announced guard
    # doubles it, and an enemy winding up for a press has already opened.
    effective_armor = enemy.armor * (1.0 - pierce) * tempo.armor_scale(target.index)
    raw *= armor_factor(effective_armor, enemy.level)

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
        # No flat magnitudes here either: a potion worth 40 health is a potion
        # worth nothing by level 20 (ADR 0007).
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
    tempo: TurnTempo,
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

        # The intent was announced before the player moved, so it is honoured here
        # even if the enemy has been wounded since.
        intent = tempo.intents.get(current.index, ActionTag.PRESS)

        hit_chance = min(
            MAX_HIT_CHANCE,
            max(
                MIN_HIT_CHANCE,
                ENEMY_ACCURACY_BASE
                + (current.enemy.level - character.level) * ENEMY_ACCURACY_PER_LEVEL_GAP
                - stats.dodge,
            ),
        )
        # A blow announced as precision is not dodged - it is answered or taken.
        if intent is not ActionTag.PRECISION and source.uniform(0, 100) > hit_chance:
            working = working.with_events(
                CombatEvent(kind=EventKind.DODGE, actor=current.name, target=working.player.name)
            )
            continue

        raw = float(current.enemy.damage) * INTENT_DAMAGE[intent]
        # Caught mid-move: countering the announced tag also blunts the answer.
        raw *= tempo.answer_scale(current.index)
        enemy_modifiers = current.effects.modifiers()
        raw *= 1.0 + enemy_modifiers.get("damage_percent", 0.0) / 100.0
        raw *= armor_factor(stats.armor, character.level)
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
        experience = round(
            sum(
                experience_reward(enemy_level=enemy.enemy.level, character_level=character.level)
                * RANK_FACTORS[enemy.enemy.rank].experience
                for enemy in state.enemies
            )
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
