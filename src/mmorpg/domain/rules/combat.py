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
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack
from mmorpg.domain.entities.location import DamageElement, Enemy, EnemyRank
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.procgen.enemies import RANK_FACTORS, group_scale
from mmorpg.domain.rules import edges as edge_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.progression import experience_reward
from mmorpg.domain.rules.skill_effects import (
    BLEED_PER_TURN,
    COUNTER,
    MEND_PER_TURN,
    SHIELD_HELD,
    UNDYING,
    UNSTUNNABLE,
    EffectCategory,
    EffectSpec,
    cleansed_count,
    recharged,
    spec_for,
    tag_of_skill,
)
from mmorpg.domain.rules.stats import DerivedStats, derived_stats, primary_stats

#: Какая доля удара достаётся цели каждый ход, пока она истекает кровью. Четверть:
#: три хода кровотечения стоят примерно трёх четвертей ещё одного удара, и это
#: заметно, но не заменяет собой сам удар.
BLEED_SHARE = 0.25

#: Сколько ходов держится щит, которому срок не назначен, - щит от грани.
DEFAULT_SHIELD_TURNS = 3

# --- прибавки, которые смотрят по сторонам ---------------------------
#
# Половина словаря ``traits.toml`` полгода была надписью: «урон по зверям выше»,
# «первый удар в бою сильнее», «на низком здоровье вы бьёте сильнее» - всё это
# показывалось на карточке, складывалось в общий свёрток модификаторов и нигде
# не читалось. Читается здесь, и только здесь: один проход по обстоятельствам
# удара, один множитель на выходе (``Claude.md``, правило 7).

#: Чем удар считается ударом чар, а не руки: стихия, названная у самого умения.
#: Обычный удар и умение без стихии - физические.
MAGIC_TAGS = frozenset({"arcane", "cold", "elemental", "fire", "holy", "nature", "poison"})

#: Прибавка к урону по породе противника - тем же ключом, каким она объявлена.
KIND_DAMAGE_KEYS: dict[str, str] = {
    "beast": "beast_damage_percent",
    "undead": "undead_damage_percent",
    "humanoid": "humanoid_damage_percent",
}

#: Кого «эпическим» считает прибавка ``elite_damage_percent``: обе длинные ступени.
ELITE_RANKS = frozenset({EnemyRank.ELITE, EnemyRank.BOSS})

#: С кем можно договориться. Умение половинчатого эльфа кончает бой миром - но
#: только с тем, кто способен на мир: волк не торгуется.
REASONING_KINDS = frozenset({"humanoid"})

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
# Сколько к удару прибавляет ведущая характеристика. Кривую уровня несут кости
# оружия (ADR 0015), а характеристика — разброс между теми, кто на одном уровне:
# если бы её не было, два героя одного уровня с одним мечом били бы одинаково.
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

# Armour is softened against the defender's own level, and both the softener and
# what a worn piece of armour is worth live in ``domain/rules/equipment.py`` - the
# two are one curve read from both ends, and splitting them let armour and damage
# drift apart once already (ADR 0007).
ARMOR_SOFTENER_BASE = gear.ARMOR_SOFTENER_BASE
ARMOR_SOFTENER_PER_LEVEL = gear.ARMOR_SOFTENER_PER_LEVEL
armor_factor = gear.armor_factor

# --- темп: кто успевает раньше ---------------------------------------
#
# Инициатива полгода была числом на экране характеристик и больше ничем: экран
# говорил «это ещё и очередь удара», а очереди в бою не было вовсе, и полтора
# десятка умений, граней и вещей, обещавших её отнять или прибавить, не делали
# ничего. Здесь у неё появляется ровно одно последствие, и оно то самое, о
# котором говорит текст: кто быстрее, того противник не успевает достать.
#
# Считается по двум величинам сразу - по самой инициативе (её несёт ловкость) и
# по процентам, которыми её двигают вещи, грани и умения. Первая нормируется,
# иначе на трёхсотом уровне ловкач отменял бы бой целиком; вторые складываются
# как есть, потому что «инициатива ниже на 30 процентов» - это и есть тридцать.

#: Какую долю от разницы в инициативе берёт темп. Половина: инициатива - не
#: замена уклонению, а второе его лицо, и вдвоём они не должны отменять бой.
OUTPACE_SHARE = 0.5
#: И сколько темп может отнять у противника, как бы велика ни была разница.
MAX_OUTPACE = 30.0


