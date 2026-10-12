"""Выбор талантов и их общие правила. Время и сохранение приходят извне."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import Combatant
from mmorpg.domain.entities.content import GameContent, OwnerKind, Skill, Talent
from mmorpg.domain.entities.statuses import CONTROL_STATUSES
from mmorpg.domain.rules.skill_effects import EffectSpec

LEVELS = (10, 30, 60, 100, 150)
EFFECTS = frozenset({"power", "cost", "cooldown", "duration", "modifier"})
MAX_POWER_BONUS = 60.0
TREE_POWER_BONUS = 80.0
TREE_VERSION = 1
FIRST_POINT_LEVEL = 10
POINT_INTERVAL = 5


def pool(content: GameContent, character: Character) -> tuple[Talent, ...]:
    return tuple(one for one in content.talents if one.class_id == character.class_id)


def points(character: Character) -> int:
    return max(0, (min(character.level, 150) - FIRST_POINT_LEVEL) // POINT_INTERVAL + 1)


def legacy_build(character: Character) -> bool:
    return character.loadout.talent_tree_version == 0 and bool(character.loadout.talents)


def ranks(content: GameContent, character: Character) -> dict[str, int]:
    """Проверяем весь граф, включая повреждённые и прежние сохранения."""
    if character.loadout.talent_tree_version != TREE_VERSION:
        return {}
    available = {one.code: one for one in pool(content, character) if one.branch}
    taken = {
        code: rank
        for code, rank in character.loadout.talent_ranks.items()
        if code in available
        and type(rank) is int
        and 0 < rank <= available[code].max_rank
        and available[code].level <= character.level
    }
    if sum(taken.values()) > points(character):
        return {}
    # Выборы одной развилки несовместимы даже в повреждённом JSON.
    groups: dict[str, list[str]] = {}
    for code in taken:
        if group := available[code].choice_group:
            groups.setdefault(group, []).append(code)
    for codes in groups.values():
        if len(codes) > 1:
            for code in codes:
                del taken[code]
    while True:
        invalid = []
        for code in taken:
            one = available[code]
            full = {key for key, rank in taken.items() if rank == available[key].max_rank}
            earlier = sum(
                rank
                for key, rank in taken.items()
                if available[key].branch == one.branch and available[key].row < one.row
            )
            if (
                not set(one.requires_all) <= full
                or (one.requires_any and not set(one.requires_any) & full)
                or earlier < one.points_required
            ):
                invalid.append(code)
        if not invalid:
            return taken
        for code in invalid:
            del taken[code]


def selected(content: GameContent, character: Character) -> tuple[Talent, ...]:
    if character.loadout.talent_tree_version == TREE_VERSION:
        taken = ranks(content, character)
        return tuple(one for one in pool(content, character) if one.code in taken)
    # Удалённые, чужие, ещё закрытые и повторные записи не дают силу.
    old: dict[int, Talent] = {}
    for one in pool(content, character):
        if (
            (one.legacy or not one.branch)
            and one.code in character.loadout.talents
            and one.level <= character.level
        ):
            old.setdefault(one.level, one)
    return tuple(old.values())


def rank_of(content: GameContent, character: Character, code: str) -> int:
    return ranks(content, character).get(code, 0)


def spent(content: GameContent, character: Character, branch: str = "") -> int:
    taken = ranks(content, character)
    return sum(
        taken.get(one.code, 0)
        for one in pool(content, character)
        if not branch or one.branch == branch
    )


def blocked(content: GameContent, character: Character, one: Talent) -> tuple[str, ...]:
    reasons = []
    if legacy_build(character):
        reasons.append("Сначала подтвердите переход к дереву талантов через /сброситьталанты.")
    if character.level < one.level:
        reasons.append(f"Выбор откроется на уровне {one.level}.")
    taken = ranks(content, character)
    if taken.get(one.code, 0) >= one.max_rank:
        reasons.append("Талант изучен полностью.")
    if sum(taken.values()) >= points(character):
        reasons.append("Нет свободных очков талантов. Следующее очко выдаётся каждые пять уровней.")
    available = {t.code: t for t in pool(content, character)}
    full = {code for code, rank in taken.items() if rank == available[code].max_rank}
    if missing := set(one.requires_all) - full:
        names = ", ".join(available[code].name for code in sorted(missing))
        reasons.append(f"Изучите полностью: {names}.")
    if one.requires_any and not set(one.requires_any) & full:
        names = " или ".join(available[code].name for code in one.requires_any)
        reasons.append(f"Изучите полностью один из талантов: {names}.")
    earlier = sum(
        rank
        for code, rank in taken.items()
        if available[code].branch == one.branch and available[code].row < one.row
    )
    if earlier < one.points_required:
        reasons.append(
            f"В предыдущих рядах этой ветки нужно вложить {one.points_required} очков; "
            f"сейчас вложено {earlier}."
        )
    if one.choice_group and any(
        available[code].choice_group == one.choice_group and code != one.code for code in taken
    ):
        reasons.append("Уже выбран другой талант этой развилки. Для смены сбросьте дерево.")
    return tuple(reasons)


def choose(
    content: GameContent, character: Character, code: str, *, expected_rank: int | None = None
) -> Character | None:
    one = next((one for one in pool(content, character) if one.code == code), None)
    if one is None or one.level > character.level:
        return None
    if not one.branch:
        # Начатые бои прежнего выпуска читают прежний каталог и прежние правила.
        if one.code in {old.code for old in selected(content, character)}:
            return None
        kept = tuple(old.code for old in selected(content, character) if old.level != one.level)
        return replace(character, loadout=replace(character.loadout, talents=(*kept, one.code)))
    current = rank_of(content, character, code)
    if blocked(content, character, one) or (expected_rank is not None and expected_rank != current):
        return None
    taken = ranks(content, character)
    taken[code] = current + 1
    return replace(
        character,
        loadout=replace(
            character.loadout,
            talents=(),
            talent_ranks=MappingProxyType(taken),
            talent_tree_version=TREE_VERSION,
        ),
    )


def reset(character: Character) -> Character:
    return replace(
        character,
        loadout=replace(
            character.loadout,
            talents=(),
            talent_ranks=MappingProxyType({}),
            talent_tree_version=TREE_VERSION,
        ),
    )


def strength(content: GameContent, character: Character, one: Talent) -> float:
    # Вызывается только для уже проверенных selected/matching, без повторного обхода графа.
    rank = (
        character.loadout.talent_ranks.get(one.code, 0)
        if character.loadout.talent_tree_version
        else 1
    )
    return one.amount * rank


def modifiers(content: GameContent, character: Character) -> dict[str, float]:
    result: dict[str, float] = {}
    for one in selected(content, character):
        if one.effect == "modifier":
            result[one.modifier] = result.get(one.modifier, 0.0) + strength(content, character, one)
    return result


def matching(content: GameContent, character: Character, skill: Skill) -> tuple[Talent, ...]:
    if skill.owner_kind not in {OwnerKind.CLASS, OwnerKind.SUBCLASS}:
        return ()
    from mmorpg.domain.rules.skill_effects import spec_for

    category = spec_for(skill.effect).category.value
    return tuple(one for one in selected(content, character) if one.category in {"all", category})


def cost_factor(content: GameContent, character: Character, skill: Skill) -> float:
    discount = sum(
        strength(content, character, one)
        for one in matching(content, character, skill)
        if one.effect == "cost"
    )
    return max(0.5, 1.0 - discount / 100.0)


def cooldown(content: GameContent, character: Character, skill: Skill, current: int) -> int:
    cut = sum(
        int(strength(content, character, one))
        for one in matching(content, character, skill)
        if one.effect == "cooldown"
    )
    # Талант не делает сильное умение бесконечно повторяемым без ожидания.
    return max(1, current - cut) if current else 0


def applied(
    content: GameContent, character: Character, skill: Skill, spec: EffectSpec
) -> EffectSpec:
    bonus = min(
        2,
        sum(
            int(strength(content, character, one))
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
        strength(content, character, one)
        for one in matching(content, character, skill)
        if one.effect == "power" and active(one, actor, target)
    )
    limit = TREE_POWER_BONUS if character.loadout.talent_tree_version else MAX_POWER_BONUS
    return 1.0 + min(limit, bonus) / 100.0
