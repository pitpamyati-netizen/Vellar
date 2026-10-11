"""Выбор талантов и их общие правила. Время и сохранение приходят извне."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import Combatant
from mmorpg.domain.entities.content import GameContent, OwnerKind, Skill, Talent
from mmorpg.domain.entities.statuses import CONTROL_STATUSES
from mmorpg.domain.rules.skill_effects import EffectSpec

LEVELS = (10, 30, 60, 100, 150)
EFFECTS = frozenset({"power", "cost", "cooldown", "duration", "modifier"})
MAX_POWER_BONUS = 60.0


def pool(content: GameContent, character: Character) -> tuple[Talent, ...]:
    return tuple(one for one in content.talents if one.class_id == character.class_id)


def selected(content: GameContent, character: Character) -> tuple[Talent, ...]:
    # Удалённые, чужие, ещё закрытые и повторные записи не дают силу.
    taken: dict[int, Talent] = {}
    for one in pool(content, character):
        if one.code in character.loadout.talents and one.level <= character.level:
            taken.setdefault(one.level, one)
    return tuple(taken.values())


def choose(content: GameContent, character: Character, code: str) -> Character | None:
    one = next((one for one in pool(content, character) if one.code == code), None)
    if one is None or one.level > character.level:
        return None
    if one.code in {old.code for old in selected(content, character)}:
        return None
    kept = tuple(old.code for old in selected(content, character) if old.level != one.level)
    return replace(character, loadout=replace(character.loadout, talents=(*kept, one.code)))


def modifiers(content: GameContent, character: Character) -> dict[str, float]:
    result: dict[str, float] = {}
    for one in selected(content, character):
        if one.effect == "modifier":
            result[one.modifier] = result.get(one.modifier, 0.0) + one.amount
    return result


def matching(content: GameContent, character: Character, skill: Skill) -> tuple[Talent, ...]:
    if skill.owner_kind not in {OwnerKind.CLASS, OwnerKind.SUBCLASS}:
        return ()
    from mmorpg.domain.rules.skill_effects import spec_for

    category = spec_for(skill.effect).category.value
    return tuple(one for one in selected(content, character) if one.category in {"all", category})


def cost_factor(content: GameContent, character: Character, skill: Skill) -> float:
    discount = sum(
        one.amount for one in matching(content, character, skill) if one.effect == "cost"
    )
    return max(0.5, 1.0 - discount / 100.0)


def cooldown(content: GameContent, character: Character, skill: Skill, current: int) -> int:
    cut = sum(
        int(one.amount) for one in matching(content, character, skill) if one.effect == "cooldown"
    )
    # Талант не делает сильное умение бесконечно повторяемым без ожидания.
    return max(1, current - cut) if current else 0


def applied(
    content: GameContent, character: Character, skill: Skill, spec: EffectSpec
) -> EffectSpec:
    bonus = min(
        2,
        sum(
            int(one.amount)
            for one in matching(content, character, skill)
            if one.effect == "duration"
        ),
    )
    if not bonus:
        return spec
    return replace(
        spec,
        duration=spec.duration + bonus if spec.duration else 0,
        dot_turns=spec.dot_turns + bonus if spec.dot_turns else 0,
        barrier_turns=spec.barrier_turns + bonus if spec.barrier_turns else 0,
        inflicts=tuple(
            one if one.kind in CONTROL_STATUSES else replace(one, turns=one.turns + bonus)
            for one in spec.inflicts
        ),
        holds=tuple(
            one if one.kind in CONTROL_STATUSES else replace(one, turns=one.turns + bonus)
            for one in spec.holds
        ),
    )


def active(one: Talent, actor: Combatant, target: Combatant) -> bool:
    match one.condition:
        case "always":
            return True
        case "actor_status":
            return any(actor.effects.has(status) for status in one.statuses)
        case "target_status":
            return any(target.effects.has(status) for status in one.statuses)
        case "actor_health_below":
            return actor.health * 100 < actor.max_health * one.threshold
        case "target_health_below":
            return target.health * 100 < target.max_health * one.threshold
        case "actor_barrier":
            return actor.barrier > 0
        case _:
            return False


def power_factor(
    content: GameContent, character: Character, skill: Skill, actor: Combatant, target: Combatant
) -> float:
    bonus = sum(
        one.amount
        for one in matching(content, character, skill)
        if one.effect == "power" and active(one, actor, target)
    )
    return 1.0 + min(MAX_POWER_BONUS, bonus) / 100.0