def outpace_chance(
    player_initiative: float,
    enemy_initiative: float,
    player_percent: float,
    enemy_percent: float,
) -> float:
    """Шанс, что противник не успеет ответить в этот ход, в процентах."""
    pace = (
        100.0
        * (player_initiative - enemy_initiative)
        / max(1.0, player_initiative + enemy_initiative)
    )
    return min(MAX_OUTPACE, max(0.0, pace * OUTPACE_SHARE + player_percent - enemy_percent))


# --- прибавки по обстоятельствам --------------------------------------


def _is_magic(spec: EffectSpec | None) -> bool:
    """Чары это или рука. Обычный удар - всегда рука."""
    return spec is not None and bool(MAGIC_TAGS & set(spec.tags))


def situational_damage(
    modifiers: Mapping[str, float],
    *,
    spec: EffectSpec | None,
    enemy: Enemy,
    enemy_health_ratio: float,
    player_health_ratio: float,
    turn: int,
) -> float:
    """Множитель, который дают прибавки, смотрящие по сторонам.

    Всё это - обычные ключи из ``traits.toml``, и каждый из них до сих пор
    показывался игроку и не считался никем. Складываются они в проценты и лишь
    потом становятся множителем: порядок источников не должен ничего решать
    (``rules/modifiers``).
    """
    total = 0.0
    kind = "magic" if _is_magic(spec) else "physical"
    total += modifiers.get(f"{kind}_damage_percent", 0.0)
    total += modifiers.get(
        "aoe_damage_percent" if spec is not None and spec.aoe else "single_target_damage_percent",
        0.0,
    )
    if turn <= 1:
        total += modifiers.get("first_turn_damage_percent", 0.0)
    if player_health_ratio <= LOW_HEALTH_THRESHOLD:
        total += modifiers.get("low_health_damage_percent", 0.0)
    if enemy_health_ratio <= WOUNDED_RATIO:
        total += modifiers.get("wounded_target_damage_percent", 0.0)
    if enemy.rank in ELITE_RANKS:
        total += modifiers.get("elite_damage_percent", 0.0)
    if (key := KIND_DAMAGE_KEYS.get(enemy.kind)) is not None:
        total += modifiers.get(key, 0.0)
    return 1.0 + total / 100.0


#: Каким ключом называется сопротивление каждой стихии.
RESIST_KEYS: dict[DamageElement, str] = {
    DamageElement.PHYSICAL: "resist_physical_percent",
    DamageElement.MAGIC: "resist_magic_percent",
    DamageElement.FIRE: "resist_fire_percent",
    DamageElement.COLD: "resist_cold_percent",
    DamageElement.POISON: "resist_poison_percent",
}


def incoming_damage_factor(modifiers: Mapping[str, float], enemy: Enemy) -> float:
    """Что сопротивление оставляет от удара противника.

    У чужого удара есть стихия (``entities/location.DamageElement``), и
    сопротивление считается по ней. Раньше её не было вовсе - была только порода
    бьющего, - и три сопротивления из содержимого, огню, холоду и яду, не
    считались ни разу: ключ в словаре, механики никакой (ADR 0018).
    """
    key = RESIST_KEYS.get(enemy.element, "resist_physical_percent")
    return max(0.0, 1.0 - modifiers.get(key, 0.0) / 100.0)


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

    # Ход, которого не было, ходом не считается. Пустой слот, откат, нехватка
    # ресурса и не то оружие в руке - это отказ до начала хода: раньше он
    # прокручивал ход целиком, и промахнувшийся мимо кнопки игрок бесплатно
    # получал удар от каждого врага. Счётчик ходов стоит на месте, след цел,
    # откаты не тикают - игрок просто нажимает ещё раз.
    refusal = _refusal(content, character, state, action)
    if refusal is not None:
        return replace(state, events=(refusal,))

    source = random.Random(int.from_bytes(seed, "big"))
    working = replace(state, events=())
    tempo = _tempo(content, character, working, action)

    unstunnable = working.player.effects.modifiers().get(UNSTUNNABLE, 0.0) > 0
    if working.player.stunned > 0 and unstunnable:
        # Обещание держится с той стороны, с какой его дают: пока умение стоит,
        # пропуск хода герою не грозит, чем бы его ни пытались сбить.
        working = replace(working, player=replace(working.player, stunned=0))
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

    working = _check_outcome(content, character, working, source)
    if working.is_over:
        return working

    if tempo.breakthrough:
        # The exchange is broken: the enemies spend the turn finding their feet.
        working = working.with_events(
            CombatEvent(kind=EventKind.BREAKTHROUGH, actor=working.player.name)
        )
    else:
        working = _enemy_actions(content, character, working, tempo, source)
        working = _check_outcome(content, character, working, source)
        if working.is_over:
            return working

    return _end_of_turn(content, character, working)


