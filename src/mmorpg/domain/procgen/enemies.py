"""Enemy generation.

An enemy is an archetype from ``content/enemies.toml`` scaled to a level. The
archetype is picked with an explicit RNG built from the node seed, so the same
node in the same cycle always produces the same opponent.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.location import Enemy, EnemyArchetype
from mmorpg.domain.procgen.seeds import rng

# Level baseline. An "average" archetype (all multipliers 1.0) uses these values.
HEALTH_BASE = 28.0
HEALTH_PER_LEVEL = 11.5
DAMAGE_BASE = 5.0
DAMAGE_PER_LEVEL = 2.1
ARMOR_PER_LEVEL = 1.15
INITIATIVE_BASE = 8.0
INITIATIVE_PER_LEVEL = 0.35
GOLD_BASE = 4.0
GOLD_PER_LEVEL = 2.4

ELITE_HEALTH_FACTOR = 2.3
ELITE_DAMAGE_FACTOR = 1.45
ELITE_GOLD_FACTOR = 3.0

# Same enemy at the same level still varies a little, so two fights do not feel
# copy-pasted. The spread is deterministic - it comes from the seed.
VARIANCE = 0.12


def candidates(archetypes: Sequence[EnemyArchetype], biome: str) -> tuple[EnemyArchetype, ...]:
    """Archetypes that fit a biome, falling back to the wildcard ones."""
    fitting = tuple(archetype for archetype in archetypes if archetype.fits(biome))
    if fitting:
        return fitting
    return tuple(archetype for archetype in archetypes if "*" in archetype.biomes)


def generate_enemy(
    seed: bytes,
    *,
    archetypes: Sequence[EnemyArchetype],
    biome: str,
    level: int,
    elite: bool = False,
    elite_titles: Sequence[str] = (),
) -> Enemy:
    """Build one opponent. Same seed, same enemy - down to the last hit point."""
    pool = candidates(archetypes, biome)
    if not pool:
        msg = f"no enemy archetype fits biome {biome!r}"
        raise LookupError(msg)

    random_source = rng(seed)
    archetype = pool[random_source.randrange(len(pool))]
    spread = 1.0 + random_source.uniform(-VARIANCE, VARIANCE)

    health = (HEALTH_BASE + HEALTH_PER_LEVEL * level) * archetype.health * spread
    damage = (DAMAGE_BASE + DAMAGE_PER_LEVEL * level) * archetype.damage * spread
    armor = ARMOR_PER_LEVEL * level * archetype.armor
    initiative = (INITIATIVE_BASE + INITIATIVE_PER_LEVEL * level) * archetype.initiative
    gold = (GOLD_BASE + GOLD_PER_LEVEL * level) * spread

    name = archetype.name
    if elite:
        health *= ELITE_HEALTH_FACTOR
        damage *= ELITE_DAMAGE_FACTOR
        gold *= ELITE_GOLD_FACTOR
        if elite_titles:
            title = elite_titles[random_source.randrange(len(elite_titles))]
            name = f"{title} {archetype.name.lower()}"

    return Enemy(
        archetype_id=archetype.id,
        name=name,
        kind=archetype.kind,
        level=level,
        max_health=max(1, round(health)),
        damage=max(1, round(damage)),
        armor=max(0, round(armor)),
        initiative=round(initiative, 2),
        is_elite=elite,
        loot=archetype.loot,
        gold=max(1, round(gold)),
    )


def generate_group(
    seed: bytes,
    *,
    archetypes: Sequence[EnemyArchetype],
    biome: str,
    level: int,
    elite: bool = False,
    elite_titles: Sequence[str] = (),
    max_size: int = 3,
) -> tuple[Enemy, ...]:
    """One to ``max_size`` opponents. Elite fights are always a single opponent."""
    from mmorpg.domain.procgen.seeds import derive  # local: keeps the seed API in one place

    if elite:
        return (
            generate_enemy(
                seed,
                archetypes=archetypes,
                biome=biome,
                level=level,
                elite=True,
                elite_titles=elite_titles,
            ),
        )

    size_source = rng(derive(seed, "group"))
    size = size_source.choices(range(1, max_size + 1), weights=[6, 3, 1][:max_size])[0]
    return tuple(
        generate_enemy(
            derive(seed, "member", index),
            archetypes=archetypes,
            biome=biome,
            level=level,
            elite_titles=elite_titles,
        )
        for index in range(size)
    )
