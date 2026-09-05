"""Сборка противников.

Противник - это порода из ``content/enemies.toml``, растянутая на уровень.
Порода выбирается явным источником случайности, собранным из сида узла, поэтому
один и тот же узел в одном и том же цикле всегда даёт того же противника.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from mmorpg.domain.entities.content import EnemyAffix
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.location import (
    DEFAULT_DAMAGE_TYPES,
    Enemy,
    EnemyArchetype,
    EnemyRank,
)
from mmorpg.domain.procgen.seeds import derive, rng

# Основание по уровням: «средняя» порода (все множители 1.0) пользуется этими
# значениями. Здоровье выставлено против стандартного удара персонажа того же
# уровня (``domain.rules.combat.standard_blow``) - обычный противник должен
# падать ходов за три, и это закрепляет ``tests/domain/test_combat_balance.py``.
# Стандартный удар считает оружие в руке (``domain/rules/equipment.py``), а
# размах у оружия узкий (ADR 0017), поэтому бой стоит на своём среднем, а не на
# невезении. Правится основание, а не рост: выше десятого уровня разница меньше
# двух процентов.
HEALTH_BASE = 17.70
HEALTH_PER_LEVEL = 24.60
# Урон выставлен против запаса здоровья игрока, а не против его удара: обычный
# противник должен стоить заметной его доли за те три хода, что он живёт, -
# чтобы проход по локации был чередой решений (идти дальше, выпить, вернуться и
# заплатить за постель). Долгие ступени отдают часть этого обратно через
# ``RANK_FACTORS``: эпический противник и босс и без того опасны.
DAMAGE_BASE = 9.20
DAMAGE_PER_LEVEL = 4.55
ARMOR_PER_LEVEL = 2.30
INITIATIVE_BASE = 7.65
INITIATIVE_PER_LEVEL = 0.70
#: Плата за бой. Растёт медленнее уровня нарочно (ADR 0058): боёв на уровень
#: становится больше по кривой опыта. Показатель ниже единицы держит одну и ту
#: же меру по всей полосе - за уровень набирается примерно полторы вещи своей
#: ступени и на первом уровне, и на сто пятидесятом.
GOLD_BASE = 4.0
GOLD_PER_LEVEL = 3.5
GOLD_EXPONENT = 0.55


def gold_at(level: int) -> float:
    """Чего стоит один обычный бой этого уровня. Мера всякой платы в игре.

    Тайник, дно спуска и надбавка за дело со сводки считаются от этого числа, а
    не каждый от своей кривой: иначе одна из них рано или поздно обгоняет бой, и
    выгодным становится не то, что задумано.
    """
    scaled: float = GOLD_PER_LEVEL * float(max(1, level)) ** GOLD_EXPONENT
    return GOLD_BASE + scaled


#: Стая делит здоровье, урон **и плату** одного боя, а не умножает их: трое
#: противников в полную силу делали «обычный» бой девятиходовым и платили как
#: три (ADR 0019). Каждое лишнее тело всё ещё прибавляет к общему счёту, просто
#: куда меньше целого противника (``domain/rules/combat._check_outcome``).
GROUP_MEMBER_TAX = 0.45


def group_scale(size: int) -> float:
    """Сколько стоит сам по себе каждый из стаи размером ``size``."""
    return 1.0 / (1.0 + GROUP_MEMBER_TAX * (size - 1))


@dataclass(frozen=True, slots=True)
class RankFactors:
    """Что умножает ступень. Здоровье покупает ходы, золото за них платит.

    Урон со ступенью не растёт, а падает: босс держится вчетверо дольше обычного
    противника, а значит, наносит вчетверо больше ударов. По всему бою он всё
    равно отнимает куда больше здоровья.
    """

    health: float
    damage: float
    armor: float
    gold: float
    experience: float


#: Золото и опыт платятся, считай, за потраченный ход: босс, который держится
#: вчетверо дольше, обязан и стоить вчетверо больше, иначе выгоднее всего в
#: локации всегда самый короткий бой.
RANK_FACTORS: dict[EnemyRank, RankFactors] = {
    EnemyRank.NORMAL: RankFactors(health=1.0, damage=1.0, armor=1.0, gold=1.0, experience=1.0),
    EnemyRank.ELITE: RankFactors(health=2.6, damage=0.5, armor=1.2, gold=3.0, experience=2.5),
    EnemyRank.BOSS: RankFactors(health=5.2, damage=0.5, armor=1.35, gold=7.0, experience=5.0),
}

# Тот же противник того же уровня всё-таки немного разный, чтобы два боя не выглядели
# одним, скопированным дважды. Разброс при этом определён: он идёт из сида.
VARIANCE = 0.12


def _roll_affixes(
    seed: bytes, affixes: Sequence[EnemyAffix], chance: float, count: int
) -> tuple[EnemyAffix, ...]:
    """Прозвища этой стаи. Бросок один на всю стаю (ADR 0042)."""
    if not affixes or chance <= 0.0 or count <= 0:
        return ()
    source = rng(derive(seed, "affix"))
    if source.random() >= chance:
        return ()
    pool = list(affixes)
    weights = [affix.weight for affix in pool]
    picked: list[EnemyAffix] = []
    for _ in range(min(count, len(pool))):
        chosen = source.choices(pool, weights=weights, k=1)[0]
        index = pool.index(chosen)
        pool.pop(index)
        weights.pop(index)
        picked.append(chosen)
    return tuple(picked)


def candidates(
    archetypes: Sequence[EnemyArchetype], biome: str, *, dungeon: bool = False
) -> tuple[EnemyArchetype, ...]:
    """Породы, подходящие биому, с откатом к тем, что годятся везде.

    ``dungeon`` разводит два пула (ADR 0042): заход в подземелье берёт только
    ``dungeon``-породы, дорога - только остальные, и у каждого свой ``*``-запас.
    """
    pool = tuple(archetype for archetype in archetypes if archetype.dungeon is dungeon)
    fitting = tuple(archetype for archetype in pool if archetype.fits(biome))
    if fitting:
        return fitting
    return tuple(archetype for archetype in pool if "*" in archetype.biomes)


def generate_enemy(
    seed: bytes,
    *,
    archetypes: Sequence[EnemyArchetype],
    biome: str,
    level: int,
    rank: EnemyRank = EnemyRank.NORMAL,
    elite_titles: Sequence[str] = (),
    members: int = 1,
    stakes: float = 1.0,
    bounty: float = 1.0,
    dungeon: bool = False,
    affixes_applied: Sequence[EnemyAffix] = (),
) -> Enemy:
    """Собрать одного противника. Тот же сид - тот же противник, до последней жизни.

    ``members`` - сколько их стоит рядом, вместе с ним самим: стая делит бюджет
    одного боя, поэтому каждый из троих слабее одиночки.

    ``stakes`` поднимает всю ставку разом - здоровье, урон, золото, - и это
    единственное, чем сложность данжа делает бой тяжелее
    (``domain/rules/dungeon.py``). ``bounty`` домножает только золото.

    ``affixes_applied`` - прозвища этой стаи (ADR 0042): их множители запекаются
    в числа здесь же, а id-шники ложатся на ``Enemy.affixes``.
    """
    pool = candidates(archetypes, biome, dungeon=dungeon)
    if not pool:
        msg = f"no enemy archetype fits biome {biome!r}"
        raise LookupError(msg)

    random_source = rng(seed)
    archetype = pool[random_source.randrange(len(pool))]
    spread = 1.0 + random_source.uniform(-VARIANCE, VARIANCE)
    factors = RANK_FACTORS[rank]
    share = group_scale(members)

    affix_health = 1.0
    affix_damage = 1.0
    affix_armor = 1.0
    affix_initiative = 1.0
    affix_gold = 1.0
    for affix in affixes_applied:
        affix_health *= affix.health
        affix_damage *= affix.damage
        affix_armor *= affix.armor
        affix_initiative *= affix.initiative
        affix_gold *= affix.gold

    health = (
        (HEALTH_BASE + HEALTH_PER_LEVEL * level)
        * archetype.health
        * spread
        * factors.health
        * share
        * stakes
        * affix_health
    )
    damage = (
        (DAMAGE_BASE + DAMAGE_PER_LEVEL * level)
        * archetype.damage
        * spread
        * factors.damage
        * share
        * stakes
        * affix_damage
    )
    armor = ARMOR_PER_LEVEL * level * archetype.armor * factors.armor * affix_armor
    initiative = (
        (INITIATIVE_BASE + INITIATIVE_PER_LEVEL * level) * archetype.initiative * affix_initiative
    )
    gold = gold_at(level) * spread * factors.gold * share * stakes * bounty * affix_gold

    return Enemy(
        archetype_id=archetype.id,
        name=_name_for(archetype, rank, elite_titles, random_source, affixes_applied),
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
        stakes=stakes,
        affixes=tuple(affix.id for affix in affixes_applied),
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
    affixes_applied: Sequence[EnemyAffix] = (),
) -> str:
    """Имя с приставками: прозвища-модификаторы, затем прозвище долгого боя.

    Ни то ни другое не называет ступень - её словами называет боевой экран.
    «Иглистый матёрый упырь»: сперва то, чем стая опасна, потом то, что это не
    обычный зверь.
    """
    prefix = " ".join(affix.adjective for affix in affixes_applied)
    title = ""
    if rank is not EnemyRank.NORMAL and elite_titles:
        title = elite_titles[random_source.randrange(len(elite_titles))]
    if not prefix and not title:
        return archetype.name
    core = archetype.name if not (prefix or title) else archetype.name.lower()
    return " ".join(part for part in (prefix, title, core) if part)


def generate_group(
    seed: bytes,
    *,
    archetypes: Sequence[EnemyArchetype],
    biome: str,
    level: int,
    rank: EnemyRank = EnemyRank.NORMAL,
    elite_titles: Sequence[str] = (),
    max_size: int = 3,
    stakes: float = 1.0,
    bounty: float = 1.0,
    dungeon: bool = False,
    affixes: Sequence[EnemyAffix] = (),
    affix_chance: float = 0.0,
    affix_count: int = 1,
) -> tuple[Enemy, ...]:
    """От одного до ``max_size`` противников. Долгий бой - всегда бой с одним.

    Эпический противник и босс стоят в одиночку: вся их цена меряется ходами, и
    двое рядом удвоили бы самый долгий бой в игре.

    ``stakes`` и ``bounty`` уходят каждому противнику. ``affixes``/
    ``affix_chance``/``affix_count`` - прозвища-модификаторы: бросок один на всю
    стаю (ADR 0042), и «выводковый» так прибавляет тел.
    """
    rolled = _roll_affixes(seed, affixes, affix_chance, affix_count)

    if rank.is_long_fight:
        return (
            generate_enemy(
                seed,
                archetypes=archetypes,
                biome=biome,
                level=level,
                rank=rank,
                elite_titles=elite_titles,
                stakes=stakes,
                bounty=bounty,
                dungeon=dungeon,
                affixes_applied=rolled,
            ),
        )

    pack_bonus = sum(affix.pack_bonus for affix in rolled)
    ceiling = max_size + pack_bonus
    weights = [6, 3, 1, 1, 1, 1, 1][:ceiling] or [1]
    size_source = rng(derive(seed, "group"))
    size = size_source.choices(range(1, ceiling + 1), weights=weights)[0]
    return tuple(
        generate_enemy(
            derive(seed, "member", index),
            archetypes=archetypes,
            biome=biome,
            level=level,
            elite_titles=elite_titles,
            members=size,
            stakes=stakes,
            bounty=bounty,
            dungeon=dungeon,
            affixes_applied=rolled,
        )
        for index in range(size)
    )
