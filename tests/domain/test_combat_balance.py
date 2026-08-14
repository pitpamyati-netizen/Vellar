"""How long a fight lasts, and what a good decision is worth.

These are the numbers the design promises out loud: an ordinary fight is about
three turns, an epic one roughly twice that, a boss twice again, and a player who
reads the announced intent finishes sooner than one who only presses "Атака".
Nothing here checks a formula - it checks the experience the formulas add up to,
which is the only thing a player can feel.

The fights are simulated with a deliberately simple "competent player": take the
biggest blow available, prefer the tag that counters what the enemy announced.
A real player can do better; if even this one cannot finish in time, the balance
is wrong.
"""

from __future__ import annotations

import statistics

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    ActionTag,
    CombatAction,
    CombatOutcome,
    CombatState,
    counter_to,
)
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.procgen.enemies import generate_group
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules.combat import blow_of, enemy_intent, resolve_turn, start_combat
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for, tag_of_skill
from mmorpg.domain.rules.stats import stat_allowance

#: A fight sampled across the whole level band, not just at the levels a
#: developer happens to play.
LEVELS = (1, 10, 40, 150, 300)
CLASSES = ("warrior", "rogue", "mage", "cleric")
TRIALS = 20

#: What the design promises. The median is the contract; the ceiling only stops a
#: single unlucky roll from being called a regression, and the floor stops the
#: opposite regression - a fight that is over before it is a fight.
ORDINARY_TURNS = 4
ORDINARY_FLOOR = 2
ORDINARY_CEILING = 8
ELITE_FLOOR = 1.5
BOSS_FLOOR = 2.5


def build(content: GameContent, class_id: str, level: int) -> Character:
    """A character built the way a player would: points into the key stats, the
    newest skills equipped, damage first."""
    klass = content.character_class(class_id)
    keys = list(klass.key_stats) or [StatCode.STR]
    allocated: dict[str, int] = {}
    for index in range(stat_allowance(content, level)):
        stat = keys[index % len(keys)]
        allocated[stat.value] = allocated.get(stat.value, 0) + 1

    unlocked = sorted(
        (
            skill
            for skill in content.skills
            if skill.owner == f"class:{class_id}" and skill.is_active and skill.level <= level
        ),
        key=lambda skill: (spec_for(skill.effect).category is EffectCategory.DAMAGE, skill.level),
    )
    actives = [skill.code for skill in unlocked[-6:]]
    actives += [None] * (6 - len(actives))

    return Character(
        id=1,
        user_id=1,
        name="Проба",
        race_id="human",
        class_id=class_id,
        level=level,
        allocated=StatBlock.from_mapping(allocated),
        loadout=SkillLoadout(actives=tuple(actives), racial=content.race("human").active_code),
    )


def _options(content: GameContent, character: Character, state: CombatState) -> list[CombatAction]:
    actions = [CombatAction(kind=ActionKind.ATTACK)]
    for slot, code in enumerate(character.loadout.actives):
        if code is None:
            continue
        skill = content.skill(code)
        if state.player.cooldown_of(code) == 0 and skill.cost <= state.player.resource:
            actions.append(CombatAction(kind=ActionKind.SKILL, slot=slot))
    return actions


def _value(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> float:
    """Roughly what this action is worth this turn, in damage and in tempo."""
    enemies = state.living_enemies
    if not enemies:
        return 0.0
    blow = blow_of(content, character, state.player.effects)

    if action.kind is ActionKind.ATTACK:
        tag, worth = ActionTag.PRESS, blow
    else:
        skill = content.skill(character.loadout.actives[action.slot])
        spec = spec_for(skill.effect)
        tag = tag_of_skill(skill)
        if spec.category is EffectCategory.DAMAGE:
            worth = blow * skill.power_at_rank(1) / 100.0 * spec.hits * spec.damage_scale
            if spec.aoe:
                worth *= len(enemies)
        else:
            # Support is worth what it saves: nothing at full health, a blow and
            # a half when the fight is nearly lost.
            missing = 1.0 - state.player.health / state.player.max_health
            worth = blow * 1.5 * missing

    if tag is counter_to(enemy_intent(enemies[0], state.turn)):
        worth *= 1.5
    if state.trace.last is tag:
        worth *= 1.3
    if state.trace.breaks_with(tag):
        worth *= 1.4
    return worth


def fight(
    content: GameContent,
    character: Character,
    *,
    rank: EnemyRank,
    trial: int,
    clever: bool = True,
) -> tuple[int, CombatOutcome]:
    """Run one whole fight and report how many turns it took."""
    seed = derive(b"balance", character.class_id, character.level, trial, rank.value)
    enemies = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="*",
        level=character.level,
        rank=rank,
        elite_titles=content.elite_titles,
    )
    state = start_combat(content, character, enemies)
    turn = 0
    while not state.is_over and turn < 60:
        turn += 1
        action = (
            max(
                _options(content, character, state),
                key=lambda a: _value(content, character, state, a),
            )
            if clever
            else CombatAction(kind=ActionKind.ATTACK)
        )
        state = resolve_turn(content, character, state, action, derive(seed, "turn", turn))
    return turn, state.outcome


