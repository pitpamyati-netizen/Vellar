"""Выучка умения: чему игрок научил ЭТО умение, поднимая его ранг.

Ранг сам по себе прибавляет силу, укорачивает откат, тянет сроки и сбивает цену
(``rules/skills.rank_gain``). Этого мало: четыре числа - это по-прежнему четыре
числа, и очко, вложенное в третий ранг, ничем не отличается от очка, вложенного
во второй. **Ранг обязан дать выбор, а не прибавку** - тогда его хотят поднять.

Поэтому на третьем ранге и на пятом умение спрашивает, ЧЕМУ ОНО НАУЧИЛОСЬ, и
спрашивает СВОИМ СПИСКОМ. Выучки не общие: у каждого боевого умения свои
четыре - две на третьем ранге и две на пятом, - и написаны они под класс, под
ветку и под то, чем это умение в бою занимается (ADR 0083). Общего списка, из
которого выбирали бы все двести умений, нет вовсе: два разных умения никогда не
покажут один и тот же выбор.

ПОЧЕМУ ЭТО НЕ ПОВТОРЕНИЕ ГРАНЕЙ. Грани были подписями: 256 строк обещали
словами то, чего движок не делал, и потому отменены (ADR 0067). Здесь наоборот -
СВОЕГО ТЕКСТА У ВЫУЧКИ НЕТ. Содержимое пишет ей только имя и называет приём
(``Way``) с размером; текст складывает сам приём по своим же числам, а приём -
это правка ``EffectSpec``, того самого описания, по которому бой и считает.
Назвать выучку словами и не сделать её невозможно: слова берутся из механики.

ПРИЁМ ЗНАЕТ, КАКОЙ ФОРМЕ ОН ГОДИТСЯ. У умения есть форма - бьёт одну цель, бьёт
по всем, точит, лечит, ставит барьер, усиливает, мешает, отнимает ход, - и приём
называет формы, к которым подходит. «Пробой» не ляжет на лечение, «Разлив» - на
удар. Форма читается из самого описания эффекта, а не пишется в содержимом.
Загрузчик проверяет каждую написанную выучку дважды: приём подходит форме
умения И правка что-то меняет. Выучка, ничего не меняющая, - это подпись, и
игра с ней не заводится.

СТУПЕНЕЙ ДВЕ. Первая (третий ранг) правит числа: сколько, как часто, как долго.
Вторая (пятый) меняет саму форму: одноцелевой удар становится ударом по всем,
лечение ложится на весь отряд, помеха расходится по стае.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg.domain.entities.content import Skill, SkillMastery
from mmorpg.domain.entities.statuses import CONTROL_STATUSES, StatusKind
from mmorpg.domain.rules.skill_effects import (
    COUNTER,
    UNDYING,
    UNSTUNNABLE,
    EffectCategory,
    EffectSpec,
    Inflict,
    ModifierSpec,
)

#: Ранги, на которых умение спрашивает, чему оно научилось. Третий и пятый:
#: первый ранг - это само умение, а на втором и четвёртом игрок ещё копит.
MASTERY_RANKS: tuple[int, ...] = (3, 5)

#: Сколько выучек каждой ступени написано умению. Две: из одной не выбирают, а
#: из пяти выбирают не глядя.
MASTERIES_PER_TIER = 2


class Shape(StrEnum):
    """Форма умения - то, по чему видно, какой приём ему подойдёт."""

    #: Бьёт одну цель.
    STRIKE = "strike"
    #: Бьёт всех разом.
    SWEEP = "sweep"
    #: Оставляет то, что точит цель каждый ход.
    OVER_TIME = "over_time"
    #: Лечит.
    MENDING = "mending"
    #: Ставит барьер.
    WARD = "ward"
    #: Усиливает своего - прибавкой или состоянием.
    BOON = "boon"
    #: Мешает чужому - штрафом или состоянием.
    BANE = "bane"
    #: Отнимает у цели ход.
    BINDING = "binding"
    #: Особое: снятие бед, уход от боя, свободный ход. Числа удара такому умению
    #: править нечем, зато цена, барьер и «не пасть» ложатся на любое.
    DEED = "deed"


def shapes_of(spec: EffectSpec) -> frozenset[Shape]:
    """Что это умение такое. Форм у одного умения бывает несколько.

    Удар, оставляющий кровотечение, - и удар, и точащее; провокация - и помеха,
    и усиление себе. Приём годится, когда сходится хоть одна форма.
    """
    found: set[Shape] = set()
    if spec.category is EffectCategory.DAMAGE:
        found.add(Shape.SWEEP if spec.aoe else Shape.STRIKE)
    if spec.dot_turns and spec.category is not EffectCategory.HEAL:
        # То, что точит цель каждый ход, - это и помеха цели, чем бы её ни
        # объявили: «Зараза» разносит её по стае так же, как обычный штраф.
        found.update({Shape.OVER_TIME, Shape.BANE})
    if spec.category is EffectCategory.HEAL or spec.bonus_heal:
        found.add(Shape.MENDING)
    if spec.category is EffectCategory.BARRIER or spec.bonus_barrier:
        found.add(Shape.WARD)
    if spec.self_modifiers or spec.holds:
        found.add(Shape.BOON)
    if spec.target_modifiers or spec.inflicts:
        found.add(Shape.BANE)
    if spec.stun_turns or any(one.kind in CONTROL_STATUSES for one in spec.inflicts):
        found.add(Shape.BINDING)
    if not found or spec.special or spec.category is EffectCategory.CLEANSE:
        found.add(Shape.DEED)
    return frozenset(found)


# --- пометки, которые читает бой --------------------------------------
#
# Приём, который нельзя выразить правкой чисел, оставляет пометку, и её читает
# сам бой. Пометок нарочно мало: всё, что укладывается в числа, лежит в числах.

#: Умение стоит вдвое дешевле. Читается там же, где считается цена хода.
MARK_CHEAP = "mastery_cheap"
#: Добив цель, умение возвращается сразу: откат снимается целиком.
MARK_RUSH = "mastery_rush"

#: Во сколько раз «лёгкая рука» сбивает цену умения.
CHEAP_FACTOR = 0.5

#: Сроки, которые приёмы держат в одном месте: менять их поштучно в полусотне
#: строк - это менять их врозь.
SHORT_TURNS = 2
HELD_TURNS = 3
LONG_TURNS = 4


# --- как приём правит описание ----------------------------------------


def _kept(spec: EffectSpec, turns: int) -> EffectSpec:
    """Продлить общий срок умения до нужного: прибавка без срока не ложится.

    ``_apply_modifier_bundles`` читает ``self_modifiers`` и ``target_modifiers``
    только вместе с ``duration``, и приём, забывший про срок, не сделал бы
    ничего.
    """
    return replace(spec, duration=max(spec.duration, turns))


def _self_mod(spec: EffectSpec, key: str, value: float, turns: int = HELD_TURNS) -> EffectSpec:
    """Прибавка себе. Ту, что умение и так даёт, приём не трогает.

    Бой складывает прибавки В СЛОВАРЬ по ключу (``_apply_modifier_bundles``):
    вторая прибавка с тем же ключом не сложилась бы с первой, а затёрла бы её, -
    и выучка, обещавшая прибавить брони, отняла бы у умения его собственную.
    Такая выучка не проходит проверку загрузчика: описание после неё не
    изменилось.
    """
    if any(one.key == key for one in spec.self_modifiers):
        return spec
    return _kept(
        replace(spec, self_modifiers=(*spec.self_modifiers, ModifierSpec(key, value))), turns
    )


def _target_mod(spec: EffectSpec, key: str, value: float, turns: int = SHORT_TURNS) -> EffectSpec:
    """То же для штрафа цели, и по той же причине."""
    if any(one.key == key for one in spec.target_modifiers):
        return spec
    return _kept(
        replace(spec, target_modifiers=(*spec.target_modifiers, ModifierSpec(key, value))), turns
    )


def _inflicted(spec: EffectSpec, one: Inflict) -> EffectSpec:
    """Повесить состояние на цель. Уже висящее не вешают дважды.

    Второе «Немоты» на умении, которое и так затыкает, - это подпись: в бою
    видно одно молчание, а не два. Такая выучка не проходит проверку загрузчика,
    потому что описание после неё не изменилось.
    """
    if any(held.kind is one.kind for held in spec.inflicts):
        return spec
    return replace(spec, inflicts=(*spec.inflicts, one))


def _holding(spec: EffectSpec, one: Inflict) -> EffectSpec:
    """То же для состояния, которое умение вешает на своего."""
    if any(held.kind is one.kind for held in spec.holds):
        return spec
    return replace(spec, holds=(*spec.holds, one))


def _marked(spec: EffectSpec, mark: str) -> EffectSpec:
    if mark in spec.marks:
        return spec
    return replace(spec, marks=frozenset({*spec.marks, mark}))


def _dotted(spec: EffectSpec, kind: StatusKind) -> EffectSpec:
    """Оставить на цели то, что точит её каждый ход.

    Умению, которое уже точит, этот приём не годится: второго дота на одном
    ударе не бывает, и загрузчик такую выучку не примет.
    """
    if spec.dot_turns:
        return spec
    return replace(spec, dot_turns=HELD_TURNS, dot_status=kind)


def _held_longer(held: tuple[Inflict, ...], turns: int) -> tuple[Inflict, ...]:
    """Продлить наложенное - всё, кроме того, что отнимает ход.

    То же правило, что у ранга (``rules/skills``): лишний ход оглушения бой не
    разменивает, а кончает.
    """
    return tuple(
        one if one.kind in CONTROL_STATUSES else replace(one, turns=one.turns + turns)
        for one in held
    )


def _linger(spec: EffectSpec, turns: float) -> EffectSpec:
    count = int(turns)
    return replace(
        spec,
        duration=spec.duration + count if spec.duration else 0,
        dot_turns=spec.dot_turns + count if spec.dot_turns else 0,
        barrier_turns=spec.barrier_turns + count if spec.barrier_turns else 0,
        inflicts=_held_longer(spec.inflicts, count),
        holds=_held_longer(spec.holds, count),
    )


# --- приём ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Way:
    """Приём: одна механика, из которой собирают выучки.

    ``change`` - вся его суть: правка описания эффекта, по которому бой и
    считает. ``words`` пишет по ней текст, и другого текста у выучки нет.
    ``low``/``high`` - границы размера; ``None`` у обоих значит, что размера у
    приёма нет вовсе («удар всегда критический» не бывает на сорок процентов).
    """

    code: str
    tier: int
    shapes: frozenset[Shape]
    words: Callable[[float], str]
    change: Callable[[EffectSpec, float], EffectSpec]
    low: float | None = None
    high: float | None = None

    @property
    def sized(self) -> bool:
        return self.low is not None

    def suits(self, spec: EffectSpec) -> bool:
        return bool(self.shapes & shapes_of(spec))

    def fits(self, amount: float | None) -> bool:
        """Годится ли этот размер приёму."""
        if not self.sized:
            return amount is None
        if amount is None:
            return False
        assert self.low is not None and self.high is not None
        return self.low <= amount <= self.high


_STRIKING = frozenset({Shape.STRIKE, Shape.SWEEP})
_HURTING = frozenset({Shape.STRIKE, Shape.SWEEP, Shape.BANE})
_HOLDING = frozenset({Shape.OVER_TIME, Shape.BANE, Shape.BOON})
_KEEPING = frozenset({Shape.MENDING, Shape.WARD, Shape.BOON, Shape.DEED})
_ANY = frozenset(Shape)


def _n(amount: float) -> str:
    """Число словами игрока: без хвоста, если он нулевой."""
    return f"{amount:g}"


def _turns(amount: float) -> str:
    count = int(amount)
    if count == 1:
        return "1 ход"
    return f"{count} хода" if count < 5 else f"{count} ходов"


#: Все приёмы игры. Ступень первая правит числа, вторая меняет форму.
WAYS: tuple[Way, ...] = (
    # ============ ступень первая: третий ранг ============
    Way(
        code="pierce",
        tier=1,
        shapes=_STRIKING,
        low=20,
        high=60,
        words=lambda a: f"Удар не считает {_n(a)} процентов брони цели.",
        change=lambda spec, a: replace(spec, pierce=min(1.0, spec.pierce + a / 100.0)),
    ),
    Way(
        code="sure",
        tier=1,
        shapes=_STRIKING,
        words=lambda a: "От удара нельзя уклониться: цель его принимает или отвечает.",
        change=lambda spec, a: replace(spec, always_hits=True),
    ),
    Way(
        code="drain",
        tier=1,
        shapes=_STRIKING,
        low=10,
        high=35,
        words=lambda a: f"{_n(a)} процентов нанесённого возвращаются вам здоровьем.",
        change=lambda spec, a: replace(spec, lifesteal=spec.lifesteal + a / 100.0),
    ),
    Way(
        code="edge",
        tier=1,
        shapes=_STRIKING,
        low=8,
        high=30,
        words=lambda a: f"Крит этим умением случается на {_n(a)} процентов чаще.",
        change=lambda spec, a: replace(spec, crit_bonus=spec.crit_bonus + a),
    ),
    Way(
        code="heavy",
        tier=1,
        shapes=_STRIKING,
        low=10,
        high=35,
        words=lambda a: f"Удар сильнее на {_n(a)} процентов.",
        change=lambda spec, a: replace(spec, damage_scale=spec.damage_scale * (1.0 + a / 100.0)),
    ),
    Way(
        code="splinter",
        tier=1,
        shapes=frozenset({Shape.STRIKE}),
        low=15,
        high=45,
        words=lambda a: f"Соседу цели достаётся {_n(a)} процентов удара.",
        change=lambda spec, a: replace(spec, splash=spec.splash + a / 100.0),
    ),
    Way(
        code="linger",
        tier=1,
        shapes=_HOLDING,
        low=1,
        high=3,
        words=lambda a: (
            f"Всё, что умение накладывает, держится на {_turns(a)} дольше — "
            "кроме того, что отнимает ход."
        ),
        change=_linger,
    ),
    Way(
        code="deepen",
        tier=1,
        shapes=frozenset({Shape.OVER_TIME}),
        low=25,
        high=80,
        words=lambda a: f"То, что точит цель каждый ход, точит на {_n(a)} процентов сильнее.",
        change=lambda spec, a: replace(spec, dot_scale=spec.dot_scale * (1.0 + a / 100.0)),
    ),
    Way(
        code="cheap",
        tier=1,
        shapes=_ANY,
        words=lambda a: f"Умение стоит вдвое дешевле — {round(CHEAP_FACTOR * 100)} процентов цены.",
        change=lambda spec, a: _marked(spec, MARK_CHEAP),
    ),
    Way(
        code="guarded",
        tier=1,
        shapes=_ANY,
        low=5,
        high=18,
        words=lambda a: f"Вдобавок ставит вам барьер на {_n(a)} процентов здоровья.",
        change=lambda spec, a: replace(spec, bonus_barrier=spec.bonus_barrier + a),
    ),
    Way(
        code="mending",
        tier=1,
        shapes=_ANY,
        low=5,
        high=18,
        words=lambda a: f"Вдобавок лечит вас на {_n(a)} процентов здоровья.",
        change=lambda spec, a: replace(spec, bonus_heal=spec.bonus_heal + a),
    ),
    Way(
        code="swift",
        tier=1,
        shapes=_ANY,
        low=20,
        high=55,
        words=lambda a: (
            f"{_turns(SHORT_TURNS)} ваша инициатива выше на {_n(a)} процентов: вы ходите раньше."
        ),
        change=lambda spec, a: _self_mod(spec, "initiative_percent", a, SHORT_TURNS),
    ),
    Way(
        code="bolster",
        tier=1,
        shapes=_ANY,
        low=10,
        high=30,
        words=lambda a: f"{_turns(HELD_TURNS)} ваш урон выше на {_n(a)} процентов.",
        change=lambda spec, a: _self_mod(spec, "damage_percent", a),
    ),
    Way(
        code="steel",
        tier=1,
        shapes=_ANY,
        low=10,
        high=35,
        words=lambda a: f"{_turns(HELD_TURNS)} ваша броня выше на {_n(a)} процентов.",
        change=lambda spec, a: _self_mod(spec, "armor_percent", a),
    ),
    Way(
        code="sidestep",
        tier=1,
        shapes=_ANY,
        low=10,
        high=30,
        words=lambda a: f"{_turns(HELD_TURNS)} вы уклоняетесь на {_n(a)} процентов чаще.",
        change=lambda spec, a: _self_mod(spec, "dodge_percent", a),
    ),
    Way(
        code="dull",
        tier=1,
        shapes=_HURTING,
        low=15,
        high=40,
        words=lambda a: (
            f"{_turns(SHORT_TURNS)} цель промахивается чаще: точность ниже на {_n(a)} процентов."
        ),
        change=lambda spec, a: _target_mod(spec, "accuracy_percent", -a),
    ),
    Way(
        code="sap",
        tier=1,
        shapes=_HURTING,
        low=15,
        high=35,
        words=lambda a: f"{_turns(SHORT_TURNS)} цель бьёт слабее на {_n(a)} процентов.",
        change=lambda spec, a: _target_mod(spec, "damage_percent", -a),
    ),
    Way(
        code="drag",
        tier=1,
        shapes=_HURTING,
        low=15,
        high=45,
        words=lambda a: (
            f"{_turns(SHORT_TURNS)} цель ходит позже: инициатива ниже на {_n(a)} процентов."
        ),
        change=lambda spec, a: _target_mod(spec, "initiative_percent", -a),
    ),
    Way(
        code="ward_long",
        tier=1,
        shapes=frozenset({Shape.WARD}),
        low=1,
        high=3,
        words=lambda a: f"Барьер держится на {_turns(a)} дольше.",
        change=lambda spec, a: replace(spec, barrier_turns=spec.barrier_turns + int(a)),
    ),
    Way(
        code="soothe",
        tier=1,
        shapes=_KEEPING,
        low=2,
        high=4,
        words=lambda a: f"{_turns(a)} здоровье прибывает само.",
        change=lambda spec, a: _holding(spec, Inflict(kind=StatusKind.HEALTH_REGEN, turns=int(a))),
    ),
    Way(
        code="refresh",
        tier=1,
        shapes=_KEEPING,
        low=2,
        high=4,
        words=lambda a: f"{_turns(a)} запас восстанавливается быстрее.",
        change=lambda spec, a: _holding(
            spec, Inflict(kind=StatusKind.RESOURCE_REGEN, turns=int(a))
        ),
    ),
    Way(
        code="cleansing",
        tier=1,
        shapes=frozenset({Shape.MENDING, Shape.WARD}),
        low=1,
        high=3,
        words=lambda a: f"Вдобавок снимает с вас {_n(a)} беды.",
        change=lambda spec, a: replace(spec, cleanse_count=spec.cleanse_count + int(a)),
    ),
    # ============ ступень вторая: пятый ранг ============
    Way(
        code="wide",
        tier=2,
        shapes=frozenset({Shape.STRIKE}),
        low=40,
        high=75,
        words=lambda a: f"Бьёт всю стаю разом, но каждого — на {_n(a)} процентов удара.",
        change=lambda spec, a: replace(spec, aoe=True, damage_scale=spec.damage_scale * a / 100.0),
    ),
    Way(
        code="focus",
        tier=2,
        shapes=frozenset({Shape.SWEEP}),
        low=30,
        high=70,
        words=lambda a: f"Бьёт одну цель вместо всех, зато сильнее на {_n(a)} процентов.",
        change=lambda spec, a: replace(
            spec, aoe=False, damage_scale=spec.damage_scale * (1.0 + a / 100.0)
        ),
    ),
    Way(
        code="double",
        tier=2,
        shapes=frozenset({Shape.STRIKE}),
        low=55,
        high=75,
        words=lambda a: f"Бьёт лишний раз, и каждый удар — {_n(a)} процентов прежнего.",
        change=lambda spec, a: replace(
            spec, hits=spec.hits + 1, damage_scale=spec.damage_scale * a / 100.0
        ),
    ),
    Way(
        code="finish",
        tier=2,
        shapes=_STRIKING,
        low=40,
        high=90,
        words=lambda a: (
            f"Чем меньше здоровья у цели, тем сильнее удар: у почти павшей — на {_n(a)} процентов."
        ),
        change=lambda spec, a: replace(spec, execute_scaling=spec.execute_scaling + a / 100.0),
    ),
    Way(
        code="certain",
        tier=2,
        shapes=frozenset({Shape.STRIKE}),
        words=lambda a: "Удар всегда критический.",
        change=lambda spec, a: replace(spec, guaranteed_crit=True),
    ),
    Way(
        code="rush",
        tier=2,
        shapes=_STRIKING,
        words=lambda a: "Добив цель, умение возвращается сразу: отката не будет.",
        change=lambda spec, a: _marked(spec, MARK_RUSH),
    ),
    Way(
        code="spread",
        tier=2,
        shapes=_KEEPING,
        words=lambda a: "Ложится на весь отряд, а не на вас одного.",
        change=lambda spec, a: spec if spec.aoe else replace(spec, aoe=True),
    ),
    Way(
        code="contagion",
        tier=2,
        shapes=frozenset({Shape.OVER_TIME, Shape.BANE}),
        words=lambda a: "Ложится на всю стаю, а не на одну цель.",
        change=lambda spec, a: spec if spec.aoe else replace(spec, aoe=True),
    ),
    Way(
        code="shatter",
        tier=2,
        shapes=_HURTING,
        low=15,
        high=45,
        words=lambda a: f"Вдобавок ломает цели броню на {_n(a)} процентов, {_turns(HELD_TURNS)}.",
        change=lambda spec, a: _target_mod(spec, "armor_percent", -a, HELD_TURNS),
    ),
    Way(
        code="mark",
        tier=2,
        shapes=_HURTING,
        low=15,
        high=40,
        words=lambda a: (
            f"Вдобавок цель принимает на {_n(a)} процентов больше урона, {_turns(HELD_TURNS)} — "
            "от вас и от ваших."
        ),
        change=lambda spec, a: _target_mod(spec, "damage_taken_percent", a, HELD_TURNS),
    ),
    Way(
        code="stagger",
        tier=2,
        shapes=_STRIKING,
        words=lambda a: "Попавший удар отнимает у цели ход.",
        change=lambda spec, a: replace(spec, stun_turns=spec.stun_turns + 1),
    ),
    Way(
        code="quiet",
        tier=2,
        shapes=frozenset({Shape.STRIKE, Shape.SWEEP, Shape.BANE, Shape.BINDING}),
        words=lambda a: f"Вдобавок затыкает цель на {_turns(SHORT_TURNS)}: умений ей не нажать.",
        change=lambda spec, a: _inflicted(
            spec, Inflict(kind=StatusKind.SILENCE, turns=SHORT_TURNS)
        ),
    ),
    Way(
        code="seal",
        tier=2,
        shapes=_HURTING,
        words=lambda a: f"Вдобавок {_turns(HELD_TURNS)} цель нельзя вылечить.",
        change=lambda spec, a: _inflicted(
            spec, Inflict(kind=StatusKind.HEAL_BLOCK, turns=HELD_TURNS)
        ),
    ),
    Way(
        code="hush",
        tier=2,
        shapes=_HURTING,
        words=lambda a: f"Вдобавок {_turns(HELD_TURNS)} цели нечем платить за умения.",
        change=lambda spec, a: _inflicted(
            spec, Inflict(kind=StatusKind.RESOURCE_BLOCK, turns=HELD_TURNS)
        ),
    ),
    Way(
        code="terrify",
        tier=2,
        shapes=_HURTING,
        words=lambda a: "Вдобавок цель на ход теряет голову от страха.",
        change=lambda spec, a: _inflicted(spec, Inflict(kind=StatusKind.FEAR, turns=1)),
    ),
    Way(
        code="frost",
        tier=2,
        shapes=_HURTING,
        words=lambda a: "Вдобавок цель на ход схватывает морозом.",
        change=lambda spec, a: _inflicted(spec, Inflict(kind=StatusKind.FREEZE, turns=1)),
    ),
    Way(
        code="chill",
        tier=2,
        shapes=_HURTING,
        low=20,
        high=45,
        words=lambda a: (
            f"Вдобавок {_turns(SHORT_TURNS)} цель медлит: инициатива ниже на {_n(a)} процентов."
        ),
        change=lambda spec, a: _inflicted(
            spec, Inflict(kind=StatusKind.SLOW, turns=SHORT_TURNS, value=a)
        ),
    ),
    Way(
        code="wither",
        tier=2,
        shapes=_HURTING,
        low=20,
        high=45,
        words=lambda a: f"Вдобавок {_turns(HELD_TURNS)} цель бьёт слабее на {_n(a)} процентов.",
        change=lambda spec, a: _inflicted(
            spec, Inflict(kind=StatusKind.WEAKNESS, turns=HELD_TURNS, value=a)
        ),
    ),
    Way(
        code="taunting",
        tier=2,
        shapes=_HURTING,
        words=lambda a: f"Вдобавок {_turns(SHORT_TURNS)} цель бьёт по вам, а не по вашим.",
        change=lambda spec, a: _kept(
            _inflicted(spec, Inflict(kind=StatusKind.TAUNT, turns=SHORT_TURNS)), SHORT_TURNS
        ),
    ),
    Way(
        code="bleed",
        tier=2,
        shapes=_STRIKING,
        words=lambda a: f"Цель истекает кровью {_turns(HELD_TURNS)}.",
        change=lambda spec, a: _dotted(spec, StatusKind.BLEEDING),
    ),
    Way(
        code="sear",
        tier=2,
        shapes=_STRIKING,
        words=lambda a: f"Цель горит {_turns(HELD_TURNS)}.",
        change=lambda spec, a: _dotted(spec, StatusKind.BURNING),
    ),
    Way(
        code="taint",
        tier=2,
        shapes=_STRIKING,
        words=lambda a: f"Цель травится {_turns(HELD_TURNS)}.",
        change=lambda spec, a: _dotted(spec, StatusKind.POISON),
    ),
    Way(
        code="hard_guard",
        tier=2,
        shapes=_ANY,
        low=18,
        high=40,
        words=lambda a: (
            f"Барьер вдобавок — {_n(a)} процентов здоровья, и держится он {_turns(LONG_TURNS)}."
        ),
        change=lambda spec, a: replace(
            spec,
            bonus_barrier=spec.bonus_barrier + a,
            barrier_turns=max(spec.barrier_turns, LONG_TURNS),
        ),
    ),
    Way(
        code="counter",
        tier=2,
        shapes=_ANY,
        low=20,
        high=50,
        words=lambda a: (
            f"{_turns(HELD_TURNS)} вы отвечаете ударившему на {_n(a)} процентов своего удара."
        ),
        change=lambda spec, a: _self_mod(spec, COUNTER, a),
    ),
    Way(
        code="reflect",
        tier=2,
        shapes=_ANY,
        low=15,
        high=40,
        words=lambda a: (
            f"{_turns(HELD_TURNS)} {_n(a)} процентов полученного возвращается ударившему."
        ),
        change=lambda spec, a: _self_mod(spec, "reflect_percent", a),
    ),
    Way(
        code="unfallen",
        tier=2,
        shapes=_KEEPING,
        words=lambda a: f"{_turns(SHORT_TURNS)} вы не падаете: здоровье не опустится ниже единицы.",
        change=lambda spec, a: _self_mod(spec, UNDYING, 1.0, SHORT_TURNS),
    ),
    Way(
        code="steadfast",
        tier=2,
        shapes=_KEEPING,
        words=lambda a: f"{_turns(HELD_TURNS)} ход у вас не отнять ничем.",
        change=lambda spec, a: _self_mod(spec, UNSTUNNABLE, 1.0),
    ),
    Way(
        code="haste",
        tier=2,
        shapes=_KEEPING,
        words=lambda a: f"Вдобавок {_turns(HELD_TURNS)} вы разогнаны: ходите чаще.",
        change=lambda spec, a: _holding(spec, Inflict(kind=StatusKind.HASTE, turns=HELD_TURNS)),
    ),
    Way(
        code="empower",
        tier=2,
        shapes=_KEEPING,
        words=lambda a: f"Вдобавок {_turns(HELD_TURNS)} ваши удары усилены.",
        change=lambda spec, a: _holding(spec, Inflict(kind=StatusKind.EMPOWER, turns=HELD_TURNS)),
    ),
    Way(
        code="vanish",
        tier=2,
        shapes=frozenset({Shape.BOON, Shape.DEED}),
        words=lambda a: (
            f"Вдобавок вы уходите из виду на {_turns(SHORT_TURNS)}: "
            "целью вас не выбрать, пока вы сами не ударите."
        ),
        change=lambda spec, a: _holding(spec, Inflict(kind=StatusKind.UNSEEN, turns=SHORT_TURNS)),
    ),
)

_BY_WAY: dict[str, Way] = {one.code: one for one in WAYS}


def way_of(code: str) -> Way | None:
    """Приём по коду. ``None`` - такого в игре нет."""
    return _BY_WAY.get(code)


def words(mastery: SkillMastery) -> str:
    """Что эта выучка делает, словами игрока. Пишет их сам приём по своим числам."""
    way = way_of(mastery.way)
    if way is None:  # pragma: no cover - загрузчик такого не пропускает
        return ""
    return way.words(mastery.amount if mastery.amount is not None else 0.0)


def changed(mastery: SkillMastery, spec: EffectSpec) -> EffectSpec:
    """Описание эффекта так, как его переделала эта выучка."""
    way = way_of(mastery.way)
    if way is None:  # pragma: no cover - загрузчик такого не пропускает
        return spec
    return way.change(spec, mastery.amount if mastery.amount is not None else 0.0)


# --- выучки одного умения ---------------------------------------------


def rank_of_tier(tier: int) -> int:
    """На каком ранге выбирают эту ступень."""
    return MASTERY_RANKS[tier - 1]


def offered(skill: Skill, tier: int) -> tuple[SkillMastery, ...]:
    """Выучки этой ступени, написанные ЭТОМУ умению."""
    return tuple(one for one in skill.masteries if one.tier == tier)


def known(skill: Skill, codes: Iterable[str]) -> tuple[SkillMastery, ...]:
    """Выучки, которые у этого умения есть. Выбор переживает содержимое (правило 8).

    Порядок - тот, в котором выучки написаны умению, а не тот, в котором их
    брали: иначе одно и то же умение считалось бы по-разному у двух игроков,
    взявших одно и то же.
    """
    taken = set(codes)
    return tuple(one for one in skill.masteries if one.code in taken)


def chosen_tiers(skill: Skill, codes: Iterable[str]) -> frozenset[int]:
    """Ступени, на которых уже выбрано."""
    return frozenset(one.tier for one in known(skill, codes))


def pending_tier(skill: Skill, rank: int, codes: Iterable[str]) -> int | None:
    """Ступень, которую игроку пора выбрать этому умению. ``None`` - нечего.

    Ступень «пора», когда ранг до неё дорос, выбор ещё не сделан и умению
    вообще есть что предложить: умение, которому этой ступени не написали, не
    должно ждать выбора, которого нет.
    """
    taken = chosen_tiers(skill, codes)
    for tier in range(1, len(MASTERY_RANKS) + 1):
        if tier in taken or rank < rank_of_tier(tier):
            continue
        if offered(skill, tier):
            return tier
    return None


def applied(skill: Skill, spec: EffectSpec, codes: Iterable[str]) -> EffectSpec:
    """Описание эффекта так, как его переделали выбранные выучки."""
    working = spec
    for one in known(skill, codes):
        working = changed(one, working)
    return working


def cost_factor(spec: EffectSpec) -> float:
    """Во сколько раз выучка сбивает цену умения. Читается по пометке."""
    return CHEAP_FACTOR if MARK_CHEAP in spec.marks else 1.0
