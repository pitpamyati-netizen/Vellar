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

# Основание по уровням. «Средняя» порода (все множители 1.0) пользуется этими
# значениями. Здоровье выставлено против стандартного удара персонажа того же уровня
# (`domain.rules.combat.standard_blow`): обычный противник должен падать ходов за три, и
# это закрепляет tests/domain/test_combat_balance.py. Здоровье меряется против
# стандартного удара, а стандартный удар с тех пор считает оружие в руке
# (``domain/rules/equipment.py``): противник, посчитанный против голых рук, ложился за
# два хода, стоил игроку одного процента здоровья и делал вылазку формальностью. Сорок
# процентов сверху возвращают бою обещанные три хода при том же оружии, каким его
# дерётся живой игрок.  Основание правилось ещё раз, когда у оружия сузился размах (ADR
# 0017 и ``entities/dice.py``): длина боя держалась на невезении. Пока верхняя граница
# удара была вчетверо выше нижней, первый уровень выигрывал бой то за два хода, то за
# шесть, и середина ложилась куда надо; с размахом в полтора раза бой встал ровно на
# своё среднее — а среднее на первом уровне было четыре с половиной хода. Правится
# основание, а не рост: выше десятого уровня разница меньше двух процентов.
HEALTH_BASE = 30.0
HEALTH_PER_LEVEL = 12.30
# Урон выставлен против запаса здоровья игрока, а не против его удара: обычный противник
# должен стоить заметной его доли за те три хода, что он живёт, — чтобы проход по
# локации был чередой решений (идти дальше, выпить, вернуться и заплатить за постель), а
# не формальностью.  Раньше он стоил примерно двадцатой части. Всякий бой выигрывался,
# не тратилось ничего, и постоялый двор, зелья и раны, переживающие бой, были
# украшением. Утроение — вся правка целиком; долгие ступени отдают это утроение обратно
# через ``RANK_FACTORS``, чтобы эпический противник и босс били ровно так же, как били
# прежде: они и без того были опасны. Основание урона поднято той же правкой и по той же
# причине: бой, вставший на своё среднее, стал стоить меньше — крайних случаев не
# осталось ни с той стороны, ни с этой.
DAMAGE_BASE = 10.5
DAMAGE_PER_LEVEL = 1.75
ARMOR_PER_LEVEL = 1.15
INITIATIVE_BASE = 8.0
INITIATIVE_PER_LEVEL = 0.35
GOLD_BASE = 4.0
GOLD_PER_LEVEL = 2.4

#: Стая делит здоровье, урон **и плату** одного боя, а не умножает их. Трое противников
#: в полную силу делали «обычный» бой девятиходовым — три боя подряд под одним именем.
#: Каждое лишнее тело всё ещё прибавляет к общему счёту, просто куда меньше целого
#: противника.  Плата делится тем же делителем, и это не мелочь: делили только здоровье
#: и урон, а золото и опыт стая множила на троих. Один и тот же бой стоил полутора боёв
#: по времени и платил как три - и грести стаи было выгоднее всего, что есть в игре
#: (``domain/rules/combat._check_outcome``).
GROUP_MEMBER_TAX = 0.45


def group_scale(size: int) -> float:
    """Сколько стоит сам по себе каждый из стаи размером ``size``."""
    return 1.0 / (1.0 + GROUP_MEMBER_TAX * (size - 1))


@dataclass(frozen=True, slots=True)
class RankFactors:
    """Что умножает ступень. Здоровье покупает ходы, золото за них платит.

    Урон со ступенью не растёт - он падает. Босс держится вчетверо дольше обычного
    противника, а значит, наносит вчетверо больше ударов: удар прежней величины
    убил бы игрока на третьем ходу из десяти. Цена ступени считается по всему бою, и
    по всему бою босс всё равно отнимает куда больше здоровья, чем обычный
    противник.
    """

    health: float
    damage: float
    armor: float
    gold: float
    experience: float


#: Золото и опыт платятся, считай, за потраченный ход: босс, который держится вчетверо
#: дольше, обязан и стоить вчетверо больше, иначе выгоднее всего в локации всегда самый
#: короткий бой.
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
    единственное, чем сложность данжа делает бой тяжелее (``domain/rules/
    dungeon.py``). ``bounty`` домножает только золото: им условие захода
    «богатая порода» делает вылазку выгоднее, не делая её опаснее.

    ``affixes_applied`` - прозвища этой стаи (ADR 0042): их множители запекаются
    в числа здесь же, а id-шники ложатся на ``Enemy.affixes``, чтобы бой навесил
    их эффекты и механики.
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
    gold = (
        (GOLD_BASE + GOLD_PER_LEVEL * level)
        * spread
        * factors.gold
        * share
        * stakes
        * bounty
        * affix_gold
    )

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

    Эпический противник и босс стоят в одиночку, потому что вся их цена меряется
    ходами: двое рядом удвоили бы бой, который и так задуман самым долгим в игре.

    ``stakes`` и ``bounty`` уходят каждому противнику: сложность данжа и его
    условия поднимают ставку всей стаи разом (``domain/rules/dungeon.py``).

    ``affixes``/``affix_chance``/``affix_count`` - прозвища-модификаторы: бросок
    один на всю стаю (ADR 0042), и «выводковый» так прибавляет тел.
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