def _refusal(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> CombatEvent | None:
    """Почему это действие не состоится. ``None`` - состоится.

    Спрашивается до всего остального и только про то, что игра отказывается
    делать вовсе. Промах отказом не является: он и есть результат хода.
    """
    if state.player.stunned > 0:
        return None
    match action.kind:
        case ActionKind.SKILL | ActionKind.RACIAL:
            attempt = _attempt_skill(content, character, state, action)
            return attempt if isinstance(attempt, CombatEvent) else None
        case _:
            return None


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
            return _use_item(content, character, state, action)
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

    struck, _ = _strike(
        state,
        target=target,
        power=blow_roll(content, character, source, state.player.effects)
        * BASIC_ATTACK_PERCENT
        / 100.0,
        character_level=character.level,
        stats=stats,
        modifiers=modifiers,
        spec=None,
        skill_name="Атака",
        tempo=tempo,
        source=source,
    )
    return struck


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

    Оружие в руке множит этот удар на долю своего рода: двуручник тяжелее
    кинжала, а голые руки - единица отсчёта, ниже которой оружия не бывает
    (``domain/rules/equipment.py``).
    """
    dice, bonus = _blow_parts(content, character, effects, scaling)
    return dice.average + bonus


def _blow_parts(
    content: GameContent,
    character: Character,
    effects: EffectStack | None,
    scaling: StatCode | None,
) -> tuple[Dice, float]:
    """Кости оружия и прибавка от ведущей характеристики, порознь."""
    if scaling is None:
        klass = content.character_class(character.class_id)
        scaling = klass.key_stats[0] if klass.key_stats else None
    primary = primary_stats(content, character, effects)
    stat_value = primary[scaling] if scaling is not None else 0
    return gear.weapon_dice(content, character), BLOW_PER_STAT * stat_value


def blow_range(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
    scaling: StatCode | None = None,
) -> tuple[int, int]:
    """Границы одного удара — то, что игрок слышит вместо среднего.

    Экран называет «от 34 до 96», а не «65»: среднее ничего не говорит о том,
    чем булава отличается от меча, а границы говорят.
    """
    dice, bonus = _blow_parts(content, character, effects, scaling)
    return round(dice.low + bonus), round(dice.high + bonus)


def blow_roll(
    content: GameContent,
    character: Character,
    source: random.Random,
    effects: EffectStack | None = None,
    scaling: StatCode | None = None,
) -> float:
    """Один настоящий удар: бросок костей оружия плюс характеристика."""
    dice, bonus = _blow_parts(content, character, effects, scaling)
    return dice.roll(source) + bonus


# --- skills ----------------------------------------------------------


def _resolve_skill(
    content: GameContent, character: Character, action: CombatAction
) -> Skill | None:
    """The skill behind a pressed slot, or ``None`` when there is none.

    A loadout outlives the content it names: a skill dropped between two releases
    leaves its code sitting in somebody's panel. That slot reads as empty rather
    than raising in the middle of a fight (``Claude.md``, rule 8).
    """
    if action.kind is ActionKind.RACIAL:
        code = character.loadout.racial or content.race(character.race_id).active_code
        return content.skill(code) if content.has_skill(code) else None
    if action.slot is None:
        return None
    actives = character.loadout.actives
    if not 0 <= action.slot < len(actives):
        return None
    slotted = actives[action.slot]
    return content.skill(slotted) if slotted and content.has_skill(slotted) else None


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

    # Умение просит оружие раньше, чем ресурс: платить за удар, который нечем
    # нанести, игрок не должен.
    if refusal := gear.skill_refusal(content, character, skill):
        return CombatEvent(kind=EventKind.WRONG_WEAPON, skill_name=skill.name, effect_name=refusal)

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
    # Грань переписывает и силу, и само действие: что именно - объявлено в
    # содержимом и разобрано в ``domain/rules/edges.py``.
    edge = skill_rules.chosen_edge(character, skill)
    power = skill.power_at_rank(rank) * edge_rules.power_factor(edge)
    spec = edge_rules.applied(spec_for(skill.effect), edge)
    cooldown = recharged(edge_rules.cooldown_of(skill.cooldown, edge), spec, power)
    # Откаты короче - это прибавка, которую носят вещи; до сих пор её никто не
    # читал. Ниже одного хода откат не сокращается: умение с откатом - это
    # умение, которое нельзя нажимать подряд.
    reduction = mods.collect_modifiers(content, character, state.player.effects).get(
        "cooldown_reduction_percent", 0.0
    )
    if cooldown and reduction:
        cooldown = max(1, round(cooldown * max(0.0, 1.0 - reduction / 100.0)))

    player = replace(state.player, resource=state.player.resource - cost, free_cast=False)
    if cooldown:
        # +1 because cooldowns tick down at the end of this same turn, so the skill
        # stays unavailable for exactly `cooldown` further turns.
        player = player.with_cooldown(skill.code, cooldown + 1)
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
    # Свои кости умения — то, что оно добавляет сверх оружия. Растут они рангом,
    # как и всё остальное в умении.
    rank_scale = 1.0 + skill.rank_step * (character.loadout.rank_of(skill.code) - 1)
    own_dice = skill.dice
    working = state

    if spec.special == "avoid_combat":
        # Договориться можно с тем, кто способен на разговор. С волком нельзя, и
        # умение об этом говорит - в тексте, который игрок читает до нажатия.
        if any(one.enemy.kind not in REASONING_KINDS for one in working.living_enemies):
            return working.with_events(
                CombatEvent(kind=EventKind.FLEE_FAILED, skill_name=skill.name)
            )
        if source.uniform(0, 100) < power + stats.crit_chance:
            return replace(working, outcome=CombatOutcome.AVOIDED).with_events(
                CombatEvent(kind=EventKind.AVOIDED, skill_name=skill.name)
            )
        return working.with_events(CombatEvent(kind=EventKind.FLEE_FAILED, skill_name=skill.name))

    # Кого удар достал. Пусто - умение промахнулось мимо всех, и вешать на них
    # нечего. У умения, которое не бьёт вовсе, броска нет, а значит нет и
    # промаха: его эффект ложится как и раньше.
    landed: list[int] = []
    blow = 0.0
    if spec.category is EffectCategory.DAMAGE:
        targets = working.living_enemies if spec.aoe else _single_target(working, target_index)
        falloff = 1.0
        for target in targets:
            for _ in range(spec.hits):
                current = working.enemy_at(target.index)
                if current is None:
                    break
                # Каждый удар — свой бросок: два удара подряд одним и тем же
                # оружием не обязаны совпасть, иначе кости были бы украшением.
                blow = blow_roll(content, character, source, state.player.effects, skill.scaling)
                working, hit = _strike(
                    working,
                    target=current,
                    power=blow * power / 100.0 * spec.damage_scale * falloff
                    + (own_dice.roll(source) * rank_scale if own_dice is not None else 0.0),
                    character_level=character.level,
                    stats=stats,
                    modifiers=modifiers,
                    spec=spec,
                    skill_name=skill.name,
                    tempo=tempo,
                    source=source,
                )
                if hit and target.index not in landed:
                    landed.append(target.index)
            falloff *= 1.0 - spec.chain_falloff

        working = _bleeding(
            working,
            spec=spec,
            skill=skill,
            blow=blow,
            power=power,
            struck=tuple(landed),
            modifiers=modifiers,
        )
        working = _splash(
            working,
            spec=spec,
            spared=target_index,
            blow=blow,
            power=power,
            character_level=character.level,
            stats=stats,
            modifiers=modifiers,
            skill_name=skill.name,
            tempo=tempo,
            source=source,
        )

    if spec.category is EffectCategory.HEAL:
        # Healing and shields are percentages of maximum health, not of a blow:
        # health grows five times faster than a blow does, so a heal priced in
        # blows would be worth nothing by level 40.
        #
        # Лечение по ходам не лечит сейчас: ``power`` у него - это то, что
        # приходит каждый ход, и оно ложится сроком, а не разом.
        amount = round(working.player.max_health * power / 100.0)
        amount = round(amount * mods.percent(modifiers, "healing_done_percent"))
        if spec.special == "heal_over_time":
            working = _mending(working, skill=skill, spec=spec, per_turn=float(amount))
        else:
            working = _heal(working, amount, modifiers, skill_name=skill.name)

    if spec.category is EffectCategory.SHIELD:
        shield = round(working.player.max_health * power / 100.0)
        working = _shielded(working, skill=skill, spec=spec, amount=shield)

    if spec.bonus_heal:
        # Лечение, которое принесла грань бьющему умению: доля максимума здоровья,
        # как и всякое лечение, и вдобавок к тому, что умение делало и раньше.
        extra = round(working.player.max_health * spec.bonus_heal / 100.0)
        working = _heal(working, extra, modifiers, skill_name=skill.name)

    if spec.bonus_shield:
        extra = round(working.player.max_health * spec.bonus_shield / 100.0)
        working = _shielded(working, skill=skill, spec=spec, amount=extra)

    if spec.cleanse_count:
        before = len(working.player.effects.penalties())
        cleansed = working.player.effects.cleanse(cleansed_count(spec, power))
        removed = before - len(cleansed.penalties())
        working = replace(working, player=replace(working.player, effects=cleansed))
        if removed:
            working = working.with_events(
                CombatEvent(kind=EventKind.CLEANSED, amount=removed, skill_name=skill.name)
            )

    if spec.self_damage_taken:
        # Замах, который открывает бьющего: цена размашистого удара - этот же
        # ход, прожитый без защиты. Стояла ценой в описании и не бралась ни разу.
        opened = ActiveEffect(
            id=f"{skill.code}_opened",
            name=skill.name,
            modifiers={"damage_taken_percent": spec.self_damage_taken},
            turns_left=1,
            source=skill.code,
            beneficial=False,
        )
        working = replace(
            working, player=replace(working.player, effects=working.player.effects.apply(opened))
        )

    working = _apply_modifier_bundles(
        content,
        character,
        working,
        skill,
        spec,
        power,
        target_index,
        landed=tuple(landed) if spec.category is EffectCategory.DAMAGE else None,
    )
    return _apply_special(working, skill, spec, power, target_index)


def _heal(
    state: CombatState,
    amount: int,
    modifiers: Mapping[str, float],
    *,
    skill_name: str = "",
) -> CombatState:
    """Вернуть герою здоровье и сказать об этом.

    Через одну дверь проходит всё лечение, которое герой получает, - потому и
    ``healing_taken_percent`` считается здесь: до этого «получаемое вами лечение
    сильнее» стояло у двух постоянных умений и не значило ничего.
    """
    healed = round(amount * mods.percent(modifiers, "healing_taken_percent"))
    player, restored = state.player.healed(max(0, healed))
    if not restored:
        return state
    return replace(state, player=player).with_events(
        CombatEvent(kind=EventKind.HEAL, actor=player.name, amount=restored, skill_name=skill_name)
    )


def _mending(state: CombatState, *, skill: Skill, spec: EffectSpec, per_turn: float) -> CombatState:
    """Лечение, которое приходит каждый ход, а не сейчас.

    Срок стоял в описании двух умений и в тексте, который читает игрок, и не
    делал ничего: умение лечило один раз и три хода молчало.
    """
    effect = ActiveEffect(
        id=f"{skill.code}_mend",
        name=skill.name,
        modifiers={MEND_PER_TURN: per_turn},
        turns_left=max(1, spec.dot_turns),
        source=skill.code,
        beneficial=True,
    )
    player = replace(state.player, effects=state.player.effects.apply(effect))
    return replace(state, player=player).with_events(
        CombatEvent(
            kind=EventKind.EFFECT_APPLIED,
            actor=player.name,
            effect_name=skill.name,
            turns=max(1, spec.dot_turns),
        )
    )


def _shielded(state: CombatState, *, skill: Skill, spec: EffectSpec, amount: int) -> CombatState:
    """Щит и срок, который его держит.

    Щит без срока не сгорает вовсе: четыре умения обещали «3 хода», а щит стоял
    до конца боя и складывался сам с собой. Сколько щита держит этот источник,
    помнит сам источник, и вместе с ним щит и уходит (``_end_of_turn``).
    """
    if amount <= 0:
        return state
    player = replace(state.player, shield=state.player.shield + amount)
    turns = spec.shield_turns or DEFAULT_SHIELD_TURNS
    effect = ActiveEffect(
        id=f"{skill.code}_shield",
        name=skill.name,
        modifiers={SHIELD_HELD: float(amount)},
        turns_left=turns,
        source=skill.code,
        beneficial=True,
    )
    player = replace(player, effects=player.effects.apply(effect))
    return replace(state, player=player).with_events(
        CombatEvent(kind=EventKind.SHIELD, actor=player.name, amount=amount, skill_name=skill.name)
    )


def _bleeding(
    state: CombatState,
    *,
    spec: EffectSpec,
    skill: Skill,
    blow: float,
    power: float,
    struck: tuple[int, ...],
    modifiers: Mapping[str, float],
) -> CombatState:
    """Оставить на раненых то, что будет их точить каждый ход.

    Ложится на всех, кого умение только что задело, - у площадного огня горят все.
    Один источник на цель: повторный поджог обновляет срок, а не жжёт вдвое
    (``entities/effects.EffectStack.apply``).
    """
    if not spec.dot_turns or spec.category is not EffectCategory.DAMAGE:
        return state
    per_turn = max(
        1.0,
        blow * power / 100.0 * BLEED_SHARE * mods.percent(modifiers, "dot_damage_percent"),
    )
    working = state
    for index in struck:
        target = working.enemy_at(index)
        if target is None or target.health <= 0:
            continue
        effect = ActiveEffect(
            id=f"{skill.code}_dot",
            name=skill.name,
            modifiers={BLEED_PER_TURN: per_turn},
            turns_left=spec.dot_turns,
            source=skill.code,
            beneficial=False,
        )
        working = working.replace_enemy(replace(target, effects=target.effects.apply(effect)))
    return working


def _splash(
    state: CombatState,
    *,
    spec: EffectSpec,
    spared: int,
    blow: float,
    power: float,
    character_level: int,
    stats: DerivedStats,
    modifiers: Mapping[str, float],
    skill_name: str,
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    """Вторая цель, которую задевает одноцелевой удар.

    Только от грани: умение, бьющее двоих само по себе, описывается как ``aoe``
    или ``damage_chain``. Задевается один сосед, а не все, - «размах» на то и
    размах, чтобы отличаться от вихря.
    """
    if not spec.splash or spec.aoe:
        return state
    neighbour = next((one for one in state.living_enemies if one.index != spared), None)
    if neighbour is None:
        return state
    struck, _ = _strike(
        state,
        target=neighbour,
        power=blow * power / 100.0 * spec.damage_scale * spec.splash,
        character_level=character_level,
        stats=stats,
        modifiers=modifiers,
        spec=spec,
        skill_name=skill_name,
        tempo=tempo,
        source=source,
    )
    return struck


def _single_target(state: CombatState, target_index: int) -> tuple[EnemyState, ...]:
    target = state.enemy_at(target_index) or state.first_living()
    return (target,) if target is not None else ()


def _apply_modifier_bundles(
    content: GameContent,
    character: Character,
    state: CombatState,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    target_index: int,
    *,
    landed: tuple[int, ...] | None = None,
) -> CombatState:
    """Усиления себе и помехи цели.

    ``landed`` - те, кого удар этого умения достал. ``None`` значит, что удара
    не было вовсе (чистая помеха, чистое усиление), и тогда цель одна - та, по
    которой умение применили.
    """
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
        working = _repooled(content, character, working)
        working = working.with_events(
            CombatEvent(
                kind=EventKind.EFFECT_APPLIED,
                actor=working.player.name,
                effect_name=skill.name,
                turns=spec.duration,
            )
        )

    if spec.target_modifiers and spec.duration:
        if landed is None:
            targets = working.living_enemies if spec.aoe else _single_target(working, target_index)
        else:
            targets = tuple(one for index in landed if (one := working.enemy_at(index)) is not None)
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


def _repooled(content: GameContent, character: Character, state: CombatState) -> CombatState:
    """Пересчитать запас здоровья под теми усилениями, что сейчас на герое.

    Медвежий облик обещает «здоровье выше на 40 процентов», и до сих пор это
    было надписью: запас считался один раз, на входе в бой, и усиление, взятое
    посреди боя, не двигало его вовсе. Здоровье при этом не дарится - растёт
    потолок, а вместе с ним и то, что в него влезает.
    """
    stats = derived_stats(content, character, state.player.effects)
    if stats.max_health == state.player.max_health:
        return state
    gained = max(0, stats.max_health - state.player.max_health)
    player = replace(
        state.player,
        max_health=stats.max_health,
        health=min(stats.max_health, state.player.health + gained),
    )
    return replace(state, player=player)


def _apply_special(
    state: CombatState, skill: Skill, spec: EffectSpec, power: float, target_index: int = 0
) -> CombatState:
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
            # Доля того, что несёт обворованный, а не написанное число: сотня
            # золота - состояние на первом уровне и мелочь на сотом (ADR 0007).
            target = state.enemy_at(target_index) or state.first_living()
            if target is not None:
                stolen = max(1, round(target.enemy.gold * power / 100.0))
                working = replace(working, gold=working.gold + stolen)
    return working


# --- striking --------------------------------------------------------


def _strike(
    state: CombatState,
    *,
    target: EnemyState,
    power: float,
    character_level: int,
    stats: DerivedStats,
    modifiers: Mapping[str, float],
    spec: EffectSpec | None,
    skill_name: str,
    tempo: TurnTempo,
    source: random.Random,
) -> tuple[CombatState, bool]:
    """Один удар. Второй член - попал ли он.

    Попал или нет решает не только урон: всё, что умение вешает на цель -
    кровотечение, помеха, оглушение, - идёт следом за попаданием. Раньше не
    шло: игра говорила «Промах» и тут же «наложен эффект», и это был один и тот
    же удар.
    """
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
        return (
            working.with_events(
                CombatEvent(kind=EventKind.MISS, target=target.name, skill_name=skill_name)
            ),
            False,
        )

    raw = power
    raw *= mods.percent(modifiers, "damage_percent")
    raw *= situational_damage(
        modifiers,
        spec=spec,
        enemy=enemy,
        enemy_health_ratio=target.health / max(1, enemy.max_health),
        player_health_ratio=state.player.health / max(1, state.player.max_health),
        turn=state.turn,
    )
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
    return working, True


# --- items and fleeing -----------------------------------------------


def _use_item(
    content: GameContent, character: Character, state: CombatState, action: CombatAction
) -> CombatState:
    if action.item_id is None or not content.has_item(action.item_id):
        return state
    item = content.item(action.item_id)
    if item.effect is None:
        return state
    working = state
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    match item.effect.kind:
        # No flat magnitudes here either: a potion worth 40 health is a potion
        # worth nothing by level 20 (ADR 0007).
        case "heal_percent":
            amount = round(working.player.max_health * item.effect.power / 100.0)
            working = _heal(working, amount, modifiers)
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

        enemy_modifiers = current.effects.modifiers()

        # Темп решает до всего остального: не успевший ответить не мажет и не
        # попадает - он просто не доходит до замаха.
        outpace = outpace_chance(
            stats.initiative,
            current.enemy.initiative,
            modifiers.get("initiative_percent", 0.0),
            enemy_modifiers.get("initiative_percent", 0.0),
        )
        if outpace and source.uniform(0, 100) < outpace:
            working = working.with_events(CombatEvent(kind=EventKind.OUTPACED, actor=current.name))
            continue

        # The intent was announced before the player moved, so it is honoured here
        # even if the enemy has been wounded since.
        intent = tempo.intents.get(current.index, ActionTag.PRESS)

        hit_chance = min(
            MAX_HIT_CHANCE,
            max(
                MIN_HIT_CHANCE,
                ENEMY_ACCURACY_BASE * mods.percent(enemy_modifiers, "accuracy_percent")
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
        raw *= 1.0 + enemy_modifiers.get("damage_percent", 0.0) / 100.0
        raw *= armor_factor(stats.armor, character.level)
        raw *= mods.percent(modifiers, "damage_taken_percent")
        raw *= 1.0 + working.player.effects.modifiers().get("damage_taken_percent", 0.0) / 100.0
        raw *= incoming_damage_factor(modifiers, current.enemy)

        amount = max(1, round(raw))
        player, lost = working.player.damaged(amount)
        # Пока держится «Последний рубеж», герой не падает: обещание умения
        # стояло в тексте с самого начала и до сих пор не значило ничего.
        if not player.alive and working.player.effects.modifiers().get(UNDYING, 0.0) > 0:
            player = replace(player, health=1)
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
            continue

        working = _answered(
            content,
            character,
            working,
            attacker=current.index,
            taken=amount,
            stats=stats,
            modifiers=modifiers,
            tempo=tempo,
            source=source,
        )
    return working


def _answered(
    content: GameContent,
    character: Character,
    state: CombatState,
    *,
    attacker: int,
    taken: int,
    stats: DerivedStats,
    modifiers: Mapping[str, float],
    tempo: TurnTempo,
    source: random.Random,
) -> CombatState:
    """Чем герой отвечает тому, кто по нему только что попал.

    Два обещания, за которыми до сих пор ничего не стояло: «вы отвечаете на
    каждый удар по вам» у выпада воина и «часть полученного урона возвращается
    обидчику» у постоянного умения паладина. Первое - настоящий удар, со всем,
    что удару причитается; второе - доля того, что дошло, и брони она не знает:
    отражается не замах, а боль.
    """
    working = state
    target = working.enemy_at(attacker)
    if target is None:
        return working

    reflect = modifiers.get("reflect_percent", 0.0)
    if reflect > 0:
        amount = max(1, round(taken * reflect / 100.0))
        hurt = target.damaged(amount)
        working = working.replace_enemy(hurt).with_events(
            CombatEvent(kind=EventKind.DAMAGE, target=hurt.name, amount=amount)
        )
        if not hurt.alive:
            return working.with_events(CombatEvent(kind=EventKind.ENEMY_DEFEATED, target=hurt.name))
        target = hurt

    counter = working.player.effects.modifiers().get(COUNTER, 0.0)
    if counter > 0:
        # Именем отвечает то умение, которое отвечать и научило: игрок слышит
        # «Ответный выпад», а не безымянный урон.
        named = next(
            (effect.name for effect in working.player.effects if COUNTER in effect.modifiers),
            "",
        )
        working, _ = _strike(
            working,
            target=target,
            power=blow_roll(content, character, source, working.player.effects) * counter / 100.0,
            character_level=character.level,
            stats=stats,
            modifiers=modifiers,
            spec=None,
            skill_name=named,
            tempo=tempo,
            source=source,
        )
    return working


# --- upkeep ----------------------------------------------------------


def _end_of_turn(content: GameContent, character: Character, state: CombatState) -> CombatState:
    modifiers = mods.collect_modifiers(content, character, state.player.effects)
    stats = derived_stats(content, character, state.player.effects)

    player = state.player.tick_cooldowns()
    # Лечение по ходам платит до того, как срок укоротится: умение, обещавшее три
    # хода, лечит три раза, а не два.
    mending = round(player.effects.modifiers().get(MEND_PER_TURN, 0.0))
    player = replace(player, effects=player.effects.tick())

    regen_percent = modifiers.get("regen_per_turn_percent", 0.0)
    if regen_percent:
        player, _ = player.healed(round(player.max_health * regen_percent / 100.0))
    player = replace(
        player,
        resource=min(player.max_resource, player.resource + round(stats.resource_regen)),
    )
    # Щит стоит ровно столько, сколько его держат: источник ушёл - ушёл и он.
    held = round(player.effects.modifiers().get(SHIELD_HELD, 0.0))
    player = replace(player, shield=min(player.shield, max(0, held)))

    working = _repooled(content, character, replace(state, player=player))
    if mending > 0:
        working = _heal(working, mending, modifiers)
    working = spend_bleeding(working)
    enemies = tuple(replace(enemy, effects=enemy.effects.tick()) for enemy in working.enemies)
    return replace(working, enemies=enemies, turn=working.turn + 1)


def spend_bleeding(state: CombatState) -> CombatState:
    """Кровотечение и горение платят по счёту - раз в ход, до самого конца срока."""
    working = state
    for current in state.living_enemies:
        amount = round(current.effects.modifiers().get(BLEED_PER_TURN, 0.0))
        if amount <= 0:
            continue
        hurt = replace(current, health=max(0, current.health - amount))
        working = working.replace_enemy(hurt).with_events(
            CombatEvent(kind=EventKind.DAMAGE, target=hurt.name, amount=amount)
        )
        if hurt.health <= 0:
            working = working.with_events(
                CombatEvent(kind=EventKind.ENEMY_DEFEATED, target=hurt.name)
            )
    return working


def _check_outcome(
    content: GameContent,
    character: Character,
    state: CombatState,
    source: random.Random | None = None,
) -> CombatState:
    if state.is_over:
        return state
    if not state.player.alive:
        return replace(state, outcome=CombatOutcome.DEFEAT)
    if not state.living_enemies:
        # Стая делит один бой - и делит его целиком. Здоровье и урон ей уже
        # делили при сборке (``procgen/enemies.group_scale``), а опыт и золото
        # множили на число тел: трое волков стоили полутора боёв по времени и
        # платили как три. Отсюда и брался самый выгодный способ играть - искать
        # стаи побольше, - вместо того, за что бой вообще считается.
        share = group_scale(len(state.enemies))
        experience = round(
            share
            * sum(
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
        # Снаряжение падает сверх сырья и только с побеждённого: обычный
        # противник платит золотом, хозяин логова — вещью, и реликтовой она
        # бывает только у него (``domain/procgen/items.py``).
        if source is not None:
            found = mods.collect_modifiers(content, character, state.player.effects)
            loot = (
                *loot,
                *(
                    dropped
                    for enemy in state.enemies
                    for dropped in (
                        item_procgen.roll_drop(
                            content,
                            source,
                            level=enemy.enemy.level,
                            rank=enemy.enemy.rank,
                            drop_bonus=found.get("drop_rate_percent", 0.0),
                            rarity_bonus=found.get("rarity_percent", 0.0),
                        ),
                    )
                    if dropped is not None
                ),
            )
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
