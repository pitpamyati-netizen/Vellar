"""Движок боя: одна очередь, две стороны, любой состав.

Один вызов :func:`act` исполняет ход того, чья очередь, а затем прокручивает
ходы всех, за кого ходит движок, - пока очередь не дойдёт до живого игрока или
бой не кончится. Поэтому одиночный бой с волками, отряд против стаи, поединок
двоих и отряд против отряда - это один и тот же код с разным составом сторон
(ADR 0021).

Таймеров нет нигде: очередь просто стоит и ждёт нажатия столько, сколько нужно.
Единственный выход из брошенного боя - кнопка «Сдаться» у того, кто ждать
устал; она отдаёт бой, а не отменяет его.

Три правила делают бой разменом, а не гонкой урона, и ни одно из них не
добавляет кнопки:

- **намерение** - тот, за кого ходит движок, объявляет тег следующего хода
  заранее; живой игрок объявляет его собственным следом, который противник
  видит на экране. Драться вслепую не приходится ни с той, ни с другой стороны;
- **след** - повтор тега даёт разгон и усиливает удар, три разных тега подряд
  ломают размен: перелом отдаёт бойцу лишний ход, а не отнимает его у всех
  разом, - в бою четверых «противники пропускают ход» решало бы бой целиком;
- **брешь** - тег, отвечающий на объявленный, снимает броню цели и вдвое
  ослабляет её ближайший удар.

Всякая величина - удар, умение, лечение - названа процентом от того, что растёт
само: удар считается костями оружия, лечение и щит - долей максимума здоровья.
Содержимое поэтому не переписывают с ростом уровней (ADR 0007, 0015).

Вся случайность идёт от семени, переданного снаружи, поэтому бой воспроизводим
по семени и последовательности действий. У намерений случайности нет вовсе: они
чистая функция бойца и круга, и экран с движком всегда называют одно и то же.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ATTACKERS,
    DEFENDERS,
    ActionKind,
    ActionTag,
    BattleAction,
    BattleEvent,
    BattleOutcome,
    BattleState,
    Combatant,
    CombatantKind,
    EventKind,
    Trace,
    counter_to,
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack
from mmorpg.domain.entities.location import DamageElement, Enemy
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.procgen.enemies import RANK_FACTORS, group_scale
from mmorpg.domain.procgen.seeds import derive, rng, to_int
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

__all__ = [
    "ATTACKERS",
    "DEFENDERS",
    "MOMENTUM_DAMAGE_PERCENT",
    "act",
    "blow_of",
    "blow_range",
    "blow_roll",
    "hero_combatant",
    "incoming_damage_factor",
    "intent_of",
    "is_low_health",
    "monster_combatant",
    "open_battle",
    "situational_damage",
    "spend_bleeding",
]

#: Какая доля удара достаётся цели каждый ход, пока она истекает кровью.
BLEED_SHARE = 0.25

#: Сколько ходов держится щит, которому срок не назначен, - щит от грани.
DEFAULT_SHIELD_TURNS = 3

#: Потолок на число ходов, которые движок прокручивает за одно нажатие. Бой
#: четверых, где все под управлением движка, кончается сам; счётчик стоит на
#: случай содержимого, которое лечит быстрее, чем бьёт.
MAX_AUTOPLAY_TURNS = 400

# --- прибавки, которые смотрят по сторонам ---------------------------
#
# Половина словаря ``traits.toml`` полгода была надписью: «урон по зверям выше»,
# «первый удар в бою сильнее», «на низком здоровье вы бьёте сильнее». Читается
# здесь, и только здесь: один проход по обстоятельствам удара, один множитель
# на выходе (``Claude.md``, правило 7).

#: Чем удар считается ударом чар, а не руки: стихия, названная у самого умения.
MAGIC_TAGS = frozenset({"arcane", "cold", "elemental", "fire", "holy", "nature", "poison"})

#: Какой стихией бьёт умение с такой пометкой. По ней считается сопротивление
#: цели - и цели-противника, и цели-игрока: в поединке двоих «Рождённый в стуже»
#: значит ровно то же, что и против ледяного волка.
TAG_ELEMENTS: dict[str, DamageElement] = {
    "fire": DamageElement.FIRE,
    "cold": DamageElement.COLD,
    "poison": DamageElement.POISON,
    "arcane": DamageElement.MAGIC,
    "elemental": DamageElement.MAGIC,
    "holy": DamageElement.MAGIC,
    "nature": DamageElement.MAGIC,
}

#: Прибавка к урону по породе цели - тем же ключом, каким она объявлена.
KIND_DAMAGE_KEYS: dict[str, str] = {
    "beast": "beast_damage_percent",
    "undead": "undead_damage_percent",
    "humanoid": "humanoid_damage_percent",
}

#: Кого «эпическим» считает прибавка ``elite_damage_percent``.
ELITE_RANKS = frozenset({"elite", "boss"})

#: С кем можно договориться. Умение половинчатого эльфа кончает бой миром - но
#: только с тем, кто способен на мир: волк не торгуется, а приключенец да.
REASONING_KINDS = frozenset({"humanoid"})

# --- одна шкала для всех чисел ---------------------------------------
#
# Кривую уровня несут кости оружия (ADR 0015), а ведущая характеристика - разброс
# между теми, кто на одном уровне: без неё два героя одного уровня с одним мечом
# били бы одинаково.
BLOW_PER_STAT = 0.6
BASIC_ATTACK_PERCENT = 100.0

MIN_HIT_CHANCE = 40.0
MAX_HIT_CHANCE = 97.0
#: Точность того, за кем стоит порода, а не характеристики.
#:
MONSTER_ACCURACY = 88.0
BASE_FLEE_CHANCE = 45.0
LOW_HEALTH_THRESHOLD = 0.35

# Точность отвечает *разницей* уровней, а не самим уровнем: измеренная
# абсолютно, она превращала любой бой на трёхсотом уровне в подбрасывание
# монеты.
ACCURACY_PER_LEVEL_GAP = 1.2

# Броня смягчается уровнем защищающегося; смягчение и цена надетого - одна кривая,
# прочитанная с двух концов, и живёт она в ``domain/rules/equipment.py``.
ARMOR_SOFTENER_BASE = gear.ARMOR_SOFTENER_BASE
ARMOR_SOFTENER_PER_LEVEL = gear.ARMOR_SOFTENER_PER_LEVEL
armor_factor = gear.armor_factor

# --- темп: намерение, след, брешь -------------------------------------

#: Круг, по которому ходят намерения того, за кого ходит движок.
INTENT_CYCLE = (ActionTag.PRESS, ActionTag.PRECISION, ActionTag.GUARD)
#: Четверть здоровья - и зверь перестаёт разменивать удары.
WOUNDED_RATIO = 0.25
#: Что объявленное намерение делает с бронёй, когда по ней бьют.
INTENT_ARMOR: dict[ActionTag, float] = {
    ActionTag.PRESS: 0.75,
    ActionTag.PRECISION: 1.0,
    ActionTag.GUARD: 2.0,
}
#: И с уроном собственного удара.
INTENT_DAMAGE: dict[ActionTag, float] = {
    ActionTag.PRESS: 1.2,
    ActionTag.PRECISION: 0.95,
    ActionTag.GUARD: 0.5,
}
#: Два одинаковых тега подряд - разгон, три разных - перелом.
MOMENTUM_STREAK = 2
#: Прибавка за каждый повтор сверх первого: третий тег подряд стоит +50%.
MOMENTUM_DAMAGE_PERCENT = 25.0
#: Удар того, в ком пробили брешь, доходит вполсилы.
BREACH_ANSWER_SCALE = 0.5


# --- сборка бойцов ----------------------------------------------------


def hero_combatant(
    content: GameContent,
    character: Character,
    *,
    combatant_id: int,
    side: int,
    live: bool = True,
    user_id: int = 0,
) -> Combatant:
    """Персонаж как боец.

    ``live`` - ждёт ли его ход нажатия. Слепок противника арены не ждёт, но
    дерётся тем же оружием и теми же умениями, что и его хозяин: выдуманного
    числа урона у него больше нет (ADR 0021).

    Бой начинается с тем здоровьем, с каким персонаж в него вошёл: раны
    переходят из узла в узел, и потому зелье и ночлег стоят денег.
    """
    stats = derived_stats(content, character)
    return Combatant(
        id=combatant_id,
        side=side,
        kind=CombatantKind.HERO,
        name=character.name,
        level=character.level,
        max_health=stats.max_health,
        health=character.health_or(stats.max_health),
        max_resource=stats.max_resource,
        resource=stats.max_resource,
        resource_name=stats.resource_name,
        initiative=stats.initiative,
        live=live,
        character_id=character.id,
        user_id=character.user_id if live else 0,
    )


def monster_combatant(enemy: Enemy, *, combatant_id: int, side: int = DEFENDERS) -> Combatant:
    """Противник как боец: всё, чем он дерётся, лежит в породе."""
    return Combatant(
        id=combatant_id,
        side=side,
        kind=CombatantKind.MONSTER,
        name=enemy.name,
        level=enemy.level,
        max_health=enemy.max_health,
        health=enemy.max_health,
        initiative=enemy.initiative,
        live=False,
        enemy=enemy,
    )


def open_battle(
    content: GameContent,
    roster: Mapping[int, Character],
    combatants: Sequence[Combatant],
    seed: bytes,
) -> BattleState:
    """Собрать бой и довести очередь до первого, кто ждёт нажатия.

    Никто не ходит первым по праву: очередь решает инициатива, и если волк
    быстрее, первый удар его. Раньше игрок ходил первым всегда - это было не
    правило, а следствие того, что другой стороны в движке не существовало.
    """
    fighters = tuple(combatants)
    state = BattleState(combatants=fighters, order=_order_for(fighters, seed, 1))
    state = _drive(content, roster, state, seed)
    return state


def _order_for(combatants: Sequence[Combatant], seed: bytes, round_number: int) -> tuple[int, ...]:
    """Очередь на круг: быстрые раньше, равные - по жребию круга.

    Очередь решает одна инициатива, и это единственное, что инициатива делает
    (ADR 0021). Порядок пересобирается каждый круг, но при неизменных числах он
    и получается тем же: боец не может отходить дважды подряд оттого, что круг
    кончился, - двойной ход достаётся только тому, кого и правда ускорили.

    Жребий берётся из семени боя, а не из номера круга: иначе двое с одинаковой
    инициативой менялись бы местами каждый круг и то один, то другой ходил бы
    дважды подряд.
    """

    def key(one: Combatant) -> tuple[float, int]:
        boost = 1.0 + one.effects.modifiers().get("initiative_percent", 0.0) / 100.0
        lot = to_int(derive(seed, "order", one.id)) % 1000
        return (-one.initiative * boost, lot)

    return tuple(one.id for one in sorted((one for one in combatants if one.alive), key=key))


# --- намерение и темп -------------------------------------------------


def intent_of(state: BattleState, combatant: Combatant) -> ActionTag | None:
    """Что этот боец объявляет на свой следующий ход.

    Чистая и однозначная функция: экран печатает объявление, движок держит
    обещание. За кого ходит движок - тот идёт по кругу намерений и всегда
    закрывается, когда ранен. За живого игрока объявляет его собственный след:
    угадать чужой ход нельзя, но видно, чем он бил, и этого хватает, чтобы
    выбрать ответ (ADR 0021).
    """
    if combatant.live:
        return combatant.trace.last
    if combatant.health * 4 <= max(1, combatant.max_health):
        return ActionTag.GUARD
    # Место в своей стороне, а не номер в бою: двое рядом объявляют разное, и
    # объявляют они одно и то же, сколько бы народу ни стояло напротив.
    place = [one.id for one in state.combatants if one.side == combatant.side].index(combatant.id)
    step = int(combatant.initiative) + place + state.round
    return INTENT_CYCLE[step % len(INTENT_CYCLE)]


@dataclass(frozen=True, slots=True)
class TurnTempo:
    """Всё, что правила тегов решают о ходе, посчитанное до самого хода.

    Считать приходится заранее: разгон меняет урон того самого действия, которое
    его и заработало, а перелом решает, останется ли ход за бойцом.
    """

    intents: Mapping[int, ActionTag | None]
    tag: ActionTag | None = None
    streak: int = 0
    breakthrough: bool = False
    #: Что объявил на этот ход тот, кто ходит. У живого игрока объявления нет:
    #: он выбирает в момент нажатия, и его удар обычный.
    own_intent: ActionTag | None = None

    @property
    def momentum(self) -> bool:
        return self.streak >= MOMENTUM_STREAK

    def breached(self, combatant_id: int) -> bool:
        """Отвечает ли тег бойца на то, что объявила эта цель."""
        intent = self.intents.get(combatant_id)
        return intent is not None and self.tag is counter_to(intent)

    def armor_scale(self, combatant_id: int) -> float:
        if self.breached(combatant_id):
            return 0.0
        intent = self.intents.get(combatant_id)
        return INTENT_ARMOR[intent] if intent is not None else 1.0

    @property
    def damage_scale(self) -> float:
        return 1.0 + MOMENTUM_DAMAGE_PERCENT * max(0, self.streak - 1) / 100.0


# --- один ход ---------------------------------------------------------


def act(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    action: BattleAction,
    seed: bytes,
) -> BattleState:
    """Исполнить действие того, чья очередь, и докрутить бой до живого игрока.

    ``roster`` - персонажи за героями, по номеру бойца: движок читает по ним
    умения, оружие и прибавки. Противнику персонаж не нужен - у него порода.
    """
    if state.is_over:
        return replace(state, events=())
    current = state.active
    if current is None:  # pragma: no cover - очередь без живых уже была бы концом
        return replace(state, events=())

    working = replace(state, events=())

    # Смена цели ходом не считается: ничего не произошло, а значит и хода не
    # было (``Claude.md``, правило 3).
    if action.kind is ActionKind.FOCUS:
        chosen = working.by_id(action.target)
        if chosen is None or not chosen.alive or chosen.side == current.side:
            return working.with_events(BattleEvent(kind=EventKind.NO_TARGET))
        return working.replace_combatant(replace(current, focus=chosen.id))

    if action.kind is ActionKind.YIELD:
        return _leave(content, roster, working, current, seed, yielded=True)

    # Ход, которого не было, ходом не считается: пустой слот, откат, нехватка
    # ресурса и не то оружие в руке - это отказ до начала хода.
    refusal = _refusal(content, roster, working, current, action)
    if refusal is not None:
        return working.with_events(refusal)

    working = _take_turn(content, roster, working, current.id, action, seed)
    return _drive(content, roster, working, seed)


def _drive(
    content: GameContent, roster: Mapping[int, Character], state: BattleState, seed: bytes
) -> BattleState:
    """Прокрутить ходы всех, за кого ходит движок, до ближайшего живого игрока."""
    working = state
    for _ in range(MAX_AUTOPLAY_TURNS):
        if working.is_over:
            return working
        current = working.active
        if current is None or current.live:
            return working
        action = _chosen_by_engine(content, roster, working, current, seed)
        working = _take_turn(content, roster, working, current.id, action, seed)
    return working  # pragma: no cover - страховка от содержимого, лечащего вечно


def _turn_source(state: BattleState, seed: bytes) -> random.Random:
    """Случайность этого хода: круг и место в очереди, и ничего больше."""
    return rng(derive(seed, "turn", state.round, state.cursor))


def _take_turn(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor_id: int,
    action: BattleAction,
    seed: bytes,
) -> BattleState:
    """Один ход целиком: действие, след, исход, содержание, очередь."""
    source = _turn_source(state, seed)
    actor = state.by_id(actor_id)
    if actor is None or not actor.alive:  # pragma: no cover - очередь чистится сама
        return _advance(state, seed)

    unstunnable = actor.effects.modifiers().get(UNSTUNNABLE, 0.0) > 0
    if actor.stunned > 0 and unstunnable:
        # Обещание держится с той стороны, с какой его дают.
        actor = replace(actor, stunned=0)
        state = state.replace_combatant(actor)

    if actor.stunned > 0:
        working = state.replace_combatant(replace(actor, stunned=actor.stunned - 1)).with_events(
            BattleEvent(kind=EventKind.TURN_SKIPPED, actor_id=actor.id, actor=actor.name)
        )
        working = _upkeep(content, roster, working, actor.id)
        return _advance(working, seed)

    tempo = _tempo(content, roster, state, actor, action)
    working = _announce_tempo(state, actor, tempo)
    working = _perform(content, roster, working, actor.id, action, tempo, source)

    updated = working.by_id(actor_id)
    if updated is not None:
        working = working.replace_combatant(
            replace(updated, trace=_advanced_trace(updated.trace, tempo))
        )

    working = _check_outcome(content, roster, working, source)
    if working.is_over:
        return working

    working = _upkeep(content, roster, working, actor_id)
    working = _check_outcome(content, roster, working, source)
    if working.is_over:
        return working

    if tempo.breakthrough:
        # Размен сломан: тот, на ком он сломался, теряет ближайший ход.
        working = _off_balance(working, actor, _target_of(working, actor, action.target))
    return _advance(working, seed)


def _off_balance(state: BattleState, actor: Combatant, target: Combatant | None) -> BattleState:
    """Сбить с ритма того, на ком размен сломался: он пропустит ближайший ход.

    Одного, а не всю сторону: в бою четверых три разных тега отнимали бы у
    противника целый круг, и один игрок решал бы бой за весь отряд. Того, кому
    достался последний тег, - и достаточно (ADR 0021).
    """
    working = state.with_events(
        BattleEvent(kind=EventKind.BREAKTHROUGH, actor_id=actor.id, actor=actor.name)
    )
    shaken = target if target is not None else next(iter(working.foes_of(actor.id)), None)
    if shaken is None or not shaken.alive:
        return working
    current = working.by_id(shaken.id)
    if current is None or not current.alive:  # pragma: no cover - цель могла пасть
        return working
    return working.replace_combatant(replace(current, stunned=max(current.stunned, 1)))


def _advance(state: BattleState, seed: bytes) -> BattleState:
    """Передать очередь дальше, пропустив павших и ушедших."""
    if not state.order:  # pragma: no cover - бой без очереди уже кончен
        return state
    cursor = state.cursor
    round_number = state.round
    order = state.order
    for _ in range(len(order) + 1):
        cursor += 1
        if cursor >= len(order):
            round_number += 1
            order = _order_for(state.combatants, seed, round_number)
            cursor = 0
            if not order:  # pragma: no cover - исход проверен выше
                break
        nxt = state.by_id(order[cursor])
        if nxt is not None and nxt.alive:
            break
    return replace(state, cursor=cursor, round=round_number, order=order)


def _refusal(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
) -> BattleEvent | None:
    """Почему это действие не состоится. ``None`` - состоится.

    Спрашивается до всего остального и только про то, что игра отказывается
    делать вовсе. Промах отказом не является: он и есть результат хода.
    """
    if actor.stunned > 0:
        return None
    match action.kind:
        case ActionKind.SKILL | ActionKind.RACIAL:
            attempt = _attempt_skill(content, roster, state, actor, action)
            return attempt if isinstance(attempt, BattleEvent) else None
        case _:
            return None


def _tempo(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
) -> TurnTempo:
    """Намерения противной стороны, тег бойца и то, что из этого следует."""
    intents = {
        one.id: intent_of(state, one)
        for one in state.combatants
        if one.alive and one.id != actor.id
    }
    tag = _action_tag(content, roster, state, actor, action)
    # Объявленное самим ходящим: по нему считается сила его удара и то, можно ли
    # от удара увернуться. У живого игрока объявления нет.
    own = None if actor.live else intent_of(state, actor)
    if tag is None or actor.stunned > 0:
        return TurnTempo(intents=intents, own_intent=own)

    trace = actor.trace
    # Разгон и брешь - награда за выбор, а выбор делает тот, за кем стоит
    # персонаж. Порода бьёт одним и тем же тегом всегда: разгон от однообразия
    # был бы прибавкой, которую никто не заслужил, а брешь - наказанием за
    # оборону, которого игрок не мог бы избежать ничем. Своё намерение порода
    # объявляет и по нему получает брешь сама - это и есть её половина размена.
    if not actor.is_hero:
        return TurnTempo(intents=intents, streak=1, own_intent=own)
    return TurnTempo(
        intents=intents,
        tag=tag,
        streak=trace.streak + 1 if trace.last is tag else 1,
        breakthrough=trace.breaks_with(tag),
        own_intent=own,
    )


def _action_tag(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
) -> ActionTag | None:
    """След, который оставит это действие, или ``None``, когда следа нет."""
    match action.kind:
        case ActionKind.ATTACK:
            return ActionTag.PRESS
        case ActionKind.ITEM:
            return ActionTag.GUARD if action.item_id is not None else None
        case ActionKind.FLEE | ActionKind.FOCUS | ActionKind.YIELD:
            return None
        case ActionKind.SKILL | ActionKind.RACIAL:
            attempt = _attempt_skill(content, roster, state, actor, action)
            return None if isinstance(attempt, BattleEvent) else tag_of_skill(attempt[0])


def _announce_tempo(state: BattleState, actor: Combatant, tempo: TurnTempo) -> BattleState:
    working = state
    if tempo.momentum:
        working = working.with_events(
            BattleEvent(
                kind=EventKind.MOMENTUM,
                actor_id=actor.id,
                actor=actor.name,
                amount=tempo.streak,
            )
        )
    for one in working.foes_of(actor.id):
        if tempo.breached(one.id):
            working = working.with_events(
                BattleEvent(kind=EventKind.BREACH, target_id=one.id, target=one.name)
            )
    return working


def _advanced_trace(trace: Trace, tempo: TurnTempo) -> Trace:
    """Перелом тратит след, всё прочее его удлиняет."""
    if tempo.tag is None:
        return trace
    return Trace() if tempo.breakthrough else trace.push(tempo.tag)


def _perform(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor_id: int,
    action: BattleAction,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    actor = state.by_id(actor_id)
    if actor is None:  # pragma: no cover
        return state
    match action.kind:
        case ActionKind.ATTACK:
            return _basic_attack(content, roster, state, actor, action.target, tempo, source)
        case ActionKind.SKILL | ActionKind.RACIAL:
            return _use_skill(content, roster, state, actor, action, tempo, source)
        case ActionKind.ITEM:
            return _use_item(content, roster, state, actor, action)
        case ActionKind.FLEE:
            return _try_flee(content, roster, state, actor, source)
        case _:  # pragma: no cover - смену цели и сдачу разбирает ``act``
            return state


# --- кого бьём --------------------------------------------------------


def _target_of(state: BattleState, actor: Combatant, requested: int) -> Combatant | None:
    """Цель этого удара: названная в действии, потом выбранная, потом любая."""
    named = state.by_id(requested)
    if named is not None and named.alive and named.side != actor.side:
        return named
    return state.target_for(actor.id)


def _foes(
    state: BattleState, actor: Combatant, requested: int, *, aoe: bool
) -> tuple[Combatant, ...]:
    if aoe:
        return state.foes_of(actor.id)
    target = _target_of(state, actor, requested)
    return (target,) if target is not None else ()


# --- обычный удар -----------------------------------------------------


def _basic_attack(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    requested: int,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    target = _target_of(state, actor, requested)
    if target is None:  # pragma: no cover - бой без целей уже кончен
        return state.with_events(BattleEvent(kind=EventKind.NO_TARGET))

    if actor.is_hero:
        character = roster.get(actor.id)
        if character is None:  # pragma: no cover - герой всегда приходит с персонажем
            return state
        power = blow_roll(content, character, source, actor.effects) * BASIC_ATTACK_PERCENT / 100.0
        skill_name = "Атака"
    else:
        # За кого ходит движок, тот бьёт так, как объявил: натиск сильнее,
        # оборона слабее.
        intent = tempo.own_intent or intent_of(state, actor) or ActionTag.PRESS
        power = float(actor.enemy.damage if actor.enemy else 1) * INTENT_DAMAGE[intent]
        skill_name = ""

    struck, _ = _strike(
        content,
        roster,
        state,
        actor=actor,
        target=target,
        power=power,
        spec=None,
        skill_name=skill_name,
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
    """Обычный удар персонажа - единица, в процентах от которой считают умения."""
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
    """Границы одного удара - то, что игрок слышит вместо среднего."""
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


# --- умения -----------------------------------------------------------


def _resolve_skill(
    content: GameContent, character: Character, action: BattleAction
) -> Skill | None:
    """Умение за нажатым слотом, или ``None``, когда его там нет.

    Панель переживает содержимое: умение, выпавшее между выпусками, оставляет
    свой код в чьём-то слоте. Такой слот читается пустым, а не падает посреди
    боя (``Claude.md``, правило 8).
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
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
) -> tuple[Skill, int] | BattleEvent:
    """Умение и его цена, или событие, говорящее, почему им не воспользоваться."""
    character = roster.get(actor.id)
    if character is None:
        return BattleEvent(kind=EventKind.EMPTY_SLOT)

    skill = _resolve_skill(content, character, action)
    if skill is None:
        return BattleEvent(kind=EventKind.EMPTY_SLOT)

    # Умение просит оружие раньше, чем ресурс: платить за удар, который нечем
    # нанести, игрок не должен.
    if refusal := gear.skill_refusal(content, character, skill):
        return BattleEvent(kind=EventKind.WRONG_WEAPON, skill_name=skill.name, effect_name=refusal)

    cooldown = actor.cooldown_of(skill.code)
    if cooldown > 0:
        return BattleEvent(kind=EventKind.ON_COOLDOWN, skill_name=skill.name, turns=cooldown)

    modifiers = mods.collect_modifiers(content, character, actor.effects)
    cost = round(
        _skill_cost(skill, modifiers, free=actor.free_cast)
        * skill_rules.cost_factor(character, skill)
    )
    if cost > actor.resource:
        return BattleEvent(kind=EventKind.NOT_ENOUGH_RESOURCE, skill_name=skill.name, amount=cost)
    return skill, cost


