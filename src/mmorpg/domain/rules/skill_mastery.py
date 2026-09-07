"""Выучка умения: чему игрок научил умение, поднимая его ранг.

Ранг сам по себе прибавляет силу, укорачивает откат, тянет сроки и сбивает цену
(``rules/skills.rank_gain``). Этого мало: четыре числа - это по-прежнему четыре
числа, и очко, вложенное в третий ранг, ничем не отличается от очка, вложенного
во второй. **Ранг обязан дать выбор, а не прибавку** - тогда его хотят поднять.

Поэтому на третьем ранге и на пятом умение спрашивает, ЧЕМУ ОНО НАУЧИЛОСЬ:
игроку предлагаются выучки, подходящие этому умению, и он берёт одну. Две за
жизнь умения, и обе меняют то, что умение делает, а не то, насколько сильно.

ВЫУЧЕК МАЛО, И КАЖДАЯ - МЕХАНИКА. Их девятнадцать на всю игру, а не по своей у
каждого умения: подпись, написанная у каждого из двухсот умений, - это ровно то,
чем были грани, и отменены они были за то, что обещали словами то, чего движок
не делал (ADR 0067). Здесь наоборот: выучка - это правка описания эффекта
(``EffectSpec``), то самое описание, по которому бой и считает, поэтому назвать
выучку словами и не сделать её невозможно.

КОМУ ЧТО ПРЕДЛАГАЮТ. У умения есть форма - бьёт одну цель, бьёт по всем, точит,
лечит, ставит барьер, усиливает, мешает, отнимает ход, - и выучка называет
формы, к которым подходит. «Пробой» не предложат лечению, «Разлив» - удару.
Форма читается из самого описания эффекта, а не пишется в содержимом: умение,
которое бьёт по всем, движок и так знает по ``aoe``.

СТУПЕНЕЙ ДВЕ. На третьем ранге выбирают из первой - те, что правят числа удара;
на пятом из второй - те, что меняют саму форму: одноцелевой удар становится
ударом по всем, лечение ложится на весь отряд, помеха расходится по стае.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from mmorpg.domain.entities.statuses import CONTROL_STATUSES, StatusKind
from mmorpg.domain.rules.skill_effects import (
    UNDYING,
    EffectCategory,
    EffectSpec,
    Inflict,
    ModifierSpec,
)

#: Ранги, на которых умение спрашивает, чему оно научилось. Третий и пятый:
#: первый ранг - это само умение, а на втором и четвёртом игрок ещё копит.
MASTERY_RANKS: tuple[int, ...] = (3, 5)


class Shape(StrEnum):
    """Форма умения - то, по чему видно, какая выучка ему подойдёт."""

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
    и усиление себе. Выучка предлагается, когда сходится хоть одна форма.
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
# Выучка, которую нельзя выразить правкой чисел, оставляет пометку, и её читает
# сам бой. Пометок нарочно мало: всё, что укладывается в числа, лежит в числах.

#: Умение стоит вдвое дешевле. Читается там же, где считается цена хода.
MARK_CHEAP = "mastery_cheap"
#: Добив цель, умение возвращается сразу: откат снимается целиком.
MARK_RUSH = "mastery_rush"

#: Во сколько раз «Лёгкая рука» сбивает цену умения.
CHEAP_FACTOR = 0.5

#: Насколько «Пробой» пробивает броню цели.
PIERCE_SHARE = 0.5
#: Какая доля нанесённого возвращается здоровьем по «Вытяжке».
DRAIN_SHARE = 0.25
#: На сколько ходов «Долгий след» тянет всё, что умение накладывает.
LINGER_TURNS = 2
#: Во сколько раз «Глубокая рана» усиливает то, что точит цель каждый ход.
DEEPEN_SCALE = 1.5
#: Барьер и лечение довеском - проценты от максимума здоровья.
GUARD_SHARE = 10.0
MENDING_SHARE = 10.0
#: Насколько «Скорая рука» поднимает инициативу и на сколько ходов.
SWIFT_INITIATIVE = 40.0
SWIFT_TURNS = 2
#: Что остаётся от силы удара, разошедшегося на всю стаю, и что прибавляется
#: удару, собранному обратно в одну цель.
WIDE_SCALE = 0.6
FOCUS_SCALE = 1.5
#: Насколько «Добой» доводит удар по цели, которой почти не осталось.
FINISH_SCALING = 0.5
#: На сколько «Раскол» ломает броню цели и на сколько ходов.
SHATTER_ARMOR = 25.0
SHATTER_TURNS = 3
#: На сколько ходов «Немота» затыкает цель.
SILENCE_TURNS = 2
#: Сколько ходов держится «Не пасть».
UNDYING_TURNS = 2
#: Барьер «Твёрдого заслона»: проценты здоровья и на сколько ходов.
HARD_GUARD_SHARE = 25.0
HARD_GUARD_TURNS = 4


def _unchanged(spec: EffectSpec) -> EffectSpec:
    return spec


def _marked(spec: EffectSpec, mark: str) -> EffectSpec:
    return replace(spec, marks=frozenset({*spec.marks, mark}))


def _held_longer(held: tuple[Inflict, ...], turns: int) -> tuple[Inflict, ...]:
    """Продлить наложенное - всё, кроме того, что отнимает ход.

    То же правило, что у ранга (``rules/skills``): лишний ход оглушения бой не
    разменивает, а кончает.
    """
    return tuple(
        one if one.kind in CONTROL_STATUSES else replace(one, turns=one.turns + turns)
        for one in held
    )


def _linger(spec: EffectSpec) -> EffectSpec:
    return replace(
        spec,
        duration=spec.duration + LINGER_TURNS if spec.duration else 0,
        dot_turns=spec.dot_turns + LINGER_TURNS if spec.dot_turns else 0,
        barrier_turns=spec.barrier_turns + LINGER_TURNS if spec.barrier_turns else 0,
        inflicts=_held_longer(spec.inflicts, LINGER_TURNS),
        holds=_held_longer(spec.holds, LINGER_TURNS),
    )


def _swift(spec: EffectSpec) -> EffectSpec:
    return replace(
        spec,
        self_modifiers=(
            *spec.self_modifiers,
            ModifierSpec(key="initiative_percent", value=SWIFT_INITIATIVE),
        ),
        duration=max(spec.duration, SWIFT_TURNS),
    )


def _wide(spec: EffectSpec) -> EffectSpec:
    return replace(spec, aoe=True, damage_scale=spec.damage_scale * WIDE_SCALE)


def _focus(spec: EffectSpec) -> EffectSpec:
    return replace(spec, aoe=False, damage_scale=spec.damage_scale * FOCUS_SCALE)


def _shatter(spec: EffectSpec) -> EffectSpec:
    return replace(
        spec,
        target_modifiers=(
            *spec.target_modifiers,
            ModifierSpec(key="armor_percent", value=-SHATTER_ARMOR),
        ),
        duration=max(spec.duration, SHATTER_TURNS),
    )


def _silence(spec: EffectSpec) -> EffectSpec:
    return replace(
        spec,
        inflicts=(*spec.inflicts, Inflict(kind=StatusKind.SILENCE, turns=SILENCE_TURNS)),
    )


def _undying(spec: EffectSpec) -> EffectSpec:
    return replace(
        spec,
        self_modifiers=(*spec.self_modifiers, ModifierSpec(key=UNDYING, value=1.0)),
        duration=max(spec.duration, UNDYING_TURNS),
    )


@dataclass(frozen=True, slots=True)
class Mastery:
    """Одна выучка: чему умение можно научить, взяв ранг.

    ``change`` - вся её механика: правка описания эффекта, по которому бой и
    считает. ``text`` пишется ПО НЕЙ и ровно про неё (``Claude.md``, правило 7).
    """

    code: str
    name: str
    text: str
    tier: int
    shapes: frozenset[Shape]
    change: Callable[[EffectSpec], EffectSpec] = _unchanged

    def suits(self, spec: EffectSpec) -> bool:
        return bool(self.shapes & shapes_of(spec))


_STRIKING = frozenset({Shape.STRIKE, Shape.SWEEP})
_HOLDING = frozenset({Shape.OVER_TIME, Shape.BANE, Shape.BOON})
_KEEPING = frozenset({Shape.MENDING, Shape.WARD, Shape.BOON, Shape.DEED})


#: Все выучки игры. Ступень первая - правит числа удара, вторая - меняет форму.
MASTERIES: tuple[Mastery, ...] = (
    # --- ступень первая: третий ранг ---
    Mastery(
        code="pierce",
        name="Пробой",
        text=f"Удар не считает {round(PIERCE_SHARE * 100)} процентов брони цели.",
        tier=1,
        shapes=_STRIKING,
        change=lambda spec: replace(spec, pierce=min(1.0, spec.pierce + PIERCE_SHARE)),
    ),
    Mastery(
        code="sure",
        name="Наверняка",
        text="От удара нельзя уклониться: цель его принимает или отвечает.",
        tier=1,
        shapes=_STRIKING,
        change=lambda spec: replace(spec, always_hits=True),
    ),
    Mastery(
        code="drain",
        name="Вытяжка",
        text=f"{round(DRAIN_SHARE * 100)} процентов нанесённого возвращаются вам здоровьем.",
        tier=1,
        shapes=_STRIKING,
        change=lambda spec: replace(spec, lifesteal=spec.lifesteal + DRAIN_SHARE),
    ),
    Mastery(
        code="linger",
        name="Долгий след",
        text=(
            f"Всё, что умение накладывает, держится на {LINGER_TURNS} хода дольше — "
            "кроме того, что отнимает ход."
        ),
        tier=1,
        shapes=_HOLDING,
        change=_linger,
    ),
    Mastery(
        code="deepen",
        name="Глубокая рана",
        text=(f"То, что точит цель каждый ход, точит в {DEEPEN_SCALE} раза сильнее."),
        tier=1,
        shapes=frozenset({Shape.OVER_TIME}),
        change=lambda spec: replace(spec, dot_scale=spec.dot_scale * DEEPEN_SCALE),
    ),
    Mastery(
        code="cheap",
        name="Лёгкая рука",
        text=f"Умение стоит вдвое дешевле — {round(CHEAP_FACTOR * 100)} процентов цены.",
        tier=1,
        shapes=frozenset(Shape),
        change=lambda spec: _marked(spec, MARK_CHEAP),
    ),
    Mastery(
        code="guarded",
        name="Прикрытие",
        text=f"Вдобавок ставит вам барьер на {round(GUARD_SHARE)} процентов здоровья.",
        tier=1,
        shapes=frozenset(Shape),
        change=lambda spec: replace(spec, bonus_barrier=spec.bonus_barrier + GUARD_SHARE),
    ),
    Mastery(
        code="swift",
        name="Скорая рука",
        text=(
            f"{SWIFT_TURNS} хода ваша инициатива выше на {round(SWIFT_INITIATIVE)} процентов: "
            "вы ходите раньше."
        ),
        tier=1,
        shapes=frozenset(Shape),
        change=_swift,
    ),
    Mastery(
        code="mending",
        name="Отзыв",
        text=f"Вдобавок лечит вас на {round(MENDING_SHARE)} процентов здоровья.",
        tier=1,
        shapes=frozenset(
            {Shape.STRIKE, Shape.SWEEP, Shape.WARD, Shape.BOON, Shape.BANE, Shape.DEED}
        ),
        change=lambda spec: replace(spec, bonus_heal=spec.bonus_heal + MENDING_SHARE),
    ),
    # --- ступень вторая: пятый ранг ---
    Mastery(
        code="wide",
        name="Размах",
        text=(f"Бьёт всю стаю разом, но каждого — на {round(WIDE_SCALE * 100)} процентов удара."),
        tier=2,
        shapes=frozenset({Shape.STRIKE}),
        change=_wide,
    ),
    Mastery(
        code="focus",
        name="Сосредоточение",
        text=(f"Бьёт одну цель вместо всех, зато в {FOCUS_SCALE} раза сильнее."),
        tier=2,
        shapes=frozenset({Shape.SWEEP}),
        change=_focus,
    ),
    Mastery(
        code="finish",
        name="Добой",
        text="Чем меньше здоровья у цели, тем сильнее удар: у почти павшей — вдвое.",
        tier=2,
        shapes=_STRIKING,
        change=lambda spec: replace(spec, execute_scaling=spec.execute_scaling + FINISH_SCALING),
    ),
    Mastery(
        code="spread",
        name="Разлив",
        text="Ложится на весь отряд, а не на вас одного.",
        tier=2,
        shapes=_KEEPING,
        change=lambda spec: replace(spec, aoe=True),
    ),
    Mastery(
        code="contagion",
        name="Зараза",
        text="Ложится на всю стаю, а не на одну цель.",
        tier=2,
        shapes=frozenset({Shape.OVER_TIME, Shape.BANE}),
        change=lambda spec: replace(spec, aoe=True),
    ),
    Mastery(
        code="shatter",
        name="Раскол",
        text=(
            f"Вдобавок ломает цели броню на {round(SHATTER_ARMOR)} процентов, {SHATTER_TURNS} хода."
        ),
        tier=2,
        shapes=frozenset({Shape.STRIKE, Shape.SWEEP, Shape.BANE}),
        change=_shatter,
    ),
    Mastery(
        code="quiet",
        name="Немота",
        text=f"Вдобавок затыкает цель на {SILENCE_TURNS} хода: умений ей не нажать.",
        tier=2,
        shapes=frozenset({Shape.STRIKE, Shape.SWEEP, Shape.BANE, Shape.BINDING}),
        change=_silence,
    ),
    Mastery(
        code="certain",
        name="Верный удар",
        text="Удар всегда критический.",
        tier=2,
        shapes=frozenset({Shape.STRIKE}),
        change=lambda spec: replace(spec, guaranteed_crit=True),
    ),
    Mastery(
        code="rush",
        name="Второе дыхание",
        text="Добив цель, умение возвращается сразу: отката не будет.",
        tier=2,
        shapes=_STRIKING,
        change=lambda spec: _marked(spec, MARK_RUSH),
    ),
    Mastery(
        code="hard_guard",
        name="Твёрдый заслон",
        text=(
            f"Барьер вдобавок — {round(HARD_GUARD_SHARE)} процентов здоровья, "
            f"и держится он {HARD_GUARD_TURNS} хода."
        ),
        tier=2,
        shapes=frozenset(Shape),
        change=lambda spec: replace(
            spec,
            bonus_barrier=spec.bonus_barrier + HARD_GUARD_SHARE,
            barrier_turns=max(spec.barrier_turns, HARD_GUARD_TURNS),
        ),
    ),
    Mastery(
        code="unfallen",
        name="Не пасть",
        text=f"{UNDYING_TURNS} хода вы не падаете: здоровье не опустится ниже единицы.",
        tier=2,
        shapes=_KEEPING,
        change=_undying,
    ),
)

_BY_CODE: dict[str, Mastery] = {one.code: one for one in MASTERIES}


def known(codes: Iterable[str]) -> tuple[Mastery, ...]:
    """Выучки, которые в игре есть. Выбор переживает содержимое (правило 8)."""
    return tuple(_BY_CODE[code] for code in codes if code in _BY_CODE)


def rank_of_tier(tier: int) -> int:
    """На каком ранге выбирают эту ступень."""
    return MASTERY_RANKS[tier - 1]


def offered(spec: EffectSpec, tier: int) -> tuple[Mastery, ...]:
    """Выучки этой ступени, подходящие умению с таким эффектом."""
    return tuple(one for one in MASTERIES if one.tier == tier and one.suits(spec))


def chosen_tiers(codes: Iterable[str]) -> frozenset[int]:
    """Ступени, на которых уже выбрано."""
    return frozenset(one.tier for one in known(codes))


def pending_tier(spec: EffectSpec, rank: int, codes: Iterable[str]) -> int | None:
    """Ступень, которую игроку пора выбрать этому умению. ``None`` - нечего.

    Ступень «пора», когда ранг до неё дорос, выбор ещё не сделан и умению
    вообще есть что предложить: умение, которому ни одна выучка этой ступени не
    подходит, не должен ждать выбора, которого нет.
    """
    taken = chosen_tiers(codes)
    for tier in range(1, len(MASTERY_RANKS) + 1):
        if tier in taken or rank < rank_of_tier(tier):
            continue
        if offered(spec, tier):
            return tier
    return None


def applied(spec: EffectSpec, codes: Iterable[str]) -> EffectSpec:
    """Описание эффекта так, как его переделали выбранные выучки.

    Порядок - тот, в котором выучки объявлены, а не тот, в котором их брали:
    иначе одно и то же умение считалось бы по-разному у двух игроков, взявших
    одно и то же.
    """
    picked = {one.code for one in known(codes)}
    working = spec
    for one in MASTERIES:
        if one.code in picked:
            working = one.change(working)
    return working


def cost_factor(spec: EffectSpec) -> float:
    """Во сколько раз выучка сбивает цену умения. Читается по пометке."""
    return CHEAP_FACTOR if MARK_CHEAP in spec.marks else 1.0
