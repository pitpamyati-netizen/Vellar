"""Воспроизводимое сравнение классов, без обращения к службам или данным игроков."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import replace
from pathlib import Path

from tests.domain.test_combat_balance import build, fight

from mmorpg.domain.entities.character import SkillLoadout
from mmorpg.domain.entities.combat import Verdict
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.infrastructure.content import load_content

LEVELS = (1, 3, 8, 15, 30, 75, 150)
TRIALS = 20


def measure(root: Path) -> list[dict[str, object]]:
    content = load_content(root)
    rows: list[dict[str, object]] = []
    for klass in content.classes:
        for level in LEVELS:
            hero = build(content, klass.id, level)
            selected: list[str] = []
            forks: set[str] = set()
            candidates = [content.skill(code) for code in hero.loadout.actives if code]
            for skill in candidates:
                if skill.fork and skill.fork in forks:
                    continue
                selected.append(skill.code)
                if skill.fork:
                    forks.add(skill.fork)
            hero = replace(
                hero,
                loadout=SkillLoadout(
                    actives=tuple(selected + [None] * (6 - len(selected))),
                    racial=hero.loadout.racial,
                ),
            )
            for rank in EnemyRank:
                results = [fight(content, hero, rank=rank, trial=i) for i in range(TRIALS)]
                outcomes = Counter(one.outcome.value for one in results)
                rows.append(
                    {
                        "class": klass.id,
                        "name": klass.name,
                        "level": level,
                        "enemy": rank.value,
                        "trials": TRIALS,
                        "wins": outcomes[Verdict.VICTORY.value],
                        "median_turns": statistics.median(one.turns for one in results),
                        "max_turns": max(one.turns for one in results),
                        "median_health_spent": round(
                            statistics.median(one.health_spent for one in results), 4
                        ),
                    }
                )
        print(f"Measured {klass.id}", flush=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, default=Path("content"))
    parser.add_argument("--baseline-content", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/class-rework-balance-2026-10-11.json")
    )
    args = parser.parse_args()
    current = measure(args.content)
    baseline = measure(args.baseline_content) if args.baseline_content else []
    report = {
        "schema": 1,
        "seed": "balance (derive: class, level, trial, enemy rank)",
        "levels": LEVELS,
        "trials_per_case": TRIALS,
        "policy": (
            "tests.domain.test_combat_balance.build/fight; "
            "rank-one panel with fork rivals removed; "
            "no specialization or passive investment"
        ),
        "current": current,
        "baseline": baseline,
        "warning": "Simulation, not player telemetry. High-level enemies and losses are retained.",
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Recorded {sum(int(row['trials']) for row in current + baseline)} fights: {args.output}")


if __name__ == "__main__":
    main()
