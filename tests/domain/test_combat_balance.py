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
from collections.abc import Sequence
from typing import NamedTuple

import pytest

from mmorpg.domain.entities import (
    Character,
    CharacterClass,
    Equipment,
    GameContent,
    Item,
    SkillLoadout,
)
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
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules.combat import blow_of, enemy_intent, resolve_turn, start_combat
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for, tag_of_skill
from mmorpg.domain.rules.stats import derived_stats, stat_allowance

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
#: Потолок стал выше на два хода вместе с костями: урон теперь бросается, и один
#: неудачный бой действительно бывает вдвое длиннее обычного. Медиана — вот
#: договор; потолок только отделяет невезение от поломки, и хвост у распределения
#: с костями честно длиннее, чем был у одного числа (ADR 0015).
ORDINARY_CEILING = 10
ELITE_FLOOR = 1.5
BOSS_FLOOR = 2.5

#: An ordinary fight is won, but it is not free: this share of the health pool is
#: what makes a run through a location a series of decisions instead of a
#: formality. Pooled across classes - a warrior in plate spends less than a mage,
#: and that is the point of wearing plate.
ORDINARY_HEALTH_COST = 0.07
#: How many ordinary fights in a row a run through a location is worth measuring
#: over: about what stands between the entrance and the exit of one.
RUN_LENGTH_FLOOR = 4
#: Сколько таких пробегов меряется и какая их доля должна дойти до конца.
#: Один пробег - это один сид, а один сид это не обещание: полгода он был
#: единственным, и стоило костям лечь иначе, как «мага держит четыре боя»
#: превращалось в «мага держал именно этот бой». Доля считается по всем классам
#: разом - ровно потому же, почему по ним разом считается цена боя: латы и
#: должны доходить чаще рясы, и вопрос только в том, доходит ли вылазка.
RUN_TRIALS = 12
RUN_SURVIVAL = 0.7
#: How much faster reading the announced intent may make a fight. It has to pay
#: (``test_reading_the_intent_shortens_the_fight``), and it has to stay a way of
#: fighting well rather than the only fight that exists.
TEMPO_CEILING = 2.0
#: How far apart the fastest and the slowest class may be on a boss. A class is
#: allowed a character; it is not allowed to make the same boss a different game.
CLASS_SPREAD = 1.75


def armed(
    content: GameContent,
    klass: CharacterClass,
    level: int,
    actives: Sequence[str | None] = (),
) -> Equipment:
    """The best weapon of its class this character could be holding by now.

    Оружие здесь не украшение: весь урон в игре растёт из костей оружия, а
    половина умений разбойника и следопыта без своего оружия просто не сработает.
    Голыми руками мерить бой значило бы мерить не ту игру, в которую играют.

    Доспех берётся тем же способом. Раньше его снимали намеренно — чтобы «чего
    стоит бой» мерилось по голому здоровью, — но с тех пор броня стала числом, и
    голый герой это уже не «худший случай», а другая игра: латы срезают треть
    удара, и меряя без них, мы меряли бы того, кого в игре нет.
    """
    # Игрок носит своё: чужое не запрещено, но стоит точности и инициативы, и брать
    # его нарочно незачем.
    wieldable = [
        item
        for item in content.items
        if item.is_weapon and klass.can_wield(item.weapon_type) and item.level <= level
    ]
    if not wieldable:
        return Equipment()
    # Игрок берёт оружие под свою панель и под свой удар, а не самое дорогое:
    # клинок, которым работают три умения, стоит больше молота, которым не
    # работает ни одно, а кадило с прибавкой к лечению не бьёт вовсе.
    wanted = [
        skill.weapon_types
        for code in actives
        if code is not None
        for skill in (content.skill(code),)
        if skill.weapon_types
    ]

    def worth(item: Item) -> tuple[int, float, int]:
        average = item.damage.average if item.damage is not None else 0.0
        share = 1.0 + item.modifiers.get("damage_percent", 0.0) / 100.0
        return sum(item.weapon_type in types for types in wanted), average * share, item.level

    equipment = Equipment().equip("weapon", max(wieldable, key=worth).id)
    for slot in ("head", "body", "hands", "feet"):
        fitting = [
            item
            for item in content.items
            if item.slot == slot
            and item.is_armor
            and klass.can_wear(item.armor_type)
            and item.level <= level
            and item.rarity == "common"
        ]
        if fitting:
            equipment = equipment.equip(slot, max(fitting, key=lambda item: item.armor).id)
    return equipment


def build(content: GameContent, class_id: str, level: int) -> Character:
    """A character built the way a player would: points into the key stats, the
    newest skills equipped, damage first, and a weapon in hand."""
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
        equipment=armed(content, klass, level, actives),
        loadout=SkillLoadout(actives=tuple(actives), racial=content.race("human").active_code),
    )


def _options(content: GameContent, character: Character, state: CombatState) -> list[CombatAction]:
    actions = [CombatAction(kind=ActionKind.ATTACK)]
    for slot, code in enumerate(character.loadout.actives):
        if code is None:
            continue
        skill = content.skill(code)
        # Умение, для которого в руках не то оружие, игрок видит отказом прямо на
        # кнопке и не нажимает: считать его доступным значило бы мерить игрока,
        # который каждый ход жмёт наугад.
        if gear.skill_refusal(content, character, skill):
            continue
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


