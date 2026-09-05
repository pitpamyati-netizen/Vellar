"""Движок боя: одна очередь, две стороны, любой состав.

Один вызов :func:`act` исполняет ход того, чья очередь, а затем прокручивает
ходы всех, за кого ходит движок, - пока очередь не дойдёт до живого игрока или
бой не кончится. Одиночный бой со стаей, отряд против стаи, поединок двоих и
отряд против отряда - один и тот же код с разным составом сторон (ADR 0021).

Таймеров нет нигде: очередь стоит и ждёт нажатия. Выход из брошенного боя один -
кнопка «Сдаться»: она отдаёт бой, а не отменяет его.

Два правила делают бой разменом, а не гонкой урона, и ни одно не добавляет
кнопки (круга контр - «тег X бьёт тег Y» - нет, ADR 0050):

- **намерение** - у того, за кого ходит движок, повадка постоянная, от породы
  (``INTENT_CYCLE`` по имени породы и месту в строю). Перебивают её раны и почти
  павшая цель; эпик и босс в заслон не встают. Живой игрок объявляет собственным
  следом. Стойка одинаково открывает своего и чужого: заслон удваивает броню и
  бьёт вполсилы, а объявивший напор - **breached**: удар по нему мимо брони, его
  собственный ответ вполсилы;
- **след** - повтор тега даёт разгон и усиливает удар (``MOMENTUM_*``), а три
  разных тега подряд - разнобой: противник теряет ближайший ход.

Всякая величина - удар, умение, лечение - названа процентом от того, что растёт
само: удар считается костями оружия, лечение и щит - долей максимума здоровья
(ADR 0007, 0015).

Вся случайность идёт от семени, переданного снаружи, поэтому бой воспроизводим.
У намерений случайности нет вовсе: они чистая функция состояния боя, и экран с
движком всегда называют одно и то же.
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
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.damage import UNARMED, DamageType
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack, status_effect
from mmorpg.domain.entities.location import Enemy, EnemyRank
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.entities.statuses import DOT_STATUSES, StatusKind, status_spec
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.procgen.enemies import RANK_FACTORS, group_scale
from mmorpg.domain.procgen.seeds import derive, rng, to_int
from mmorpg.domain.rules import edges as edge_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.progression import experience_reward
from mmorpg.domain.rules.skill_effects import (
    COUNTER,
    UNDYING,
    UNSTUNNABLE,
    EffectCategory,
    EffectSpec,
    Inflict,
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
    "damage_type_of",
    "defend_armor",
    "defend_dodge",
    "hero_combatant",
    "incoming_damage_factor",
    "intent_of",
    "is_low_health",
    "monster_combatant",
    "open_battle",
    "situational_damage",
    "spend_dot",
]

#: Какая доля удара достаётся цели каждый ход, пока на ней горение, яд или
#: кровотечение. Одна на все три: разница между ними в роде урона, а не в силе.
DOT_SHARE = 0.25

#: Сколько ходов держится барьер, которому срок не назначен, - барьер от грани.
DEFAULT_BARRIER_TURNS = 3

#: Потолок на число ходов, которые движок прокручивает за одно нажатие. Бой
#: четверых, где все под управлением движка, кончается сам; счётчик стоит на
#: случай содержимого, которое лечит быстрее, чем бьёт.
MAX_AUTOPLAY_TURNS = 400

# --- прибавки, которые смотрят по сторонам ---------------------------
#
# «Урон по зверям выше», «первый удар в бою сильнее», «на низком здоровье вы
# бьёте сильнее» читаются здесь, и только здесь: один проход по обстоятельствам
# удара, один множитель на выходе (``Claude.md``, правило 7).

#: Род урона умения - это его собственный тег: тег ``fire`` и есть
#: ``DamageType.FIRE`` (``entities/damage.py``). Тега нет - умение бьёт тем же,
#: чем боец бьёт и без него: оружием или своей породой.

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
# абсолютно, она превращала бы поздний бой в подбрасывание монеты.
ACCURACY_PER_LEVEL_GAP = 2.4

# Броня смягчается уровнем защищающегося; смягчение и цена надетого - одна кривая,
# прочитанная с двух концов, и живёт она в ``domain/rules/equipment.py``.
ARMOR_SOFTENER_BASE = gear.ARMOR_SOFTENER_BASE
ARMOR_SOFTENER_PER_LEVEL = gear.ARMOR_SOFTENER_PER_LEVEL
armor_factor = gear.armor_factor

# --- темп: намерение, след, разгон -----------------------------------

#: Повадка бойца, за которого ходит движок: её задаёт порода (по её инициативе),
#: поэтому вся стая волков стоит на экране ровным строем одного намерения.
#: Держится весь бой, пока обстоятельства не перебьют (ADR 0050).
INTENT_CYCLE = (ActionTag.PRESS, ActionTag.PRECISION, ActionTag.GUARD)
#: Эпик и босс в глухую оборону не встают и раненые не цепенеют: они хозяева
#: логова, весь бой давят. Открытость напора - их цена за длину боя.
INTENT_ELITE = (ActionTag.PRESS,)
#: Четверть здоровья - и зверь перестаёт разменивать удары.
WOUNDED_RATIO = 0.25
#: Треть здоровья у цели - и боец бросает повадку и добивает.
FINISH_RATIO = 1.0 / 3.0
#: Что объявленная стойка делает с бронёй того, кто её объявил, когда по нему
#: бьют. Повадка держится весь бой, а не круг, поэтому заслон - меньше, чем
#: удвоение: оно растягивало бой с породой-заслоном втрое (ADR 0050).
INTENT_ARMOR: dict[ActionTag, float] = {
    ActionTag.PRESS: 0.75,
    ActionTag.PRECISION: 1.0,
    ActionTag.GUARD: 1.5,
}
#: И с уроном его собственного удара.
INTENT_DAMAGE: dict[ActionTag, float] = {
    ActionTag.PRESS: 1.0,
    ActionTag.PRECISION: 0.95,
    ActionTag.GUARD: 0.5,
}
#: Два одинаковых тега подряд - разгон.
MOMENTUM_STREAK = 2
#: Прибавка за каждый повтор сверх первого: третий тег подряд стоит +50%.
MOMENTUM_DAMAGE_PERCENT = 25.0
#: Удар того, кого застали на замахе напора, доходит вполсилы.
BREACH_ANSWER_SCALE = 0.5

# --- защита ------------------------------------------------------------
#
# Закрыться умеет всякий, и умения на это не нужно: ход уходит целиком на
# оборону, а взамен чужой удар и находит реже, и стоит дешевле (ADR 0025).
# Броня считается от уровня, потому что от уровня растёт и чужой удар.

#: Сколько брони прибавляет закрывшемуся каждый его уровень.
DEFEND_ARMOR_PER_LEVEL = 6.0
#: Сколько ходов держится защита. Два, а не один: срок укорачивается в конце
#: того же хода, в который защита поставлена (``_upkeep``).
DEFEND_TURNS = 2
#: Как защита называется в событии - тем же словом, каким её называет кнопка.
DEFEND_NAME = "Защита"


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
    дерётся тем же оружием и теми же умениями, что и его хозяин (ADR 0021).

    Отряд на числа бойца не влияет ничем (``domain/rules/party.py``). Бой
    начинается с тем здоровьем, с каким персонаж в него вошёл: раны переходят
    из узла в узел, и потому зелье и ночлег стоят денег.
    """
    effects = EffectStack()
    stats = derived_stats(content, character, effects)
    return Combatant(
        id=combatant_id,
        side=side,
        kind=CombatantKind.HERO,
        name=character.name,
        level=character.level,
        max_health=stats.max_health,
        health=min(character.health_or(stats.max_health), stats.max_health),
        max_resource=stats.max_resource,
        resource=stats.max_resource,
        resource_name=stats.resource_name,
        initiative=stats.initiative,
        live=live,
        damage_type=gear.weapon_damage_type(content, character),
        character_id=character.id,
        user_id=character.user_id if live else 0,
        effects=effects,
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
    быстрее, первый удар его.
    """
    fighters = tuple(combatants)
    state = BattleState(combatants=fighters, order=_order_for(fighters, seed, 1))
    state = _drive(content, roster, state, seed)
    return state


def _order_for(combatants: Sequence[Combatant], seed: bytes, round_number: int) -> tuple[int, ...]:
    """Очередь на круг: быстрые раньше, равные - по жребию круга.

    Очередь решает одна инициатива, и это единственное, что инициатива делает
    (ADR 0021). Порядок пересобирается каждый круг, но при неизменных числах
    получается тем же: двойной ход достаётся только тому, кого и правда ускорили.
    Жребий берётся из семени боя, а не из номера круга, иначе двое с одинаковой
    инициативой менялись бы местами каждый круг.
    """

    def key(one: Combatant) -> tuple[float, int]:
        boost = 1.0 + one.effects.modifiers().get("initiative_percent", 0.0) / 100.0
        lot = to_int(derive(seed, "order", one.id)) % 1000
        return (-one.initiative * boost, lot)

    return tuple(one.id for one in sorted((one for one in combatants if one.alive), key=key))


# --- намерение и темп -------------------------------------------------


def intent_of(state: BattleState, combatant: Combatant) -> ActionTag | None:
    """Что этот боец объявляет на свой следующий ход.

    Чистая функция от состояния боя: экран печатает объявление, движок держит
    обещание, случайности здесь нет. За живого игрока объявляет его собственный
    след. За кого ходит движок - у того постоянная повадка от породы и места в
    строю (``INTENT_CYCLE``), пока обстоятельства её не перебьют: сам ранен - в
    заслон, цель почти пала - в напор (ADR 0050).
    """
    if combatant.live:
        return combatant.trace.last
    # Эпик и босс - хозяева логова: в глухую оборону не встают и раненые не
    # цепенеют, весь бой давят и метят. У обычного противника четверть здоровья -
    # и он бросает размен.
    elite = combatant.rank is not EnemyRank.NORMAL
    if not elite and combatant.health * 4 <= max(1, combatant.max_health):
        return ActionTag.GUARD
    target = state.target_for(combatant.id)
    if target is not None and target.health <= max(1, target.max_health) * FINISH_RATIO:
        return ActionTag.PRESS
    # Повадка постоянна весь бой и у каждого бойца стаи своя: место в строю
    # разводит троих по трём намерениям, а порода (сумма кодов имени) решает, с
    # какого из них начать (ADR 0050).
    key = sum(map(ord, combatant.enemy.archetype_id)) if combatant.enemy is not None else 0
    place = [one.id for one in state.combatants if one.side == combatant.side].index(combatant.id)
    if elite:
        return INTENT_ELITE[(key + place) % len(INTENT_ELITE)]
    return INTENT_CYCLE[(key + place) % len(INTENT_CYCLE)]


@dataclass(frozen=True, slots=True)
class TurnTempo:
    """Всё, что правила тегов решают о ходе, посчитанное до самого хода.

    Считать заранее приходится: разгон меняет урон того самого действия, которое
    его и заработало, а разнобой решает, останется ли ход за бойцом.
    """

    intents: Mapping[int, ActionTag | None]
    tag: ActionTag | None = None
    streak: int = 0
    #: Три разных тега подряд: противник, на ком размен сломался, теряет ход.
    breakthrough: bool = False
    #: Что объявил на этот ход тот, кто ходит. У живого игрока объявления нет -
    #: за него стоит ``tag`` (тег выбранного действия).
    own_intent: ActionTag | None = None

    @property
    def momentum(self) -> bool:
        return self.streak >= MOMENTUM_STREAK

    def breached(self, combatant_id: int) -> bool:
        """Застали ли эту цель на замахе напора.

        Напор - это и «бью сильнее», и «открыт», и открыт одинаково с обеих
        сторон: удар по объявившему мимо брони, его ответ вполсилы (ADR 0050).
        """
        return self.intents.get(combatant_id) is ActionTag.PRESS

    def armor_scale(self, combatant_id: int) -> float:
        """Что объявленная этой целью стойка делает с её же бронёй в этот ход.

        Заслон броню поднимает; напор выносит её из счёта целиком (цель на замахе);
        финт не трогает. Для живого игрока «объявление» - это его последний тег
        (``intent_of``).
        """
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
    if unstunnable and actor.effects.control() is not None:
        # Обещание держится с той стороны, с какой его дают: всё, что отнимает
        # ход, с такого бойца снимается разом.
        actor = replace(actor, effects=actor.effects.without_control())
        state = state.replace_combatant(actor)

    if (held := actor.effects.control()) is not None:
        working = state.with_events(
            BattleEvent(
                kind=EventKind.TURN_SKIPPED,
                actor_id=actor.id,
                actor=actor.name,
                effect_name=held.name,
            )
        )
        working = _upkeep(content, roster, working, actor.id)
        return _advance(working, seed)

    # Очарованный дерётся против своих, спутанный бьёт куда попало. Ход у них
    # есть - они просто не решают, куда он уйдёт.
    action = _hijacked(state, actor, action, seed)
    # Незаметность, с которой боец вошёл в ход: тот ход, на котором её и повесили,
    # её не снимает - иначе умение гасило бы само себя (ADR 0043).
    was_unseen = actor.effects.has(StatusKind.UNSEEN)

    tempo = _tempo(content, roster, state, actor, action)
    working = _announce_tempo(state, actor, tempo)
    working = _perform(content, roster, working, actor.id, action, tempo, source)

    updated = working.by_id(actor_id)
    if updated is not None:
        updated = replace(updated, trace=_advanced_trace(updated.trace, tempo))
        if (
            was_unseen
            and action.kind is not ActionKind.DEFEND
            and updated.effects.has(StatusKind.UNSEEN)
        ):
            # Всякое действие, кроме защиты, выдаёт ушедшего из виду (ADR 0043).
            updated = replace(updated, effects=updated.effects.without(StatusKind.UNSEEN))
            working = working.with_events(
                BattleEvent(
                    kind=EventKind.STATUS_ENDED,
                    actor_id=updated.id,
                    actor=updated.name,
                    effect_name=status_spec(StatusKind.UNSEEN).name,
                )
            )
        updated, recloaked = _recloaked(content, updated, was_unseen)
        working = working.replace_combatant(updated)
        if recloaked:
            working = working.with_events(
                BattleEvent(
                    kind=EventKind.STATUS_APPLIED,
                    actor_id=updated.id,
                    actor=updated.name,
                    effect_name=status_spec(StatusKind.UNSEEN).name,
                    turns=updated.effects.turns_of(StatusKind.UNSEEN),
                )
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
    противника целый круг, и один игрок решал бы бой за весь отряд (ADR 0021).
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
    return _inflicted(
        working,
        current.id,
        Inflict(kind=StatusKind.STUN, turns=1),
        power=0.0,
        skill_name="",
        source_code="breakthrough",
    )


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
    if actor.effects.control() is not None:
        return None
    if actor.effects.has(StatusKind.CHARM) or actor.effects.has(StatusKind.CONFUSION):
        # Нажатое всё равно будет заменено: спрашивать о цене нечего.
        return None
    match action.kind:
        case ActionKind.SKILL | ActionKind.RACIAL:
            if actor.effects.has(StatusKind.SILENCE):
                # Молчание - отказ до хода, а не промах: ход остаётся за бойцом.
                return BattleEvent(kind=EventKind.SILENCED, actor_id=actor.id, actor=actor.name)
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
    if tag is None or actor.effects.control() is not None:
        return TurnTempo(intents=intents, own_intent=own)

    trace = actor.trace
    # Разгон - награда за выбор, а выбор делает тот, за кем стоит персонаж. Порода
    # бьёт одним и тем же тегом всегда, и разгона за однообразие ей не полагается.
    # Своя стойка у неё есть - её объявляет ``intent_of``.
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
        case ActionKind.DEFEND:
            return ActionTag.GUARD
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
    """Разнобой тратит след, всё прочее его удлиняет; ход без тега не трогает.

    Без траты следа вечное «напор - заслон - финт» ломало бы каждый ход с
    четвёртого, и противник не ходил бы больше никогда.
    """
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
        case ActionKind.DEFEND:
            return _defend(state, actor)
        case ActionKind.SKILL | ActionKind.RACIAL:
            return _use_skill(content, roster, state, actor, action, tempo, source)
        case ActionKind.ITEM:
            return _use_item(content, roster, state, actor, action)
        case ActionKind.FLEE:
            return _try_flee(content, roster, state, actor, source)
        case _:  # pragma: no cover - смену цели и сдачу разбирает ``act``
            return state


# --- кого бьём --------------------------------------------------------


def _hijacked(
    state: BattleState, actor: Combatant, action: BattleAction, seed: bytes
) -> BattleAction:
    """Что боец сделает на самом деле, если выбор больше не за ним.

    Очарованный бьёт своих, спутанный - кого попало, включая своих. Ход при этом
    состоится: отнимать его - дело оглушения, заморозки и страха, и три разных
    состояния не должны делать одно и то же.
    """
    if actor.effects.has(StatusKind.CHARM):
        allies = state.allies_of(actor.id, include_self=False)
        if not allies:
            return action
        pick = rng(derive(seed, "charm", state.round, actor.id)).choice(allies)
        return BattleAction(kind=ActionKind.ATTACK, target=pick.id)
    if actor.effects.has(StatusKind.CONFUSION):
        others = tuple(one for one in state.living() if one.id != actor.id)
        if not others:  # pragma: no cover - бой без второго бойца уже кончен
            return action
        pick = rng(derive(seed, "confusion", state.round, actor.id)).choice(others)
        return BattleAction(kind=ActionKind.ATTACK, target=pick.id)
    return action


def _strays(actor: Combatant) -> bool:
    """Бьёт ли этот боец по кому попало, включая своих."""
    return actor.effects.has(StatusKind.CHARM) or actor.effects.has(StatusKind.CONFUSION)


def _target_of(state: BattleState, actor: Combatant, requested: int) -> Combatant | None:
    """Цель этого удара: названная в действии, потом выбранная, потом любая."""
    named = state.by_id(requested)
    if named is not None and named.alive and not named.effects.has(StatusKind.UNSEEN):
        strayed = _strays(actor) and named.id != actor.id
        if named.side != actor.side or strayed:
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
    if target is None:
        # Бой не кончен, а бить некого: все враги ушли из виду (ADR 0043). Ход
        # уходит впустую - это и есть выигрыш незаметности в одиночном бою.
        return state.with_events(
            BattleEvent(kind=EventKind.NO_TARGET, actor_id=actor.id, actor=actor.name)
        )

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


def defend_armor(level: int) -> int:
    """Сколько брони даёт защита на этом уровне. То же число слышит игрок."""
    return max(1, round(level * DEFEND_ARMOR_PER_LEVEL))


def defend_dodge() -> float:
    """Насколько защита поднимает уклонение. Объявлено у самого состояния."""
    return status_spec(StatusKind.GUARD).flat_modifiers.get("dodge_percent", 0.0)


def _defend(state: BattleState, actor: Combatant) -> BattleState:
    """Закрыться: броня от уровня и уклонение до своего следующего хода."""
    armor = float(defend_armor(actor.level))
    return _inflicted(
        state,
        actor.id,
        Inflict(kind=StatusKind.GUARD, turns=DEFEND_TURNS),
        power=armor,
        skill_name=DEFEND_NAME,
        source_code="defend",
        magnitude=armor,
    )


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

    # Удар со спины бьют только из незаметности. Отказ до хода, как не то оружие:
    # ход не тратится, следа нет (ADR 0050, ADR 0016).
    if skill.requires_stealth and not actor.effects.has(StatusKind.UNSEEN):
        return BattleEvent(kind=EventKind.NEEDS_STEALTH, skill_name=skill.name)

    cooldown = actor.cooldown_of(skill.code)
    if cooldown > 0:
        return BattleEvent(kind=EventKind.ON_COOLDOWN, skill_name=skill.name, turns=cooldown)

    modifiers = mods.collect_modifiers(content, character, actor.effects)
    cost = round(
        _skill_cost(skill, modifiers, free=actor.free_cast, max_resource=actor.max_resource)
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
    if rank >= content.rules.max_rank:
        cooldown = max(0, cooldown - skill_rules.MASTERY_COOLDOWN)
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


def skill_cost(skill: Skill, max_resource: int) -> int:
    """Во что умение обходится бойцу с таким запасом.

    ``Skill.cost`` - проценты от максимума запаса, а не число (ADR 0058): запас
    растёт с уровнем и характеристикой, а число не росло бы ни с чем. Проценты
    значат одно и то же на всей полосе.
    """
    if skill.cost <= 0 or max_resource <= 0:
        return 0
    return max(1, round(max_resource * skill.cost / 100.0))


def _skill_cost(
    skill: Skill, modifiers: Mapping[str, float], *, free: bool, max_resource: int
) -> int:
    if free:
        return 0
    reduction = 1.0 - modifiers.get("cost_reduction_percent", 0.0) / 100.0
    return max(0, round(skill_cost(skill, max_resource) * max(0.1, reduction)))


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

        working = _dotted(
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

    # Кого лечит и с кого снимает беды это умение: себя, а умение по площади -
    # весь свой отряд.
    mended = tuple(one.id for one in working.allies_of(actor_id)) if spec.aoe else (actor_id,)

    if spec.category is EffectCategory.HEAL:
        # Лечение и щит - проценты от максимума здоровья, а не от удара: здоровье
        # растёт впятеро быстрее удара. Доля своя у каждого: лечение на четверть
        # запаса поднимает раненого товарища ровно на четверть его запаса.
        for beneficiary in mended:
            one = working.by_id(beneficiary)
            if one is None:
                continue
            amount = round(one.max_health * power / 100.0)
            amount = round(amount * mods.percent(modifiers, "healing_done_percent"))
            if spec.special == "heal_over_time":
                working = _mending(
                    working, beneficiary, skill=skill, spec=spec, per_turn=float(amount)
                )
            else:
                # «Насколько тебя лечат» - прибавка того, кого лечат, а не того,
                # кто лечит.
                working = _heal(
                    working,
                    beneficiary,
                    amount,
                    _modifiers_of(content, roster, one),
                    skill_name=skill.name,
                )

    if spec.category is EffectCategory.BARRIER:
        holder = working.by_id(actor_id)
        if holder is not None:
            held = round(holder.max_health * power / 100.0)
            working = _barriered(working, actor_id, skill=skill, spec=spec, amount=held)

    if spec.bonus_heal:
        # По площади - тому же отряду: «лечит ещё на 10 процентов» у полкового
        # лечения добавляет всем, а не одному знаменосцу.
        for beneficiary in mended if spec.category is EffectCategory.HEAL else (actor_id,):
            holder = working.by_id(beneficiary)
            if holder is not None:
                extra = round(holder.max_health * spec.bonus_heal / 100.0)
                working = _heal(
                    working,
                    beneficiary,
                    extra,
                    _modifiers_of(content, roster, holder),
                    skill_name=skill.name,
                )

    if spec.bonus_barrier:
        holder = working.by_id(actor_id)
        if holder is not None:
            extra = round(holder.max_health * spec.bonus_barrier / 100.0)
            working = _barriered(working, actor_id, skill=skill, spec=spec, amount=extra)

    if spec.cleanse_count:
        # Умение по площади снимает беды со всего отряда - «и раны закрыть»
        # у полкового лечения закрывает раны всем, а не только знаменосцу.
        cleansed_from = mended if spec.aoe else (actor_id,)
        for beneficiary in cleansed_from:
            holder = working.by_id(beneficiary)
            if holder is None:
                continue
            before = len(holder.effects.penalties())
            cleansed = holder.effects.cleanse(cleansed_count(spec, power))
            removed = before - len(cleansed.penalties())
            working = working.replace_combatant(replace(holder, effects=cleansed))
            if removed:
                working = working.with_events(
                    BattleEvent(
                        kind=EventKind.CLEANSED,
                        actor_id=beneficiary,
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
    working = _apply_statuses(
        working,
        actor_id,
        skill,
        spec,
        power,
        requested,
        blow=blow,
        modifiers=modifiers,
        landed=tuple(landed) if spec.category is EffectCategory.DAMAGE else None,
    )
    return _apply_special(working, actor_id, skill, spec, power, requested)


def _apply_statuses(
    state: BattleState,
    actor_id: int,
    skill: Skill,
    spec: EffectSpec,
    power: float,
    requested: int,
    *,
    blow: float,
    modifiers: Mapping[str, float],
    landed: tuple[int, ...] | None,
) -> BattleState:
    """Состояния, которые умение вешает: на себя и на цель.

    На цель - только следом за попаданием: всё, что умение обещает цели, идёт за
    состоявшимся ударом, а не за нажатием (ADR 0016).
    """
    working = state
    actor = working.by_id(actor_id)
    if actor is None:  # pragma: no cover
        return working

    for hold in spec.holds:
        working = _inflicted(
            working,
            actor_id,
            hold,
            power=power,
            skill_name=skill.name,
            source_code=skill.code,
        )

    if not spec.inflicts:
        return working
    if landed is None:
        targets = tuple(one.id for one in _foes(working, actor, requested, aoe=spec.aoe))
    else:
        targets = landed
    per_turn = max(
        1.0, blow * power / 100.0 * DOT_SHARE * mods.percent(modifiers, "dot_damage_percent")
    )
    for target_id in targets:
        for inflict in spec.inflicts:
            if inflict.kind is StatusKind.TAUNT:
                # Величина провокации - номер провокатора: по нему движок потом
                # ведёт вызванного бойца (``_forced_target``). Срок берётся из
                # умения, чтобы грань «Затянуть» тянула и саму провокацию, а не
                # только броню провокатора.
                inflict = replace(inflict, turns=max(inflict.turns, spec.duration))
                magnitude: float | None = float(actor_id)
            elif status_spec(inflict.kind).is_dot:
                magnitude = per_turn
            else:
                magnitude = None
            working = _inflicted(
                working,
                target_id,
                inflict,
                power=power,
                skill_name=skill.name,
                source_code=skill.code,
                magnitude=magnitude,
            )
    return working


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
    ``healing_taken_percent`` считается здесь. ``modifiers`` поэтому всегда
    свёрток **того, кого лечат**: «насколько меня лечат» - его прибавка, а не
    того, кто взмахнул рукой.
    """
    one = state.by_id(combatant_id)
    if one is None:  # pragma: no cover
        return state
    if one.effects.has(StatusKind.HEAL_BLOCK):
        # Запрет лечения не уменьшает лечение, а отменяет его: половинчатый
        # запрет читался бы как «слабое лечение» и не был бы виден вовсе.
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


def _inflicted(
    state: BattleState,
    combatant_id: int,
    inflict: Inflict,
    *,
    power: float,
    skill_name: str,
    source_code: str,
    magnitude: float | None = None,
) -> BattleState:
    """Повесить состояние на бойца и сказать об этом.

    ``magnitude`` называется прямо там, где величину считает не сила умения, а
    удар: горение сильного бойца жжёт сильнее, чем горение слабого.
    """
    one = state.by_id(combatant_id)
    if one is None or not one.alive:
        return state
    kind = inflict.kind
    spec = status_spec(kind)
    if spec.skips_turn and one.effects.modifiers().get(UNSTUNNABLE, 0.0) > 0:
        # Обещание «вас нельзя оглушить» держится с той стороны, с какой дано.
        return state
    amount = inflict.magnitude(power) if magnitude is None else magnitude
    effect = status_effect(kind, turns=inflict.turns, magnitude=amount, source=source_code)
    updated = replace(one, effects=one.effects.apply(effect))
    if kind is StatusKind.BARRIER:
        updated = replace(updated, barrier=updated.barrier + max(0, round(amount)))
    return state.replace_combatant(updated).with_events(
        BattleEvent(
            kind=EventKind.BARRIER if kind is StatusKind.BARRIER else EventKind.STATUS_APPLIED,
            actor_id=updated.id if spec.beneficial else 0,
            actor=updated.name if spec.beneficial else "",
            target_id=0 if spec.beneficial else updated.id,
            target="" if spec.beneficial else updated.name,
            amount=max(0, round(amount)) if kind is StatusKind.BARRIER else 0,
            skill_name=skill_name,
            effect_name=spec.name,
            turns=inflict.turns,
        )
    )


def _mending(
    state: BattleState, combatant_id: int, *, skill: Skill, spec: EffectSpec, per_turn: float
) -> BattleState:
    """Лечение, которое приходит каждый ход, а не сейчас.

    Величина - доля максимума здоровья за ход: состояние живёт дольше, чем
    посчитанное однажды число, и здоровье за это время могло вырасти.
    """
    one = state.by_id(combatant_id)
    if one is None:  # pragma: no cover
        return state
    share = per_turn / max(1, one.max_health) * 100.0
    return _inflicted(
        state,
        combatant_id,
        Inflict(kind=StatusKind.HEALTH_REGEN, turns=max(1, spec.dot_turns)),
        power=share,
        skill_name=skill.name,
        source_code=skill.code,
        magnitude=share,
    )


def _barriered(
    state: BattleState, combatant_id: int, *, skill: Skill, spec: EffectSpec, amount: int
) -> BattleState:
    """Барьер и срок, который его держит."""
    if amount <= 0:
        return state
    turns = spec.barrier_turns or DEFAULT_BARRIER_TURNS
    return _inflicted(
        state,
        combatant_id,
        Inflict(kind=StatusKind.BARRIER, turns=turns),
        power=float(amount),
        skill_name=skill.name,
        source_code=skill.code,
        magnitude=float(amount),
    )


def _dotted(
    state: BattleState,
    *,
    spec: EffectSpec,
    skill: Skill,
    blow: float,
    power: float,
    struck: tuple[int, ...],
    modifiers: Mapping[str, float],
) -> BattleState:
    """Оставить на раненых то, что будет их точить каждый ход.

    Каким состоянием точит, называет само умение: огненное жжёт, ядовитое
    травит, а всё прочее пускает кровь.
    """
    if not spec.dot_turns:
        return state
    per_turn = max(
        1.0,
        blow * power / 100.0 * DOT_SHARE * mods.percent(modifiers, "dot_damage_percent"),
    )
    working = state
    for combatant_id in struck:
        working = _inflicted(
            working,
            combatant_id,
            Inflict(kind=spec.dot_status, turns=spec.dot_turns),
            power=per_turn,
            skill_name=skill.name,
            source_code=skill.code,
            magnitude=per_turn,
        )
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
    # У породы своего уклонения нет: её защита - броня и здоровье. Прозвище
    # («Верткий» и подобные) может его дать эффектом (ADR 0042).
    return one.effects.modifiers().get("dodge_percent", 0.0)


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


def damage_type_of(attacker: Combatant, spec: EffectSpec | None) -> DamageType:
    """Род урона: названный умением, иначе свой собственный.

    Свой - это оружие в руке героя или порода противника. Умение, назвавшее род,
    бьёт им, чем бы боец ни держал: огненная стрела жжёт и из лука, и из посоха.
    """
    if spec is not None and (named := spec.damage_type) is not None:
        return named
    return attacker.element


def situational_damage(
    modifiers: Mapping[str, float],
    *,
    spec: EffectSpec | None,
    magic: bool,
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
    kind = "magic" if magic else "physical"
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


def incoming_damage_factor(modifiers: Mapping[str, float], damage: DamageType) -> float:
    """Что сопротивление оставляет от чужого удара этого рода.

    Считаются оба ключа: сопротивление самому роду и сопротивление его половине -
    физической или магической. Латы держат удар вообще, а стёганка под ними -
    именно колющий, и складываются они, а не выбирают друг друга.
    """
    resisted = modifiers.get(damage.resist_key, 0.0) + modifiers.get(damage.half_resist_key, 0.0)
    return max(0.0, 1.0 - resisted / 100.0)


def _recloaked(content: GameContent, one: Combatant, was_unseen: bool) -> tuple[Combatant, bool]:
    """Стая-«соглядатай» уходит из виду снова после того, как её выдали (ADR 0043).

    Только когда она вошла в ход уже видимой: ход, на котором её выдал
    собственный удар, окна игроку не отнимает - незаметность возвращается через
    ``recloak`` её ходов, отсчитанных откатом ``affix:recloak``.
    """
    if was_unseen or one.enemy is None or one.effects.has(StatusKind.UNSEEN):
        return one, False
    period = max(
        (
            content.affix(affix_id).recloak
            for affix_id in one.enemy.affixes
            if content.has_affix(affix_id)
        ),
        default=0,
    )
    if not period or one.cooldown_of("affix:recloak") > 0:
        return one, False
    hidden = replace(
        one, effects=one.effects.apply(status_effect(StatusKind.UNSEEN, turns=period + 1))
    ).with_cooldown("affix:recloak", period)
    return hidden, True


def _shed_on_hit(one: Combatant) -> tuple[Combatant, tuple[str, ...]]:
    """Снять с бойца всё, что спадает от первого же долетевшего удара.

    Пометка состояния - ``broken_by_damage`` (страх и незаметность), и разлив
    один на всех, кто её несёт (ADR 0043). Второй член - имена снятого, для
    события ``STATUS_ENDED``.
    """
    shed = tuple(
        effect.status
        for effect in one.effects.statuses()
        if effect.status is not None and status_spec(effect.status).broken_by_damage
    )
    if not shed:
        return one, ()
    effects = one.effects
    for kind in shed:
        effects = effects.without(kind)
    return replace(one, effects=effects), tuple(status_spec(kind).name for kind in shed)


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

    # Неуязвимость - не большая броня, а её отсутствие с той стороны: удар
    # доходит и не значит ничего.
    if target.effects.has(StatusKind.INVULNERABILITY):
        return (
            working.with_events(
                BattleEvent(
                    kind=EventKind.IMMUNE,
                    actor_id=actor.id,
                    actor=actor.name,
                    target_id=target.id,
                    target=target.name,
                    skill_name=skill_name,
                )
            ),
            False,
        )

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
    # Не уклоняются от объявленного породой финта и от удара из незаметности:
    # первый нацелен заранее, второго цель не видит (ADR 0050).
    dodgeable = tempo.own_intent is not ActionTag.PRECISION and not (
        spec is not None and spec.always_hits
    )
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
    struck_with = damage_type_of(actor, spec)
    raw *= situational_damage(
        attacker_mods,
        spec=spec,
        magic=struck_with.is_magic,
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
    # Заслон удваивает броню цели; напор выносит её из счёта целиком (цель на
    # замахе); финт брони не трогает (``TurnTempo.armor_scale``).
    effective_armor = (
        _armor_of(content, roster, target) * (1.0 - pierce) * tempo.armor_scale(target.id)
    )
    raw *= armor_factor(effective_armor, target.level)
    raw *= incoming_damage_factor(target_mods, struck_with)
    # Удар того, кого застали на замахе напора, доходит вполсилы: он открылся, и
    # платит этим же ходом, а не следующим.
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
    # Испуганного приводит в чувство первый же удар, а ушедшего из виду - выдаёт:
    # оба спадают от долетевшего удара (``_shed_on_hit``).
    hurt, revealed = _shed_on_hit(hurt)
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
    for name in revealed:
        working = working.with_events(
            BattleEvent(
                kind=EventKind.STATUS_ENDED,
                actor_id=hurt.id,
                actor=hurt.name,
                effect_name=name,
            )
        )

    if spec is not None and spec.stun_turns and hurt.alive:
        working = _inflicted(
            working,
            hurt.id,
            Inflict(kind=StatusKind.STUN, turns=spec.stun_turns),
            power=0.0,
            skill_name=skill_name,
            source_code="",
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

    if not answering and actor.enemy is not None and actor.enemy.affixes:
        working = _affix_on_hit(
            content, working, attacker=actor, target=hurt, amount=amount, source=source
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


def _affix_on_hit(
    content: GameContent,
    state: BattleState,
    *,
    attacker: Combatant,
    target: Combatant,
    amount: int,
    source: random.Random,
) -> BattleState:
    """Прозвище-модификатор породы, срабатывающее по попаданию (ADR 0042).

    «Гнилозубый» травит, «Стылый» студит, «Измождённый» вешает немощь - и только
    на состоявшийся удар, а не на ответный (``answering``): размен статусов не
    должен множиться.
    """
    if attacker.enemy is None:
        return state
    working = state
    for affix_id in attacker.enemy.affixes:
        if not content.has_affix(affix_id):
            continue
        affix = content.affix(affix_id)
        if affix.on_hit_status is None:
            continue
        if source.uniform(0, 100) >= affix.on_hit_chance:
            continue
        if affix.on_hit_status in DOT_STATUSES:
            magnitude = affix.on_hit_magnitude or max(1.0, amount * DOT_SHARE)
        else:
            magnitude = affix.on_hit_magnitude
        working = _inflicted(
            working,
            target.id,
            Inflict(kind=affix.on_hit_status, turns=max(1, affix.on_hit_turns)),
            power=0.0,
            skill_name=affix.adjective,
            source_code=f"affix:{affix_id}",
            magnitude=magnitude,
        )
    return working


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

    Выпад воина отвечает на каждый удар, пассивное умение паладина возвращает
    часть полученного урона. Ответ ответа не вызывает: размен двух отражений не
    кончился бы никогда.
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
        case "flashbang":
            # Летит во всех врагов сразу, цель не выбирают: слепит вспышкой и
            # выдаёт ушедших из виду (ADR 0043). Незаметность снимается прямо,
            # а не через ``_shed_on_hit``: урона у гранаты нет.
            span = max(1, item.effect.turns)
            for foe in working.foes_of(actor.id):
                one = working.by_id(foe.id)
                if one is None:
                    continue
                effects = one.effects.without(StatusKind.UNSEEN)
                effects = effects.apply(
                    ActiveEffect(
                        id=f"item:{item.id}",
                        name=item.name,
                        modifiers={"accuracy_percent": -item.effect.power},
                        turns_left=span,
                        beneficial=False,
                    )
                )
                working = working.replace_combatant(replace(one, effects=effects))
                if one.effects.has(StatusKind.UNSEEN):
                    working = working.with_events(
                        BattleEvent(
                            kind=EventKind.STATUS_ENDED,
                            actor_id=one.id,
                            actor=one.name,
                            effect_name=status_spec(StatusKind.UNSEEN).name,
                        )
                    )
                working = working.with_events(
                    BattleEvent(
                        kind=EventKind.EFFECT_APPLIED,
                        target_id=one.id,
                        target=one.name,
                        effect_name=item.name,
                        turns=span,
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

    Всё, что меряется ходами, - откаты, сроки состояний, горение, яд, кровь,
    восстановление здоровья и сил, - тикает здесь и только здесь. Своими ходами,
    а не чужими: в бою четверых «три хода» иначе значило бы разное для каждого.
    """
    one = state.by_id(combatant_id)
    if one is None or not one.alive:
        return state

    modifiers = _modifiers_of(content, roster, one)
    # Брешь живёт до ответа: он только что походил - снимаем.
    updated = replace(one.tick_cooldowns(), breached=False)

    # Состояния платят по счёту до того, как срок укоротится: горение на три
    # хода жжёт три раза, а не два.
    dots = tuple(
        (effect.status, max(0, round(effect.magnitude)))
        for effect in updated.effects.statuses()
        if effect.status is not None and status_spec(effect.status).is_dot
    )
    mending = updated.effects.magnitude_of(StatusKind.HEALTH_REGEN)
    refill = updated.effects.magnitude_of(StatusKind.RESOURCE_REGEN)
    blocked = updated.effects.has(StatusKind.RESOURCE_BLOCK)
    ended = tuple(
        effect
        for effect in updated.effects.statuses()
        if effect.turns_left <= 1 and effect.status is not None
    )
    updated = replace(updated, effects=updated.effects.tick())

    regen_percent = modifiers.get("regen_per_turn_percent", 0.0)
    if regen_percent:
        updated, _ = updated.healed(round(updated.max_health * regen_percent / 100.0))
    if updated.max_resource and not blocked:
        stats = _stats_of(content, roster, updated)
        regen = round(stats.resource_regen) if stats is not None else 0
        regen += round(updated.max_resource * refill / 100.0)
        updated = replace(updated, resource=min(updated.max_resource, updated.resource + regen))
    # Барьер стоит ровно столько, сколько его держат: состояние ушло - ушёл и он.
    held = round(updated.effects.magnitude_of(StatusKind.BARRIER))
    updated = replace(updated, barrier=min(updated.barrier, max(0, held)))

    working = state.replace_combatant(updated)
    working = _repooled(content, roster, working, combatant_id)
    if mending > 0:
        healed = round(updated.max_health * mending / 100.0)
        working = _heal(working, combatant_id, healed, modifiers)
    for kind, amount in dots:
        if kind is None or amount <= 0:  # pragma: no cover - величина всегда есть
            continue
        working = spend_dot(working, combatant_id, kind, amount)
    for effect in ended:
        working = working.with_events(
            BattleEvent(
                kind=EventKind.STATUS_ENDED,
                actor_id=combatant_id,
                actor=updated.name,
                effect_name=effect.name,
            )
        )
    return working


def spend_dot(
    state: BattleState,
    combatant_id: int,
    kind: StatusKind | None = None,
    amount: int | None = None,
) -> BattleState:
    """Горение, яд и кровотечение платят по счёту - раз в ход, до конца срока.

    Урон идёт своим родом: огонь жжёт огнём, яд травит ядом, кровь течёт от
    разрыва, - и сопротивление цели считается по нему, как у всякого удара.
    """
    one = state.by_id(combatant_id)
    if one is None or not one.alive:
        return state

    working = state
    pending: list[tuple[StatusKind, int | None]] = (
        [(kind, amount)]
        if kind is not None
        else [
            (effect.status, round(effect.magnitude))
            for effect in one.effects.statuses()
            if effect.status is not None and status_spec(effect.status).is_dot
        ]
    )
    for status, raw in pending:
        held = raw if raw is not None else round(one.effects.magnitude_of(status))
        if held <= 0:
            continue
        current = working.by_id(combatant_id)
        if current is None or not current.alive:
            break
        if current.effects.has(StatusKind.INVULNERABILITY):
            continue
        spec = status_spec(status)
        damage = spec.damage if spec.damage is not None else UNARMED
        toll = max(1, round(held * incoming_damage_factor(current.effects.modifiers(), damage)))
        hurt = replace(current, health=max(0, current.health - toll))
        # Долетевший дот выдаёт ушедшего из виду так же, как обычный удар.
        hurt, revealed = _shed_on_hit(hurt)
        working = working.replace_combatant(hurt).with_events(
            BattleEvent(
                kind=EventKind.DAMAGE,
                target_id=hurt.id,
                target=hurt.name,
                amount=toll,
                effect_name=spec.name,
            ),
            *(
                BattleEvent(
                    kind=EventKind.STATUS_ENDED,
                    actor_id=hurt.id,
                    actor=hurt.name,
                    effect_name=name,
                )
                for name in revealed
            ),
        )
        if not hurt.alive:
            working = working.with_events(
                BattleEvent(kind=EventKind.DEFEATED, target_id=hurt.id, target=hurt.name)
            )
            break
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
    ``domain/rules/pvp.py``. Стая делит бой целиком - и здоровье, и урон, и плату
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
            * one.enemy.stakes
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
    решает за него движок (ADR 0021).
    """
    target = _forced_target(state, actor) or _weakest_foe(state, actor)
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

    Добить раненого читается со слуха: игрок слышит, кого бьют, и успевает его
    прикрыть. Уводит удар с раненого только провокация - она названа кнопкой, и
    за неё платят ходом (``_forced_target``, ADR 0027).
    """
    foes = state.visible_foes_of(actor.id)
    if not foes:
        return None
    return min(foes, key=lambda one: (one.health / max(1, one.max_health), one.id))


def _forced_target(state: BattleState, actor: Combatant) -> Combatant | None:
    """Кого этот боец обязан бить, если его вызвали на провокацию.

    Провокация вешает на бойца ``StatusKind.TAUNT``, а величиной у неё - номер
    провокатора. Пока он жив и всё ещё враг вызванному, движок ведёт удар на
    него, а не на самого слабого. Провокатор при этом сам открыт: он крикнул -
    он и стоит под ударом (ADR 0027).
    """
    if not actor.effects.has(StatusKind.TAUNT):
        return None
    caller = state.by_id(round(actor.effects.magnitude_of(StatusKind.TAUNT)))
    if caller is None or not caller.alive or caller.side == actor.side:
        return None
    if caller.effects.has(StatusKind.UNSEEN):
        # Провокатор успел уйти из виду: пока он невидим, вести на него удар
        # некого - незаметность сильнее провокации (ADR 0043).
        return None
    return caller


def is_low_health(state: BattleState, combatant_id: int) -> bool:
    """Читается экранами, чтобы начать с предупреждения."""
    one = state.by_id(combatant_id)
    if one is None:
        return False
    return one.health / max(1, one.max_health) <= LOW_HEALTH_THRESHOLD
