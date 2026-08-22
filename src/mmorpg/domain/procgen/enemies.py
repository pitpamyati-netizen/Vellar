"""Enemy generation.

An enemy is an archetype from ``content/enemies.toml`` scaled to a level. The
archetype is picked with an explicit RNG built from the node seed, so the same
node in the same cycle always produces the same opponent.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.location import (
    DEFAULT_DAMAGE_TYPES,
    Enemy,
    EnemyArchetype,
    EnemyRank,
)
from mmorpg.domain.procgen.seeds import rng

# Level baseline. An "average" archetype (all multipliers 1.0) uses these values.
# Health is set against the standard blow of a character of the same level
# (`domain.rules.combat.standard_blow`): an ordinary opponent is meant to fall in
# about three turns, which is what tests/domain/test_combat_balance.py pins down.
# Здоровье меряется против стандартного удара, а стандартный удар с тех пор
# считает оружие в руке (``domain/rules/equipment.py``): противник, посчитанный
# против голых рук, ложился за два хода, стоил игроку одного процента здоровья и
# делал вылазку формальностью. Сорок процентов сверху возвращают бою обещанные три хода
# при том же оружии, каким его дерётся живой игрок.
#
# Основание правилось ещё раз, когда у оружия сузился размах (ADR 0017 и
# ``entities/dice.py``): длина боя держалась на невезении. Пока верхняя граница
# удара была вчетверо выше нижней, первый уровень выигрывал бой то за два хода,
# то за шесть, и середина ложилась куда надо; с размахом в полтора раза бой
# встал ровно на своё среднее — а среднее на первом уровне было четыре с
# половиной хода. Правится основание, а не рост: выше десятого уровня разница
# меньше двух процентов.
HEALTH_BASE = 30.0
HEALTH_PER_LEVEL = 12.30
# Damage is set against the player's health pool rather than against their blow:
# an ordinary opponent should cost a noticeable share of it over the three turns
# it lives, so that a run through a location is a series of decisions - press on,
# drink, walk back and pay for a bed - and not a formality.
#
# It used to cost about a twentieth. Every fight was won, nothing was ever spent,
# and the inn, the potions and the wounds that outlive a fight were all decoration
# (Roadmap, "Риски"). Tripling it is the whole change; the long tiers give the
# tripling back in ``RANK_FACTORS`` so that an epic and a boss hurt exactly as
# much as they did before - they were already dangerous.
# Основание урона поднято той же правкой и по той же причине: бой, вставший на
# своё среднее, стал стоить меньше — крайних случаев не осталось ни с той
# стороны, ни с этой.
DAMAGE_BASE = 10.5
DAMAGE_PER_LEVEL = 1.75
ARMOR_PER_LEVEL = 1.15
INITIATIVE_BASE = 8.0
INITIATIVE_PER_LEVEL = 0.35
GOLD_BASE = 4.0
GOLD_PER_LEVEL = 2.4

#: A pack shares one fight's worth of health, damage **and pay** rather than
#: multiplying it. Three full-strength opponents made an "ordinary" fight nine
#: turns long - three fights in a row wearing one name. Each extra body still
#: adds to the total, just far less than a whole opponent.
#:
#: Плата делится тем же делителем, и это не мелочь: делили только здоровье и
#: урон, а золото и опыт стая множила на троих. Один и тот же бой стоил
#: полутора боёв по времени и платил как три - и грести стаи было выгоднее
#: всего, что есть в игре (``domain/rules/combat._check_outcome``).
GROUP_MEMBER_TAX = 0.45


def group_scale(size: int) -> float:
    """What each member of a pack of ``size`` is worth on its own."""
    return 1.0 / (1.0 + GROUP_MEMBER_TAX * (size - 1))


@dataclass(frozen=True, slots=True)
class RankFactors:
    """What a tier multiplies. Health buys the turns, gold pays for them.

    Damage does not grow with the tier - it shrinks. A boss lasts four times as
    long as an ordinary opponent, so it lands four times as many blows: a blow of
    the same size would kill the player on turn three of ten. What a tier costs is
    counted over the whole fight, and over the whole fight a boss still takes far
    more health than an ordinary opponent does.
    """

    health: float
    damage: float
    armor: float
    gold: float
    experience: float


#: Gold and experience are paid per turn spent, near enough: a boss that takes
#: four times as long has to be worth four times as much, or the shortest fight
#: in the location is always the best one to grind.
RANK_FACTORS: dict[EnemyRank, RankFactors] = {
    EnemyRank.NORMAL: RankFactors(health=1.0, damage=1.0, armor=1.0, gold=1.0, experience=1.0),
    EnemyRank.ELITE: RankFactors(health=2.6, damage=0.5, armor=1.2, gold=3.0, experience=2.5),
    EnemyRank.BOSS: RankFactors(health=5.2, damage=0.5, armor=1.35, gold=7.0, experience=5.0),
}

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
    rank: EnemyRank = EnemyRank.NORMAL,
    elite_titles: Sequence[str] = (),
    members: int = 1,
) -> Enemy:
    """Build one opponent. Same seed, same enemy - down to the last hit point.

    ``members`` is how many stand beside it, itself included: a pack shares one
    fight's budget, so each of three is weaker than one alone.
    """
    pool = candidates(archetypes, biome)
    if not pool:
        msg = f"no enemy archetype fits biome {biome!r}"
        raise LookupError(msg)

    random_source = rng(seed)
    archetype = pool[random_source.randrange(len(pool))]
    spread = 1.0 + random_source.uniform(-VARIANCE, VARIANCE)
    factors = RANK_FACTORS[rank]
    share = group_scale(members)

    health = (
        (HEALTH_BASE + HEALTH_PER_LEVEL * level)
        * archetype.health
        * spread
        * factors.health
        * share
    )
    damage = (
        (DAMAGE_BASE + DAMAGE_PER_LEVEL * level)
        * archetype.damage
        * spread
        * factors.damage
        * share
    )
    armor = ARMOR_PER_LEVEL * level * archetype.armor * factors.armor
    initiative = (INITIATIVE_BASE + INITIATIVE_PER_LEVEL * level) * archetype.initiative
    gold = (GOLD_BASE + GOLD_PER_LEVEL * level) * spread * factors.gold * share

    return Enemy(
        archetype_id=archetype.id,
        name=_name_for(archetype, rank, elite_titles, random_source),
        kind=archetype.kind,
        level=level,
        max_health=max(1, round(health)),
        damage=max(1, round(damage)),
        armor=max(0, round(armor)),
        initiative=round(initiative, 2),
        loot=archetype.loot,
        gold=max(1, round(gold)),
        rank=rank,
        element=element_of(archetype),
    )


def element_of(archetype: EnemyArchetype) -> DamageType:
    """Чем бьёт этот противник. Не объявлено в содержимом - решает порода."""
    if archetype.element is not None:
        return archetype.element
    return DEFAULT_DAMAGE_TYPES[archetype.kind]


def _name_for(
    archetype: EnemyArchetype,
    rank: EnemyRank,
    elite_titles: Sequence[str],
    random_source: random.Random,
) -> str:
    """A titled name for the long-fight tiers.

    The title does not say which tier - the combat screen does that in words. It
    only says that this one is not an ordinary animal.
    """
    if rank is EnemyRank.NORMAL or not elite_titles:
        return archetype.name
    title = elite_titles[random_source.randrange(len(elite_titles))]
    return f"{title} {archetype.name.lower()}"


def generate_group(
    seed: bytes,
    *,
    archetypes: Sequence[EnemyArchetype],
    biome: str,
    level: int,
    rank: EnemyRank = EnemyRank.NORMAL,
    elite_titles: Sequence[str] = (),
    max_size: int = 3,
) -> tuple[Enemy, ...]:
    """One to ``max_size`` opponents. A long fight is always a single opponent.

    An epic or a boss stands alone because its whole cost is measured in turns:
    two of them side by side would double a fight that is already meant to be the
    longest in the game.
    """
    from mmorpg.domain.procgen.seeds import derive  # local: keeps the seed API in one place

    if rank.is_long_fight:
        return (
            generate_enemy(
                seed,
                archetypes=archetypes,
                biome=biome,
                level=level,
                rank=rank,
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
            members=size,
        )
        for index in range(size)
    )
