"""Класс меняет решение в бою через уже видимые раны, защиту и состояния."""

from __future__ import annotations

from mmorpg.domain.entities.combat import Combatant
from mmorpg.domain.entities.content import ClassTactic
from mmorpg.domain.entities.statuses import status_name

CATEGORIES = frozenset({"damage", "heal", "barrier"})
CONDITIONS = frozenset(
    {"actor_status", "target_status", "actor_health_below", "target_health_below", "actor_barrier"}
)
MAX_BONUS = 25.0


def active(tactic: ClassTactic, actor: Combatant, target: Combatant) -> bool:
    match tactic.condition:
        case "actor_status":
            return any(actor.effects.has(one) for one in tactic.statuses)
        case "target_status":
            return any(target.effects.has(one) for one in tactic.statuses)
        case "actor_health_below":
            return actor.health * 100 < actor.max_health * tactic.threshold
        case "target_health_below":
            return target.health * 100 < target.max_health * tactic.threshold
        case "actor_barrier":
            return actor.barrier > 0
        case _:
            return False


def factor(
    tactics: tuple[ClassTactic, ...], category: str, actor: Combatant, target: Combatant
) -> float:
    """Совпавшие условия берут наибольшую прибавку, а не перемножаются."""
    bonus = max(
        (one.bonus for one in tactics if one.category == category and active(one, actor, target)),
        default=0.0,
    )
    return 1.0 + min(MAX_BONUS, max(0.0, bonus)) / 100.0


def description(tactic: ClassTactic) -> str:
    """Текст строится из тех же условий и чисел, которые исполняет бой."""
    statuses = " или ".join(f"«{status_name(one)}»" for one in tactic.statuses)
    match tactic.condition:
        case "actor_status":
            condition = f"когда на вас есть {statuses}"
        case "target_status":
            condition = f"когда на цели есть {statuses}"
        case "actor_health_below":
            condition = f"когда у вас менее {tactic.threshold:g} процентов здоровья"
        case "target_health_below":
            condition = f"когда у цели менее {tactic.threshold:g} процентов здоровья"
        case _:
            condition = "пока на вас есть барьер"
    action = {
        "damage": "основной урон боевых умений",
        "heal": "основное лечение",
        "barrier": "основные барьеры",
    }[tactic.category]
    return f"{tactic.name}: {action} сильнее на {tactic.bonus:g} процентов, {condition}."
