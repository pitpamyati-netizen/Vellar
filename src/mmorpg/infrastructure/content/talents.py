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
                branch=str(entry.get("branch", "")),
                branch_name=str(entry.get("branch_name", "")),
                row=int(entry.get("row", 0)),
                max_rank=int(entry.get("max_rank", 1)),
                points_required=int(entry.get("points_required", 0)),
                requires_all=tuple(str(code) for code in entry.get("requires_all", ())),
                requires_any=tuple(str(code) for code in entry.get("requires_any", ())),
                choice_group=str(entry.get("choice_group", "")),
                legacy=bool(entry.get("legacy", False)),
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
            not one.branch
            or not one.branch_name
            or len(one.branch_name) > 48
            or one.row not in range(1, len(LEVELS) + 1)
            or one.level != LEVELS[one.row - 1]
            or not 1 <= one.max_rank <= 3
            or not 0 <= one.points_required <= 29
        ):
            bad.append("invalid branch, row, rank or point gate")
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
    by_code = {one.code: one for one in parsed}
    for one in parsed:
        parents = (*one.requires_all, *one.requires_any)
        if len(set(parents)) != len(parents):
            problems.append(f"talents.toml: {one.code}: repeated prerequisite")
        for code in parents:
            parent = by_code.get(code)
            if (
                parent is None
                or parent.code == one.code
                or parent.class_id != one.class_id
                or parent.branch != one.branch
                or parent.row > one.row
            ):
                problems.append(f"talents.toml: {one.code}: invalid prerequisite {code}")
        if one.effect in {"duration", "cooldown"} and one.max_rank != 1:
            problems.append(f"talents.toml: {one.code}: turn talent must have one rank")
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(code: str) -> None:
        if code in visiting:
            problems.append(f"talents.toml: {code}: prerequisite cycle")
            return
        if code in done or code not in by_code:
            return
        visiting.add(code)
        one = by_code[code]
        for parent in (*one.requires_all, *one.requires_any):
            visit(parent)
        visiting.remove(code)
        done.add(code)

    for code in by_code:
        visit(code)
    for class_id in class_ids:
        owned = [one for one in parsed if one.class_id == class_id]
        branches = {one.branch for one in owned}
        if len(branches) != 3:
            problems.append(f"talents.toml: {class_id}: must have three branches")
        for branch in branches:
            nodes = [one for one in owned if one.branch == branch]
            if len({one.branch_name for one in nodes}) != 1:
                problems.append(f"talents.toml: {class_id} {branch}: inconsistent branch name")
            for row in range(1, 6):
                if len([one for one in nodes if one.row == row]) != 3:
                    problems.append(
                        f"talents.toml: {class_id} {branch} row {row} must have three talents"
                    )
            groups: dict[str, list[Talent]] = {}
            for one in nodes:
                if one.choice_group:
                    groups.setdefault(one.choice_group, []).append(one)
            for group, members in groups.items():
                if len(members) < 2 or len({one.row for one in members}) != 1:
                    problems.append(f"talents.toml: {group}: invalid choice group")
            for one in nodes:
                if one.row == 1 and one.points_required:
                    problems.append(f"talents.toml: {one.code}: first row cannot require points")
                earlier = sum(t.max_rank for t in nodes if t.row < one.row and not t.choice_group)
                earlier += sum(
                    max(t.max_rank for t in members)
                    for members in groups.values()
                    if members[0].row < one.row
                )
                if one.points_required > earlier:
                    problems.append(f"talents.toml: {one.code}: unreachable point gate")
    return tuple(parsed)