def _use_skill(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    attempt = _attempt_skill(content, roster, state, actor, action)
    if isinstance(attempt, BattleEvent):
        return state.with_events(attempt)
    character = roster[actor.id]

    skill, cost = attempt
    rank = character.loadout.rank_of(skill.code)
    # Грань переписывает и силу, и само действие: что именно - объявлено в
    # содержимом и разобрано в ``domain/rules/edges.py``.
    edge = skill_rules.chosen_edge(character, skill)
    power = skill.power_at_rank(rank) * edge_rules.power_factor(edge)
    spec = edge_rules.applied(spec_for(skill.effect), edge)
    cooldown = recharged(edge_rules.cooldown_of(skill.cooldown, edge), spec, power)
    reduction = mods.collect_modifiers(content, character, actor.effects).get(
        "cooldown_reduction_percent", 0.0
    )
    if cooldown and reduction:
        cooldown = max(1, round(cooldown * max(0.0, 1.0 - reduction / 100.0)))

    spent = replace(actor, resource=actor.resource - cost, free_cast=False)
    if cooldown:
        # +1, потому что откаты тикают в конце этого же хода, и умение остаётся
        # недоступным ровно ``cooldown`` дальнейших ходов.
        spent = spent.with_cooldown(skill.code, cooldown + 1)
    working = state.replace_combatant(spent)
    return _apply_spec(
        content, roster, working, spent.id, skill, spec, power, action.target, tempo, source
    )


def _skill_cost(skill: Skill, modifiers: Mapping[str, float], *, free: bool) -> int:
    if free:
        return 0
    reduction = 1.0 - modifiers.get("cost_reduction_percent", 0.0) / 100.0
    return max(0, round(skill.cost * max(0.1, reduction)))


def _apply_spec(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor_id: int,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    requested: int,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    working = state
    actor = working.by_id(actor_id)
    character = roster.get(actor_id)
    if actor is None or character is None:  # pragma: no cover
        return working
    modifiers = mods.collect_modifiers(content, character, actor.effects)
    rank_scale = 1.0 + skill.rank_step * (character.loadout.rank_of(skill.code) - 1)
    own_dice = skill.dice

    if spec.special == "avoid_combat":
        # Договориться можно с тем, кто способен на разговор. С волком нельзя, и
        # умение говорит об этом до нажатия.
        if any(one.race_kind not in REASONING_KINDS for one in working.foes_of(actor_id)):
            return working.with_events(
                BattleEvent(kind=EventKind.FLEE_FAILED, skill_name=skill.name)
            )
        stats = derived_stats(content, character, actor.effects)
        if source.uniform(0, 100) < power + stats.crit_chance:
            return replace(working, outcome=BattleOutcome.AVOIDED).with_events(
                BattleEvent(kind=EventKind.AVOIDED, skill_name=skill.name)
            )
        return working.with_events(BattleEvent(kind=EventKind.FLEE_FAILED, skill_name=skill.name))

    # Кого удар достал. Пусто - умение промахнулось мимо всех, и вешать на них
    # нечего: всё, что умение вешает на цель, идёт следом за попаданием.
    landed: list[int] = []
    blow = 0.0
    if spec.category is EffectCategory.DAMAGE:
        targets = _foes(working, actor, requested, aoe=spec.aoe)
        falloff = 1.0
        for target in targets:
            for _ in range(spec.hits):
                current = working.by_id(target.id)
                if current is None or not current.alive:
                    break
                striker = working.by_id(actor_id)
                if striker is None:  # pragma: no cover
                    break
                # Каждый удар - свой бросок: два удара подряд одним и тем же
                # оружием не обязаны совпасть.
                blow = blow_roll(content, character, source, striker.effects, skill.scaling)
                working, hit = _strike(
                    content,
                    roster,
                    working,
                    actor=striker,
                    target=current,
                    power=blow * power / 100.0 * spec.damage_scale * falloff
                    + (own_dice.roll(source) * rank_scale if own_dice is not None else 0.0),
                    spec=spec,
                    skill_name=skill.name,
                    tempo=tempo,
                    source=source,
                )
                if hit and target.id not in landed:
                    landed.append(target.id)
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
        primary = _target_of(working, actor, requested)
        working = _splash(
            content,
            roster,
            working,
            actor_id=actor_id,
            spec=spec,
            # Задевается сосед, а не сама цель: «размах» на то и размах.
            spared=landed[0] if landed else (primary.id if primary is not None else 0),
            blow=blow,
            power=power,
            skill_name=skill.name,
            tempo=tempo,
            source=source,
        )

    if spec.category is EffectCategory.HEAL:
        # Лечение и щит - проценты от максимума здоровья, а не от удара: здоровье
        # растёт впятеро быстрее удара, и лечение, оценённое в ударах, к сороковому
        # уровню не стоило бы ничего.
        healer = working.by_id(actor_id)
        if healer is not None:
            amount = round(healer.max_health * power / 100.0)
            amount = round(amount * mods.percent(modifiers, "healing_done_percent"))
            if spec.special == "heal_over_time":
                working = _mending(
                    working, actor_id, skill=skill, spec=spec, per_turn=float(amount)
                )
            else:
                working = _heal(working, actor_id, amount, modifiers, skill_name=skill.name)

    if spec.category is EffectCategory.SHIELD:
        holder = working.by_id(actor_id)
        if holder is not None:
            shield = round(holder.max_health * power / 100.0)
            working = _shielded(working, actor_id, skill=skill, spec=spec, amount=shield)

    if spec.bonus_heal:
        holder = working.by_id(actor_id)
        if holder is not None:
            extra = round(holder.max_health * spec.bonus_heal / 100.0)
            working = _heal(working, actor_id, extra, modifiers, skill_name=skill.name)

    if spec.bonus_shield:
        holder = working.by_id(actor_id)
        if holder is not None:
            extra = round(holder.max_health * spec.bonus_shield / 100.0)
            working = _shielded(working, actor_id, skill=skill, spec=spec, amount=extra)

    if spec.cleanse_count:
        holder = working.by_id(actor_id)
        if holder is not None:
            before = len(holder.effects.penalties())
            cleansed = holder.effects.cleanse(cleansed_count(spec, power))
            removed = before - len(cleansed.penalties())
            working = working.replace_combatant(replace(holder, effects=cleansed))
            if removed:
                working = working.with_events(
                    BattleEvent(
                        kind=EventKind.CLEANSED,
                        actor_id=actor_id,
                        actor=holder.name,
                        amount=removed,
                        skill_name=skill.name,
                    )
                )

    if spec.self_damage_taken:
        # Замах, который открывает бьющего: цена размашистого удара - этот же
        # ход, прожитый без защиты.
        holder = working.by_id(actor_id)
        if holder is not None:
            opened = ActiveEffect(
                id=f"{skill.code}_opened",
                name=skill.name,
                modifiers={"damage_taken_percent": spec.self_damage_taken},
                turns_left=1,
                source=skill.code,
                beneficial=False,
            )
            working = working.replace_combatant(
                replace(holder, effects=holder.effects.apply(opened))
            )

    working = _apply_modifier_bundles(
        content,
        roster,
        working,
        actor_id,
        skill,
        spec,
        power,
        requested,
        landed=tuple(landed) if spec.category is EffectCategory.DAMAGE else None,
    )
    return _apply_special(working, actor_id, skill, spec, power, requested)


def _heal(
    state: BattleState,
    combatant_id: int,
    amount: int,
    modifiers: Mapping[str, float],
    *,
    skill_name: str = "",
) -> BattleState:
    """Вернуть бойцу здоровье и сказать об этом.

    Через одну дверь проходит всё лечение, которое боец получает, - потому и
    ``healing_taken_percent`` считается здесь.
    """
    one = state.by_id(combatant_id)
    if one is None:  # pragma: no cover
        return state
    healed = round(amount * mods.percent(modifiers, "healing_taken_percent"))
    updated, restored = one.healed(max(0, healed))
    if not restored:
        return state
    return state.replace_combatant(updated).with_events(
        BattleEvent(
            kind=EventKind.HEAL,
            actor_id=updated.id,
            actor=updated.name,
            amount=restored,
            skill_name=skill_name,
        )
    )


def _mending(
    state: BattleState, combatant_id: int, *, skill: Skill, spec: EffectSpec, per_turn: float
) -> BattleState:
    """Лечение, которое приходит каждый ход, а не сейчас."""
    one = state.by_id(combatant_id)
    if one is None:  # pragma: no cover
        return state
    effect = ActiveEffect(
        id=f"{skill.code}_mend",
        name=skill.name,
        modifiers={MEND_PER_TURN: per_turn},
        turns_left=max(1, spec.dot_turns),
        source=skill.code,
        beneficial=True,
    )
    updated = replace(one, effects=one.effects.apply(effect))
    return state.replace_combatant(updated).with_events(
        BattleEvent(
            kind=EventKind.EFFECT_APPLIED,
            actor_id=updated.id,
            actor=updated.name,
            effect_name=skill.name,
            turns=max(1, spec.dot_turns),
        )
    )


def _shielded(
    state: BattleState, combatant_id: int, *, skill: Skill, spec: EffectSpec, amount: int
) -> BattleState:
    """Щит и срок, который его держит."""
    one = state.by_id(combatant_id)
    if one is None or amount <= 0:
        return state
    turns = spec.shield_turns or DEFAULT_SHIELD_TURNS
    effect = ActiveEffect(
        id=f"{skill.code}_shield",
        name=skill.name,
        modifiers={SHIELD_HELD: float(amount)},
        turns_left=turns,
        source=skill.code,
        beneficial=True,
    )
    updated = replace(one, shield=one.shield + amount, effects=one.effects.apply(effect))
    return state.replace_combatant(updated).with_events(
        BattleEvent(
            kind=EventKind.SHIELD,
            actor_id=updated.id,
            actor=updated.name,
            amount=amount,
            skill_name=skill.name,
        )
    )


def _bleeding(
    state: BattleState,
    *,
    spec: EffectSpec,
    skill: Skill,
    blow: float,
    power: float,
    struck: tuple[int, ...],
    modifiers: Mapping[str, float],
) -> BattleState:
    """Оставить на раненых то, что будет их точить каждый ход."""
    if not spec.dot_turns or spec.category is not EffectCategory.DAMAGE:
        return state
    per_turn = max(
        1.0,
        blow * power / 100.0 * BLEED_SHARE * mods.percent(modifiers, "dot_damage_percent"),
    )
    working = state
    for combatant_id in struck:
        target = working.by_id(combatant_id)
        if target is None or not target.alive:
            continue
        effect = ActiveEffect(
            id=f"{skill.code}_dot",
            name=skill.name,
            modifiers={BLEED_PER_TURN: per_turn},
            turns_left=spec.dot_turns,
            source=skill.code,
            beneficial=False,
        )
        working = working.replace_combatant(replace(target, effects=target.effects.apply(effect)))
    return working


def _splash(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    *,
    actor_id: int,
    spec: EffectSpec,
    spared: int,
    blow: float,
    power: float,
    skill_name: str,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    """Вторая цель, которую задевает одноцелевой удар. Только от грани."""
    if not spec.splash or spec.aoe:
        return state
    actor = state.by_id(actor_id)
    if actor is None:  # pragma: no cover
        return state
    neighbour = next((one for one in state.foes_of(actor_id) if one.id != spared), None)
    if neighbour is None:
        return state
    struck, _ = _strike(
        content,
        roster,
        state,
        actor=actor,
        target=neighbour,
        power=blow * power / 100.0 * spec.damage_scale * spec.splash,
        spec=spec,
        skill_name=skill_name,
        tempo=tempo,
        source=source,
    )
    return struck


def _apply_modifier_bundles(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor_id: int,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    requested: int,
    *,
    landed: tuple[int, ...] | None = None,
) -> BattleState:
    """Усиления себе и помехи цели."""
    working = state
    actor = working.by_id(actor_id)
    if actor is None:  # pragma: no cover
        return working

    if spec.self_modifiers and spec.duration:
        effect = ActiveEffect(
            id=skill.code,
            name=skill.name,
            modifiers={item.key: item.amount(power) for item in spec.self_modifiers},
            turns_left=spec.duration,
            source=skill.code,
            beneficial=True,
        )
        working = working.replace_combatant(replace(actor, effects=actor.effects.apply(effect)))
        working = _repooled(content, roster, working, actor_id)
        working = working.with_events(
            BattleEvent(
                kind=EventKind.EFFECT_APPLIED,
                actor_id=actor_id,
                actor=actor.name,
                effect_name=skill.name,
                turns=spec.duration,
            )
        )

    if spec.target_modifiers and spec.duration:
        if landed is None:
            targets = _foes(working, actor, requested, aoe=spec.aoe)
        else:
            targets = tuple(
                one for target_id in landed if (one := working.by_id(target_id)) is not None
            )
        for target in targets:
            effect = ActiveEffect(
                id=skill.code,
                name=skill.name,
                modifiers={item.key: item.amount(power) for item in spec.target_modifiers},
                turns_left=spec.duration,
                source=skill.code,
                beneficial=False,
            )
            working = working.replace_combatant(
                replace(target, effects=target.effects.apply(effect))
            )
            working = working.with_events(
                BattleEvent(
                    kind=EventKind.EFFECT_APPLIED,
                    target_id=target.id,
                    target=target.name,
                    effect_name=skill.name,
                    turns=spec.duration,
                )
            )
    return working


def _repooled(
    content: GameContent, roster: Mapping[int, Character], state: BattleState, combatant_id: int
) -> BattleState:
    """Пересчитать запас здоровья под теми усилениями, что сейчас на бойце.

    Медвежий облик обещает «здоровье выше на 40 процентов»: растёт потолок, а
    вместе с ним и то, что в него влезает. Здоровье при этом не дарится.
    """
    one = state.by_id(combatant_id)
    character = roster.get(combatant_id)
    if one is None or character is None:
        return state
    stats = derived_stats(content, character, one.effects)
    if stats.max_health == one.max_health:
        return state
    gained = max(0, stats.max_health - one.max_health)
    return state.replace_combatant(
        replace(
            one,
            max_health=stats.max_health,
            health=min(stats.max_health, one.health + gained),
        )
    )


def _apply_special(
    state: BattleState,
    actor_id: int,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    requested: int = 0,
) -> BattleState:
    working = state
    actor = working.by_id(actor_id)
    if actor is None:  # pragma: no cover
        return working
    match spec.special:
        case "evade_next":
            working = working.replace_combatant(
                replace(actor, evade_charges=actor.evade_charges + 1)
            )
        case "free_cast":
            working = working.replace_combatant(replace(actor, free_cast=True))
        case "cooldown_reset":
            working = working.replace_combatant(replace(actor, cooldowns={}))
        case "full_heal":
            healed, restored = actor.healed(actor.max_health)
            working = working.replace_combatant(healed).with_events(
                BattleEvent(
                    kind=EventKind.HEAL,
                    actor_id=healed.id,
                    actor=healed.name,
                    amount=restored,
                    skill_name=skill.name,
                )
            )
        case "steal_gold":
            # Доля того, что несёт обворованный, а не написанное число. Красть
            # можно у породы: кошелёк другого игрока лежит в базе, и трогать его
            # ударом в бою движок не станет (``domain/rules/pvp.py``).
            target = _target_of(working, actor, requested)
            if target is not None and target.enemy is not None:
                stolen = max(1, round(target.enemy.gold * power / 100.0))
                working = replace(working, gold=working.gold + stolen)
    return working


# --- удар -------------------------------------------------------------


def _accuracy_of(content: GameContent, roster: Mapping[int, Character], one: Combatant) -> float:
    if one.is_hero and (character := roster.get(one.id)) is not None:
        return derived_stats(content, character, one.effects).accuracy
    return MONSTER_ACCURACY * mods.percent(one.effects.modifiers(), "accuracy_percent")


def _dodge_of(content: GameContent, roster: Mapping[int, Character], one: Combatant) -> float:
    if one.is_hero and (character := roster.get(one.id)) is not None:
        return derived_stats(content, character, one.effects).dodge
    # У породы уклонения нет: её защита - броня и здоровье.
    return 0.0


def _armor_of(content: GameContent, roster: Mapping[int, Character], one: Combatant) -> float:
    if one.is_hero and (character := roster.get(one.id)) is not None:
        return float(derived_stats(content, character, one.effects).armor)
    base = float(one.enemy.armor if one.enemy is not None else 0)
    return base * mods.percent(one.effects.modifiers(), "armor_percent")


def _modifiers_of(
    content: GameContent, roster: Mapping[int, Character], one: Combatant
) -> Mapping[str, float]:
    """Все прибавки, действующие на бойца. У породы это только её эффекты."""
    if one.is_hero and (character := roster.get(one.id)) is not None:
        return mods.collect_modifiers(content, character, one.effects)
    return one.effects.modifiers()


def _stats_of(
    content: GameContent, roster: Mapping[int, Character], one: Combatant
) -> DerivedStats | None:
    if one.is_hero and (character := roster.get(one.id)) is not None:
        return derived_stats(content, character, one.effects)
    return None


def element_of(attacker: Combatant, spec: EffectSpec | None) -> DamageElement:
    """Стихия удара: названная умением, иначе своя собственная."""
    if spec is not None:
        for tag in spec.tags:
            if (element := TAG_ELEMENTS.get(tag)) is not None:
                return element
    return attacker.element


def _is_magic(spec: EffectSpec | None) -> bool:
    """Чары это или рука. Обычный удар - всегда рука."""
    return spec is not None and bool(MAGIC_TAGS & set(spec.tags))


def situational_damage(
    modifiers: Mapping[str, float],
    *,
    spec: EffectSpec | None,
    target: Combatant,
    target_health_ratio: float,
    attacker_health_ratio: float,
    round_number: int,
) -> float:
    """Множитель, который дают прибавки, смотрящие по сторонам.

    Складываются они в проценты и лишь потом становятся множителем: порядок
    источников не должен ничего решать (``rules/modifiers``).
    """
    total = 0.0
    kind = "magic" if _is_magic(spec) else "physical"
    total += modifiers.get(f"{kind}_damage_percent", 0.0)
    total += modifiers.get(
        "aoe_damage_percent" if spec is not None and spec.aoe else "single_target_damage_percent",
        0.0,
    )
    if round_number <= 1:
        total += modifiers.get("first_turn_damage_percent", 0.0)
    if attacker_health_ratio <= LOW_HEALTH_THRESHOLD:
        total += modifiers.get("low_health_damage_percent", 0.0)
    if target_health_ratio <= WOUNDED_RATIO:
        total += modifiers.get("wounded_target_damage_percent", 0.0)
    if target.rank.value in ELITE_RANKS:
        total += modifiers.get("elite_damage_percent", 0.0)
    if (key := KIND_DAMAGE_KEYS.get(target.race_kind)) is not None:
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


def incoming_damage_factor(modifiers: Mapping[str, float], element: DamageElement) -> float:
    """Что сопротивление оставляет от чужого удара этой стихии."""
    key = RESIST_KEYS.get(element, "resist_physical_percent")
    return max(0.0, 1.0 - modifiers.get(key, 0.0) / 100.0)


def _strike(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    *,
    actor: Combatant,
    target: Combatant,
    power: float,
    spec: EffectSpec | None,
    skill_name: str,
    tempo: TurnTempo,
    source: random.Random,
    answering: bool = False,
) -> tuple[BattleState, bool]:
    """Один удар, кто бы ни бил и кого бы ни били. Второй член - попал ли он.

    Попал или нет решает не только урон: всё, что умение вешает на цель -
    кровотечение, помеха, оглушение, - идёт следом за попаданием.
    """
    working = state
    attacker_mods = _modifiers_of(content, roster, actor)
    target_mods = _modifiers_of(content, roster, target)

    # Уклонение, купленное умением: заряд тратится на первый же удар по цели.
    if target.evade_charges > 0:
        working = working.replace_combatant(replace(target, evade_charges=target.evade_charges - 1))
        return (
            working.with_events(
                BattleEvent(
                    kind=EventKind.DODGE,
                    actor_id=actor.id,
                    actor=actor.name,
                    target_id=target.id,
                    target=target.name,
                )
            ),
            False,
        )

    accuracy_penalty = 15.0 if spec is not None and "inaccurate" in spec.tags else 0.0
    gap = target.level - actor.level
    hit_chance = min(
        MAX_HIT_CHANCE,
        max(
            MIN_HIT_CHANCE,
            _accuracy_of(content, roster, actor)
            - _dodge_of(content, roster, target)
            - gap * ACCURACY_PER_LEVEL_GAP
            - accuracy_penalty,
        ),
    )
    # Удар, объявленный точностью, не уклоняется - его принимают или отвечают.
    dodgeable = tempo.own_intent is not ActionTag.PRECISION
    if dodgeable and source.uniform(0, 100) > hit_chance:
        # По герою - «уклонился», по породе - «промах». Одно и то же число, но
        # игрок слышит в нём своё уклонение, а не чужую неловкость: за первым
        # стоит его ловкость, и он на неё тратил очки.
        return (
            working.with_events(
                BattleEvent(
                    kind=EventKind.DODGE if target.is_hero else EventKind.MISS,
                    actor_id=actor.id,
                    actor=actor.name,
                    target_id=target.id,
                    target=target.name,
                    skill_name=skill_name,
                )
            ),
            False,
        )

    raw = power
    raw *= mods.percent(attacker_mods, "damage_percent")
    raw *= situational_damage(
        attacker_mods,
        spec=spec,
        target=target,
        target_health_ratio=target.health / max(1, target.max_health),
        attacker_health_ratio=actor.health / max(1, actor.max_health),
        round_number=state.round,
    )
    raw *= tempo.damage_scale if not answering else 1.0
    if spec is not None and spec.execute_scaling:
        missing = 1.0 - target.health / max(1, target.max_health)
        raw *= 1.0 + missing * spec.execute_scaling

    # Что цель получает сверх обычного: и её собственная уязвимость, и та,
    # которую на неё повесили.
    raw *= mods.percent(target_mods, "damage_taken_percent")

    pierce = spec.pierce if spec is not None else 0.0
    # Брешь выносит броню из счёта целиком; объявленная оборона её удваивает, а
    # тот, кто замахнулся для натиска, уже открылся.
    effective_armor = (
        _armor_of(content, roster, target) * (1.0 - pierce) * tempo.armor_scale(target.id)
    )
    raw *= armor_factor(effective_armor, target.level)
    raw *= incoming_damage_factor(target_mods, element_of(actor, spec))
    # Удар того, в ком пробили брешь, доходит вполсилы: его застали на замахе, и
    # платит он этим ходом, а не следующим.
    if actor.breached:
        raw *= BREACH_ANSWER_SCALE

    stats = _stats_of(content, roster, actor)
    guaranteed = spec is not None and spec.guaranteed_crit
    crit_chance = (stats.crit_chance if stats is not None else 0.0) + (
        spec.crit_bonus if spec is not None else 0.0
    )
    is_crit = guaranteed or source.uniform(0, 100) < crit_chance
    if is_crit:
        raw *= (stats.crit_damage if stats is not None else 150.0) / 100.0

    amount = max(1, round(raw))
    hurt, lost = target.damaged(amount)
    if tempo.breached(target.id):
        hurt = replace(hurt, breached=True)
    # Пока держится «Последний рубеж», боец не падает.
    if not hurt.alive and target.effects.modifiers().get(UNDYING, 0.0) > 0:
        hurt = replace(hurt, health=1)
    working = working.replace_combatant(hurt)
    working = working.with_events(
        BattleEvent(
            kind=EventKind.CRIT if is_crit else EventKind.DAMAGE,
            actor_id=actor.id,
            actor=actor.name,
            target_id=hurt.id,
            target=hurt.name,
            amount=amount,
            skill_name=skill_name,
        )
    )

    if spec is not None and spec.stun_turns and hurt.alive:
        working = working.replace_combatant(replace(hurt, stunned=spec.stun_turns))
        working = working.with_events(
            BattleEvent(
                kind=EventKind.STUNNED,
                target_id=hurt.id,
                target=hurt.name,
                turns=spec.stun_turns,
            )
        )

    lifesteal = spec.lifesteal if spec is not None else 0.0
    lifesteal += attacker_mods.get("lifesteal_percent", 0.0) / 100.0
    if lifesteal:
        working = _heal(working, actor.id, round(amount * lifesteal), attacker_mods)

    if not hurt.alive:
        return (
            working.with_events(
                BattleEvent(kind=EventKind.DEFEATED, target_id=hurt.id, target=hurt.name)
            ),
            True,
        )

    if not answering and lost:
        working = _answered(
            content,
            roster,
            working,
            attacker_id=actor.id,
            target_id=hurt.id,
            taken=amount,
            tempo=tempo,
            source=source,
        )
    return working, True


def _answered(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    *,
    attacker_id: int,
    target_id: int,
    taken: int,
    tempo: TurnTempo,
    source: random.Random,
) -> BattleState:
    """Чем цель отвечает тому, кто по ней только что попал.

    Два обещания, за которыми долго ничего не стояло: «вы отвечаете на каждый
    удар по вам» у выпада воина и «часть полученного урона возвращается
    обидчику» у постоянного умения паладина. Ответ ответа не вызывает: размен
    двух отражений не кончился бы никогда.
    """
    working = state
    defender = working.by_id(target_id)
    attacker = working.by_id(attacker_id)
    if defender is None or attacker is None or not defender.alive or not attacker.alive:
        return working

    defender_mods = _modifiers_of(content, roster, defender)
    reflect = defender_mods.get("reflect_percent", 0.0)
    if reflect > 0:
        amount = max(1, round(taken * reflect / 100.0))
        hurt, _ = attacker.damaged(amount)
        working = working.replace_combatant(hurt).with_events(
            BattleEvent(
                kind=EventKind.DAMAGE,
                actor_id=defender.id,
                actor=defender.name,
                target_id=hurt.id,
                target=hurt.name,
                amount=amount,
            )
        )
        if not hurt.alive:
            return working.with_events(
                BattleEvent(kind=EventKind.DEFEATED, target_id=hurt.id, target=hurt.name)
            )
        attacker = hurt

    counter = defender.effects.modifiers().get(COUNTER, 0.0)
    character = roster.get(defender.id)
    if counter > 0 and character is not None:
        # Именем отвечает то умение, которое отвечать и научило.
        named = next(
            (effect.name for effect in defender.effects if COUNTER in effect.modifiers),
            "",
        )
        working, _ = _strike(
            content,
            roster,
            working,
            actor=defender,
            target=attacker,
            power=blow_roll(content, character, source, defender.effects) * counter / 100.0,
            spec=None,
            skill_name=named,
            tempo=tempo,
            source=source,
            answering=True,
        )
    return working


# --- расходники и бегство ---------------------------------------------


def _use_item(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    action: BattleAction,
) -> BattleState:
    if action.item_id is None or not content.has_item(action.item_id):
        return state
    item = content.item(action.item_id)
    if item.effect is None:
        return state
    working = state
    modifiers = _modifiers_of(content, roster, actor)
    match item.effect.kind:
        # Плоских величин здесь тоже нет: зелье на 40 здоровья к двадцатому
        # уровню не стоит ничего (ADR 0007).
        case "heal_percent":
            amount = round(actor.max_health * item.effect.power / 100.0)
            working = _heal(working, actor.id, amount, modifiers)
        case "restore_resource_percent":
            amount = round(actor.max_resource * item.effect.power / 100.0)
            restored = replace(actor, resource=min(actor.max_resource, actor.resource + amount))
            working = working.replace_combatant(restored).with_events(
                BattleEvent(
                    kind=EventKind.RESOURCE,
                    actor_id=restored.id,
                    actor=restored.name,
                    amount=amount,
                )
            )
        case "cleanse":
            cleansed = actor.effects.cleanse(round(item.effect.power))
            working = working.replace_combatant(replace(actor, effects=cleansed))
            working = working.with_events(
                BattleEvent(kind=EventKind.CLEANSED, actor_id=actor.id, actor=actor.name, amount=1)
            )
        case "buff_damage_percent":
            effect = ActiveEffect(
                id=f"item:{item.id}",
                name=item.name,
                modifiers={"damage_percent": item.effect.power},
                turns_left=max(1, item.effect.turns),
            )
            working = working.replace_combatant(replace(actor, effects=actor.effects.apply(effect)))
            working = working.with_events(
                BattleEvent(
                    kind=EventKind.EFFECT_APPLIED,
                    actor_id=actor.id,
                    actor=actor.name,
                    effect_name=item.name,
                    turns=max(1, item.effect.turns),
                )
            )
    return working


def _try_flee(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    source: random.Random,
) -> BattleState:
    """Уйти с поля. Удалось - боец вне боя; не удалось - ход потрачен.

    Бежать из поединка можно так же, как из боя со стаей: чужое согласие для
    этого не нужно, а цена одна - ход и то, что противник за него успеет.
    """
    modifiers = _modifiers_of(content, roster, actor)
    chance = BASE_FLEE_CHANCE + modifiers.get("flee_chance_percent", 0.0)
    if source.uniform(0, 100) < chance:
        return state.replace_combatant(replace(actor, left=True)).with_events(
            BattleEvent(kind=EventKind.FLED, actor_id=actor.id, actor=actor.name)
        )
    return state.with_events(
        BattleEvent(kind=EventKind.FLEE_FAILED, actor_id=actor.id, actor=actor.name)
    )


def _leave(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    seed: bytes,
    *,
    yielded: bool,
) -> BattleState:
    """Выйти из боя, отдав его. Единственная дверь из брошенного поединка.

    Таймеров в игре нет, и ждать чужого нажатия можно бесконечно: тот, кто ждать
    больше не хочет, отдаёт бой - и это засчитывается противнику, а не отменяет
    случившееся (ADR 0021).
    """
    working = state.replace_combatant(replace(actor, left=True)).with_events(
        BattleEvent(
            kind=EventKind.YIELDED if yielded else EventKind.FLED,
            actor_id=actor.id,
            actor=actor.name,
        )
    )
    working = _check_outcome(content, roster, working, _turn_source(state, seed))
    if working.is_over:
        return working
    working = _advance(working, seed)
    return _drive(content, roster, working, seed)


# --- содержание -------------------------------------------------------


def _upkeep(
    content: GameContent, roster: Mapping[int, Character], state: BattleState, combatant_id: int
) -> BattleState:
    """Что случается с бойцом в конце его собственного хода.

    Всё, что меряется ходами, - откаты, сроки эффектов, кровотечение, лечение по
    ходам, восстановление ресурса, - тикает здесь и только здесь. Своими ходами,
    а не чужими: в бою четверых «три хода» иначе значило бы разное для каждого.
    """
    one = state.by_id(combatant_id)
    if one is None or not one.alive:
        return state

    modifiers = _modifiers_of(content, roster, one)
    # Брешь стоила ему одного удара - того, который он только что нанёс.
    updated = replace(one.tick_cooldowns(), breached=False)
    # Лечение по ходам платит до того, как срок укоротится: умение, обещавшее
    # три хода, лечит три раза, а не два.
    mending = round(updated.effects.modifiers().get(MEND_PER_TURN, 0.0))
    bleeding = round(updated.effects.modifiers().get(BLEED_PER_TURN, 0.0))
    updated = replace(updated, effects=updated.effects.tick())

    regen_percent = modifiers.get("regen_per_turn_percent", 0.0)
    if regen_percent:
        updated, _ = updated.healed(round(updated.max_health * regen_percent / 100.0))
    if updated.max_resource:
        stats = _stats_of(content, roster, updated)
        regen = round(stats.resource_regen) if stats is not None else 0
        updated = replace(updated, resource=min(updated.max_resource, updated.resource + regen))
    # Щит стоит ровно столько, сколько его держат: источник ушёл - ушёл и он.
    held = round(updated.effects.modifiers().get(SHIELD_HELD, 0.0))
    updated = replace(updated, shield=min(updated.shield, max(0, held)))

    working = state.replace_combatant(updated)
    working = _repooled(content, roster, working, combatant_id)
    if mending > 0:
        working = _heal(working, combatant_id, mending, modifiers)
    if bleeding > 0:
        working = spend_bleeding(working, combatant_id, bleeding)
    return working


def spend_bleeding(state: BattleState, combatant_id: int, amount: int | None = None) -> BattleState:
    """Кровотечение и горение платят по счёту - раз в ход, до конца срока.

    ``amount`` не назван - берётся то, что висит на бойце сейчас.
    """
    one = state.by_id(combatant_id)
    if one is None or not one.alive:
        return state
    if amount is None:
        amount = round(one.effects.modifiers().get(BLEED_PER_TURN, 0.0))
    if amount <= 0:
        return state
    hurt = replace(one, health=max(0, one.health - amount))
    working = state.replace_combatant(hurt).with_events(
        BattleEvent(kind=EventKind.DAMAGE, target_id=hurt.id, target=hurt.name, amount=amount)
    )
    if not hurt.alive:
        working = working.with_events(
            BattleEvent(kind=EventKind.DEFEATED, target_id=hurt.id, target=hurt.name)
        )
    return working


# --- исход ------------------------------------------------------------


def _check_outcome(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    source: random.Random | None = None,
) -> BattleState:
    if state.is_over:
        return state
    standing = {side: state.living(side) for side in (ATTACKERS, DEFENDERS)}
    if standing[ATTACKERS] and standing[DEFENDERS]:
        return state

    if not standing[ATTACKERS] and not standing[DEFENDERS]:
        # Оба поля пусты: последний удар свалил обоих. Ничья, платить некому.
        return replace(state, outcome=BattleOutcome.DECIDED, winner=-1)

    winner = ATTACKERS if standing[ATTACKERS] else DEFENDERS
    loser = DEFENDERS if winner == ATTACKERS else ATTACKERS
    # Сторона, которая ушла с поля целиком, боя не проиграла - она из него
    # вышла: платы за это победителю нет.
    walked_out = all(one.left for one in state.combatants if one.side == loser)
    outcome = BattleOutcome.FLED if walked_out else BattleOutcome.DECIDED
    settled = replace(state, outcome=outcome, winner=winner)
    if outcome is BattleOutcome.FLED:
        return settled
    return _spoils(content, roster, settled, winner, loser, source)


def _spoils(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    winner: int,
    loser: int,
    source: random.Random | None,
) -> BattleState:
    """Что победившая сторона забирает с побеждённой стаи.

    Платит порода, а не игрок: за поединок расплачиваются кошельки, и считает их
    ``domain/rules/pvp.py``. Стая делит бой целиком - и здоровье, и урон, и
    плату: трое волков стоили полутора боёв по времени и платили как три
    (ADR 0019).
    """
    fallen = tuple(one for one in state.combatants if one.side == loser and one.enemy is not None)
    if not fallen:
        return state

    victors = state.heroes(winner)
    level = max((one.level for one in victors), default=1)
    share = group_scale(len(fallen))
    experience = round(
        share
        * sum(
            experience_reward(enemy_level=one.enemy.level, character_level=level)
            * RANK_FACTORS[one.enemy.rank].experience
            for one in fallen
            if one.enemy is not None
        )
    )
    gold = sum(one.enemy.gold for one in fallen if one.enemy is not None)
    loot = tuple(item for one in fallen for item in (one.enemy.loot if one.enemy else ()))

    if source is not None:
        # Снаряжение падает сверх сырья и только с побеждённого. Прибавки к
        # находке берутся лучшие из тех, что есть у победителей: добычу делят на
        # всех, и искал её тот, кто умеет искать.
        found = [_modifiers_of(content, roster, one) for one in victors]
        drop_bonus = max((bundle.get("drop_rate_percent", 0.0) for bundle in found), default=0.0)
        rarity_bonus = max((bundle.get("rarity_percent", 0.0) for bundle in found), default=0.0)
        loot = (
            *loot,
            *(
                dropped
                for one in fallen
                if one.enemy is not None
                for dropped in (
                    item_procgen.roll_drop(
                        content,
                        source,
                        level=one.enemy.level,
                        rank=one.enemy.rank,
                        drop_bonus=drop_bonus,
                        rarity_bonus=rarity_bonus,
                    ),
                )
                if dropped is not None
            ),
        )

    return replace(state, experience=experience, gold=state.gold + gold, loot=loot)


# --- за кого ходит движок ---------------------------------------------


def _chosen_by_engine(
    content: GameContent,
    roster: Mapping[int, Character],
    state: BattleState,
    actor: Combatant,
    seed: bytes,
) -> BattleAction:
    """Ход того, за кем не стоит живой игрок.

    Порода бьёт тем, чем объявила. Персонаж под управлением движка - слепок
    противника на арене - дерётся своими умениями: он и есть тот игрок, только
    решает за него движок. Раньше слепок бил выдуманным числом, одинаковым для
    воина и мага одного уровня (ADR 0021).
    """
    target = _weakest_foe(state, actor)
    target_id = target.id if target is not None else 0
    if not actor.is_hero:
        return BattleAction(kind=ActionKind.ATTACK, target=target_id)

    source = rng(derive(seed, "engine", state.round, actor.id))
    character = roster.get(actor.id)
    if character is not None:
        ready = [
            slot
            for slot in range(len(character.loadout.actives))
            if not isinstance(
                _attempt_skill(
                    content,
                    roster,
                    state,
                    actor,
                    BattleAction(kind=ActionKind.SKILL, slot=slot, target=target_id),
                ),
                BattleEvent,
            )
        ]
        if ready and source.uniform(0, 100) < ENGINE_SKILL_CHANCE:
            return BattleAction(kind=ActionKind.SKILL, slot=source.choice(ready), target=target_id)
    return BattleAction(kind=ActionKind.ATTACK, target=target_id)


#: Как часто движок берётся за умение, когда ходит за персонажем. Больше
#: половины ходов - обычный удар: так дерётся тот, кто дерётся не глядя.
ENGINE_SKILL_CHANCE = 40.0


def _weakest_foe(state: BattleState, actor: Combatant) -> Combatant | None:
    """Кого движок бьёт: того, кому осталось меньше всех.

    Не жребий: добить раненого - это то, что сделал бы всякий, и это читается со
    слуха. Игрок слышит, кого бьют, и успевает его прикрыть.
    """
    foes = state.foes_of(actor.id)
    if not foes:
        return None
    return min(foes, key=lambda one: (one.health / max(1, one.max_health), one.id))


def is_low_health(state: BattleState, combatant_id: int) -> bool:
    """Читается экранами, чтобы начать с предупреждения."""
    one = state.by_id(combatant_id)
    if one is None:
        return False
    return one.health / max(1, one.max_health) <= LOW_HEALTH_THRESHOLD
