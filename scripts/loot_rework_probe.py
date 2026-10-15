"""Повторяемая выборка добычи; не подключается к службам или данным игроков."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tries", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.tries < 1:
        parser.error("--tries must be positive")
    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "src"))
    from mmorpg.domain.entities.location import EnemyRank
    from mmorpg.domain.procgen import items
    from mmorpg.infrastructure.content import load_content

    content = load_content(root / "content")
    rows = []
    for klass in content.classes:
        for level in (1, 12, 46, 76, 150):
            source = random.Random(args.seed)
            own = 0
            with_affixes = 0
            primary = 0
            named = 0
            named_own = 0
            slots: Counter[str] = Counter()
            for _ in range(args.tries):
                found = items.roll_drop(
                    content, source, level=level, rank=EnemyRank.BOSS, class_ids=(klass.id,)
                )
                assert found is not None
                made = content.item(found)
                compatible = (
                    klass.can_wield(made.weapon_type)
                    if made.weapon_type
                    else klass.can_wear(made.armor_type)
                    if made.armor_type
                    else not made.is_tool
                )
                if not compatible:
                    continue
                own += 1
                slots[made.slot] += 1
                if content.rarity(made.rarity).affixes:
                    with_affixes += 1
                    primary += klass.key_stats[0].value in made.stat_bonuses
                if made.class_affix_id:
                    named += 1
                    named_own += (
                        next(
                            one for one in content.class_affixes if one.id == made.class_affix_id
                        ).class_id
                        == klass.id
                    )
            rows.append(
                {
                    "class": klass.id,
                    "level": level,
                    "attempts": args.tries,
                    "compatible": own,
                    "compatible_percent": round(100 * own / args.tries, 2),
                    "noncommon_compatible": with_affixes,
                    "primary_stat": primary,
                    "primary_stat_percent": round(100 * primary / max(1, with_affixes), 2),
                    "named": named,
                    "named_own": named_own,
                    "slots": dict(sorted(slots.items())),
                }
            )
    result = json.dumps({"seed": args.seed, "rows": rows}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)


if __name__ == "__main__":
    main()
