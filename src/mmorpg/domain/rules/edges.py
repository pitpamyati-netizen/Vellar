"""Что грань меняет в умении.

Грань выбирают один раз, на третьем ранге, и она - единственная развилка
умения (``docs/skills.md``). Здесь описан словарь, которым грань говорит о себе
в ``skills.toml``, и правило, по которому этот словарь ложится на умение.

Словарь маленький нарочно: грань - это поправка к умению, а не второе умение, и
всё, что она умеет, - сдвинуть одно из чисел, которыми умение уже описано
(``skill_effects.EffectSpec``). Новая грань не требует кода; требует его только
новый **род** поправки - тогда это одно поле здесь и одна строка в ``combat``.
Её текст обязан описывать ровно объявленное (``Claude.md``, правило 7).

Ничего не складывается дважды: грань у умения одна, поэтому поправки не
пересекаются и порядок их наложения не важен.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from mmorpg.domain.entities.content import EdgeEffect
from mmorpg.domain.rules.skill_effects import EffectSpec, ModifierSpec

#: Сколько ходов держится прибавка, которую грань принесла умению, у которого
#: своего срока не было. Два хода — самый короткий срок в игре: грань, дающая
#: уклонение бьющему умению, обязана когда-то кончаться.
DEFAULT_DURATION = 2

#: Потолок доли игнорируемой брони. Единица — «броня не считается вовсе»,
#: и выше единицы у доли смысла нет.
FULL_PIERCE = 1.0


#: Поправка, которой нет: грань без объявленной механики.
NOTHING = EdgeEffect()


def changes_effect(edge: EdgeEffect) -> bool:
    """Меняет ли грань само действие, а не только силу и цену.

    Сила и цена накладываются в другом месте (``rules.skills``), поэтому грань,
    кроме них ничего не трогающая, оставляет описание действия как есть.
    """
    return edge != replace(EdgeEffect(), power=edge.power, cost=edge.cost)


def power_factor(edge: EdgeEffect | None) -> float:
    """Во сколько раз грань усиливает умение."""
    return 1.0 if edge is None else 1.0 + edge.power / 100.0


def cost_factor(edge: EdgeEffect | None) -> float:
    """Во сколько раз грань меняет стоимость. Ниже нуля не опускается."""
    if edge is None:
        return 1.0
    return max(0.0, 1.0 + edge.cost / 100.0)


def cooldown_of(base: int, edge: EdgeEffect | None) -> int:
    """Откат с поправкой грани. Ниже нуля откат не бывает."""
    return base if edge is None else max(0, base + edge.cooldown)


def applied(spec: EffectSpec, edge: EdgeEffect | None) -> EffectSpec:
    """Действие умения так, как его переписала грань.

    Возвращается новое описание действия: сами описания неизменяемы и общие для
    всех, у кого это умение есть, поэтому править их на месте нельзя.
    """
    if edge is None or not changes_effect(edge):
        return spec

    hits = spec.hits + edge.hits
    scale = spec.damage_scale * edge.hit_power / 100.0 if edge.hits else spec.damage_scale
    self_modifiers = (*spec.self_modifiers, *_modifiers(edge.self_modifiers))
    target_modifiers = (*spec.target_modifiers, *_modifiers(edge.target_modifiers))
    duration = max(0, spec.duration + edge.duration)
    if duration == 0 and (self_modifiers or target_modifiers):
        # Умение без своего срока, которому грань принесла прибавку: без срока
        # прибавка не наложилась бы вовсе (``combat._apply_modifier_bundles``).
        duration = DEFAULT_DURATION
    return replace(
        spec,
        aoe=spec.aoe or edge.aoe,
        hits=hits,
        damage_scale=scale,
        splash=spec.splash + edge.splash / 100.0,
        pierce=min(FULL_PIERCE, spec.pierce + edge.pierce / 100.0),
        crit_bonus=spec.crit_bonus + edge.crit,
        lifesteal=spec.lifesteal + edge.lifesteal / 100.0,
        dot_turns=spec.dot_turns + edge.dot_turns,
        stun_turns=spec.stun_turns + edge.stun_turns,
        cleanse_count=spec.cleanse_count + edge.cleanse,
        bonus_heal=spec.bonus_heal + edge.heal,
        bonus_barrier=spec.bonus_barrier + edge.barrier,
        duration=duration,
        self_modifiers=self_modifiers,
        target_modifiers=target_modifiers,
    )


def _modifiers(declared: Mapping[str, float]) -> tuple[ModifierSpec, ...]:
    """Объявленные гранью модификаторы — числами, а не долей силы умения.

    Грань называет свою прибавку прямо («ещё 15 процентов уклонения»), потому что
    она и в тексте названа прямо; долей силы умения выражена сама сила.
    """
    return tuple(ModifierSpec(key=key, value=value) for key, value in declared.items())