def sample(
    content: GameContent, class_id: str, level: int, *, rank: EnemyRank, clever: bool = True
) -> list[int]:
    character = build(content, class_id, level)
    return [
        fight(content, character, rank=rank, trial=trial, clever=clever)[0]
        for trial in range(TRIALS)
    ]


# --- the promise ------------------------------------------------------


@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_is_about_three_turns(
    content: GameContent, class_id: str, level: int
) -> None:
    turns = sample(content, class_id, level, rank=EnemyRank.NORMAL)
    median = statistics.median(turns)
    assert ORDINARY_FLOOR <= median <= ORDINARY_TURNS, f"{class_id} at {level}: median {median}"
    assert max(turns) <= ORDINARY_CEILING, f"{class_id} at {level}: worst {max(turns)} turns"


@pytest.mark.parametrize("class_id", CLASSES)
@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_at_your_own_level_is_won(
    content: GameContent, class_id: str, level: int
) -> None:
    """A fight the world hands you is not a coin flip. Losing is for the tiers
    that announce themselves as long."""
    character = build(content, class_id, level)
    outcomes = [
        fight(content, character, rank=EnemyRank.NORMAL, trial=trial)[1] for trial in range(TRIALS)
    ]
    assert all(outcome is CombatOutcome.VICTORY for outcome in outcomes)


def test_the_long_tiers_are_the_only_long_fights(content: GameContent) -> None:
    """Pooled across classes on purpose: the tiers are a promise about the game,
    not about any one class, and a per-class ratio over medians of two and three
    turns is arithmetic noise."""

    def pooled(rank: EnemyRank) -> float:
        turns = [turn for class_id in CLASSES for turn in sample(content, class_id, 40, rank=rank)]
        return statistics.median(turns)

    ordinary = pooled(EnemyRank.NORMAL)
    elite = pooled(EnemyRank.ELITE)
    boss = pooled(EnemyRank.BOSS)
    assert elite >= ordinary * ELITE_FLOOR, f"epic {elite} against ordinary {ordinary}"
    assert boss >= ordinary * BOSS_FLOOR, f"boss {boss} against ordinary {ordinary}"


@pytest.mark.parametrize("class_id", CLASSES)
def test_reading_the_intent_shortens_the_fight(content: GameContent, class_id: str) -> None:
    """The whole point of intent, trace and breach: choosing well has to pay.

    Ordinary fights, where both players win, so the comparison is turns and not
    survival - a player who dies on turn four also "finished" in four turns.
    """
    clever = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL))
    plain = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL, clever=False))
    assert clever < plain, f"{class_id}: {clever} turns played well vs {plain} turns of attacking"


@pytest.mark.parametrize("level", (1, 40, 300))
def test_a_skill_always_beats_a_plain_attack(content: GameContent, level: int) -> None:
    """The regression that started all this: skill power used to be a flat number
    from content while the plain attack grew with level, so by level 30 every
    skill in the game was worse than pressing "Атака"."""
    character = build(content, "warrior", level)
    blow = blow_of(content, character)
    for code in character.loadout.equipped_actives():
        skill = content.skill(code)
        if spec_for(skill.effect).category is not EffectCategory.DAMAGE:
            continue
        power = blow * skill.power_at_rank(1) / 100.0
        assert power > blow, f"{skill.name} at level {level} is weaker than an attack"