class FightResult(NamedTuple):
    """One simulated fight, in the three numbers the promises are about."""

    turns: int
    outcome: CombatOutcome
    health_left: int
    health_start: int

    @property
    def health_spent(self) -> float:
        """The share of the pool this fight cost, between 0 and 1."""
        return (self.health_start - max(0, self.health_left)) / self.health_start


def fight(
    content: GameContent,
    character: Character,
    *,
    rank: EnemyRank,
    trial: int,
    clever: bool = True,
) -> FightResult:
    """Run one whole fight and report what it took out of the character."""
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
    started_with = state.player.health
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
    return FightResult(turn, state.outcome, state.player.health, started_with)


def trials(
    content: GameContent, class_id: str, level: int, *, rank: EnemyRank, clever: bool = True
) -> list[FightResult]:
    character = build(content, class_id, level)
    return [
        fight(content, character, rank=rank, trial=trial, clever=clever) for trial in range(TRIALS)
    ]


def sample(
    content: GameContent, class_id: str, level: int, *, rank: EnemyRank, clever: bool = True
) -> list[int]:
    return [result.turns for result in trials(content, class_id, level, rank=rank, clever=clever)]


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
    results = trials(content, class_id, level, rank=EnemyRank.NORMAL)
    assert all(result.outcome is CombatOutcome.VICTORY for result in results)


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


@pytest.mark.parametrize("level", LEVELS)
def test_an_ordinary_fight_is_won_but_not_for_free(content: GameContent, level: int) -> None:
    """A fight that costs nothing is not a fight, it is a button.

    Ordinary opponents used to take about a twentieth of the health pool, so a
    location could be walked end to end without a thought: the wounds that
    outlive a fight, the potions and the beds were all decoration (Roadmap,
    "Риски"). Pooled across classes, because plate is *supposed* to spend less
    than a robe.
    """
    spent = [
        result.health_spent
        for class_id in CLASSES
        for result in trials(content, class_id, level, rank=EnemyRank.NORMAL)
    ]
    median = statistics.median(spent)
    assert median >= ORDINARY_HEALTH_COST, f"at {level}: an ordinary fight costs {median:.0%}"


def _walk(content: GameContent, class_id: str, offset: int) -> tuple[bool, float]:
    """One run through a location: fights in a row, nothing drunk, no bed paid.

    Returns whether the run was walked to the end and what share of the health
    pool was left at that point.
    """
    character = build(content, class_id, 40)
    stats = derived_stats(content, character)
    for step in range(RUN_LENGTH_FLOOR):
        result = fight(
            content, character, rank=EnemyRank.NORMAL, trial=offset * RUN_LENGTH_FLOOR + step
        )
        if result.outcome is not CombatOutcome.VICTORY:
            return False, 0.0
        character = character.with_health(result.health_left, stats.max_health)
    return True, character.health / stats.max_health


def test_wounds_add_up_over_a_run(content: GameContent) -> None:
    """Wounds carry, so a location is walked with an eye on the health line.

    Fight after fight with nothing drunk and no bed paid for. How far a character
    gets is theirs - plate walks the whole location and a robe stops for a potion
    halfway - but a run is walked far more often than not, and nobody comes out
    of one untouched.
    """
    walked = [
        _walk(content, klass.id, offset)
        for klass in content.classes
        for offset in range(RUN_TRIALS)
    ]
    share = sum(1 for done, _ in walked if done) / len(walked)
    assert share >= RUN_SURVIVAL, f"only {share:.0%} of runs are walked to the end"

    left = [health for done, health in walked if done]
    assert max(left) < 1.0, f"{RUN_LENGTH_FLOOR} fights in a row and not a scratch"


def test_no_class_makes_a_boss_a_different_game(content: GameContent) -> None:
    """The classes are allowed to feel different, not to be a different length.

    A rogue used to finish a boss in half the turns a cleric needed, which made
    the same content a ten-turn fight for one player and a five-turn one for
    another (Roadmap, "Риски"). Sampled at both ends of the band: the gap used to
    be widest at the top, where crit caps out.
    """
    for level in (10, 40, 150, 300):
        medians = {
            class_id: statistics.median(sample(content, class_id, level, rank=EnemyRank.BOSS))
            for class_id in (klass.id for klass in content.classes)
        }
        fastest = min(medians.values())
        slowest = max(medians.values())
        assert slowest <= fastest * CLASS_SPREAD, f"at {level}: {medians}"


@pytest.mark.parametrize("class_id", CLASSES)
def test_reading_the_intent_shortens_the_fight(content: GameContent, class_id: str) -> None:
    """The whole point of intent, trace and breach: choosing well has to pay.

    Ordinary fights, where both players win, so the comparison is turns and not
    survival - a player who dies on turn four also "finished" in four turns.

    The ceiling is the other half of the promise: tempo is how a fight is fought
    well, not the only fight there is. If a breach ever becomes worth more than
    everything else put together, the thresholds in ``domain/rules/combat.py``
    are what moves - not the mechanic (Roadmap, "Риски").
    """
    clever = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL))
    plain = sum(sample(content, class_id, 40, rank=EnemyRank.NORMAL, clever=False))
    assert clever < plain, f"{class_id}: {clever} turns played well vs {plain} turns of attacking"
    assert clever * TEMPO_CEILING >= plain, (
        f"{class_id}: tempo alone is worth {plain / clever:.1f} fights"
    )


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
