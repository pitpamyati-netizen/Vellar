"""Модель местных противников и объединённых областей; служб и игроков не касается."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from scripts.character_balance_report import developed, simulate, summarized

from mmorpg.domain.entities.location import NodeKind
from mmorpg.domain.procgen.enemies import candidates, generate_enemy, generate_group, role_of
from mmorpg.domain.procgen.seeds import derive
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.flows.play import LocationSession, build_location

SEED = b"world-design-20261016"
LEVELS = (1, 10, 30, 60, 100, 150)


def measure(content):
    samples = defaultdict(list)
    for klass in content.classes:
        for level in LEVELS:
            hero = developed(content, klass.id, level)
            for archetype in content.enemy_archetypes:
                seed = derive(SEED, klass.id, level, archetype.id)
                enemy = generate_enemy(
                    seed,
                    archetypes=(archetype,),
                    biome=archetype.biomes[0],
                    level=level,
                    dungeon=archetype.dungeon,
                )
                result = simulate(content, {hero.id: hero}, (enemy,), seed)
                samples[("species", klass.id, level)].append(result)
        for city in content.cities:
            for location in city.active_locations:
                level = (location.level_min + location.level_max) // 2
                hero = developed(content, klass.id, level)
                for attempt in range(3):
                    seed = derive(SEED, klass.id, city.id, location.slot, attempt)
                    pack = generate_group(
                        seed,
                        archetypes=content.enemy_archetypes,
                        biome=location.biomes,
                        level=level,
                    )
                    samples[("packs", klass.id, level)].append(
                        simulate(content, {hero.id: hero}, pack, seed)
                    )
        # Неполная сборка новичка: реальные уровни всех обычных боевых узлов первой области.
        hero = developed(content, klass.id, 1, partial=True)
        location = build_location(
            content, "world-design-20261016", LocationSession(city_id="farhold", slot=1)
        )
        first_level = min(node.level for node in location.nodes if node.kind is NodeKind.BATTLE)
        for attempt in range(12):
            seed = derive(SEED, "novice", klass.id, attempt)
            pack = generate_group(
                seed, archetypes=content.enemy_archetypes, biome=location.biomes, level=first_level
            )
            samples[("novice", klass.id, first_level)].append(
                simulate(content, {hero.id: hero}, pack, seed)
            )
    rows = [
        dict(
            kind=key[0], hero_class=key[1], level=key[2], trials=len(results), **summarized(results)
        )
        for key, results in sorted(samples.items())
    ]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("backups/world-design-20261016.json"))
    args = parser.parse_args()
    content = load_content(Path("content"))
    rows = measure(content)
    report = {
        "seed": SEED.decode(),
        "levels": LEVELS,
        "limits": (
            "Модель одной тактики, обычное исправное снаряжение, полный запас. "
            "Отдельные виды проверены на шести уровнях, стаи — в середине диапазона места. "
            "Новичок с неполной сборкой — против самых младших обычных стай первой карты. "
            "Живой игры и долговременного баланса не доказывает."
        ),
        "active_locations": sum(len(city.active_locations) for city in content.cities),
        "legacy_maps": sum(
            len(city.locations) - len(city.active_locations) for city in content.cities
        ),
        "species": len(content.enemy_archetypes),
        "biomes": [
            {
                "biome": biome,
                "species": len(pool),
                "roles": sorted({role_of(one).value for one in pool}),
            }
            for biome in sorted({one.biome for city in content.cities for one in city.locations})
            if (pool := candidates(content.enemy_archetypes, biome))
        ],
        "rows": rows,
        "total": sum(row["trials"] for row in rows),
        "wins": sum(row["wins"] for row in rows),
        "unfinished": sum(row["unfinished"] for row in rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("active_locations", "species", "total", "wins", "unfinished")
            }
        )
    )


if __name__ == "__main__":
    main()
