"""Проверка каталога талантов до запуска игры."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from mmorpg.domain.entities.content import CharacterClass, Skill, Talent
from mmorpg.domain.entities.statuses import CONTROL_STATUSES, StatusKind
from mmorpg.domain.rules.class_tactics import CONDITIONS
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS
from mmorpg.domain.rules.skill_effects import spec_for
from mmorpg.domain.rules.talents import EFFECTS, LEVELS


def parse_talents(
    raw: Mapping[str, Any],
    classes: Sequence[CharacterClass],
    skills: Sequence[Skill],
    problems: list[str],
) -> tuple[Talent, ...]:
    parsed: list[Talent] = []
    codes: set[str] = set()
    names: set[str] = set()
    class_ids = {one.id for one in classes}
    limits = {"power": 25.0, "cost": 25.0, "cooldown": 1.0, "duration": 1.0, "modifier": 25.0}
    for entry in raw.get("talent", ()):
        code = str(entry.get("code", ""))
        try:
            one = Talent(
                code=code,
                name=str(entry["name"]),
                class_id=str(entry["class"]),
                level=int(entry["level"]),
                effect=str(entry["effect"]),
                amount=float(entry["amount"]),
                category=str(entry.get("category", "all")),
                modifier=str(entry.get("modifier", "")),
                condition=str(entry.get("condition", "always")),
                statuses=tuple(StatusKind(status) for status in entry.get("statuses", ())),
                threshold=float(entry.get("threshold", 0)),
            )
        except KeyError, ValueError, TypeError:
            problems.append(f"talents.toml: invalid talent {code}")
            continue
        bad = []
        if not code or code in codes or not one.name or one.name in names or len(one.name) > 64:
            bad.append("empty or repeated code/name, or long name")
        codes.add(code)
        names.add(one.name)
        if one.class_id not in class_ids or one.level not in LEVELS:
            bad.append("unknown class or level")
        if (
            one.effect not in EFFECTS
            or not math.isfinite(one.amount)
            or not 0 < one.amount <= limits.get(one.effect, 0)
        ):
            bad.append("unknown effect or amount outside limits")
        if one.category not in {
            "all",
            "damage",
            "heal",
            "barrier",
            "buff",
            "debuff",
            "cleanse",
            "special",
        }:
            bad.append("unknown category")
        if one.condition not in {"always", *CONDITIONS}:
            bad.append("unknown condition")
        if one.condition != "always" and one.effect != "power":
            bad.append("only power supports conditions")
        if one.condition.endswith("status") != bool(one.statuses):
            bad.append("statuses must match a status condition")
        if not math.isfinite(one.threshold) or (
            one.condition.endswith("health_below") and not 0 < one.threshold <= 100
        ):
            bad.append("invalid health threshold")
        if one.effect == "modifier":
            if one.modifier not in EFFECTIVE_KEYS or one.category != "all":
                bad.append("ineffective modifier")
        elif one.modifier:
            bad.append("modifier on a different effect")
        suited = [
            s
            for s in skills
            if s.owner == f"class:{one.class_id}"
            and s.is_active
            and (one.category == "all" or spec_for(s.effect).category.value == one.category)
        ]
        if one.effect != "modifier" and not suited:
            bad.append("class has no matching skill")
        if one.effect == "power" and one.category not in {"damage", "heal", "barrier"}:
            bad.append("power must name a numeric primary category")
        if one.effect == "cooldown" and not any(s.cooldown > 1 for s in suited):
            bad.append("cooldown changes no skill")
        if one.effect == "cost" and not any(s.cost > 0 for s in suited):
            bad.append("cost changes no skill")
        if one.effect == "duration" and not any(
            spec_for(s.effect).duration
            or spec_for(s.effect).dot_turns
            or spec_for(s.effect).barrier_turns
            or any(
                i.kind not in CONTROL_STATUSES
                for i in (*spec_for(s.effect).inflicts, *spec_for(s.effect).holds)
            )
            for s in suited
        ):
            bad.append("duration changes no skill")
        if one.effect in {"cooldown", "duration"} and not one.amount.is_integer():
            bad.append("turn count must be an integer")
        if bad:
            problems.append(f"talents.toml: {code}: {', '.join(bad)}")
        parsed.append(one)
    for class_id in class_ids:
        for level in LEVELS:
            row = [one for one in parsed if one.class_id == class_id and one.level == level]
            if len(row) != 3:
                problems.append(f"talents.toml: {class_id} level {level} must have three choices")
    return tuple(parsed)
