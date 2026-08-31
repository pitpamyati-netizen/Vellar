"""Снаряжение собирается, а не пишется.

Противник в Vellar давно собирается из породы и уровня (``procgen/enemies.py``);
снаряжение до сих пор писали руками, и его было сорок вещей на триста уровней —
после тридцатого надеть было нечего вовсе. Здесь то же правило распространяется
на вещи: в ``content/items.toml`` пишется **вид** (меч, кольчуга, оберег), а вещь
собирается из вида, ступени и редкости.

Три числа и одно правило.

- **Урон** — кости вида, у которых растёт среднее. Размах остаётся размахом
  рода и не плывёт со ступенью: булава бьёт вразброс и на первом уровне, и на
  трёхсотом, но верхняя граница нигде не выше полутора нижних
  (``entities/dice.py``, ``MAX_SPREAD``).
- **Броня** — число, считанное из ступени, рода доспеха и того, сколько
  прикрывает место.
- **Характеристики** — их даёт редкость, и только она: обычная вещь не даёт ни
  одной, необычная одну, редкая, легендарная и реликтовая по две. У легендарной и
  реликтовой сверх того есть особое свойство.
- **Реликтовая вещь считается от уровня героя, а не от своего.** Она растёт
  вместе с ним и потому не устаревает — за это её и дают только с хозяина логова
  или за пройденную цепочку заданий.

Сборка чистая и повторимая: всё, что в вещи «случайно» — какие именно
характеристики и какое свойство, — выведено из её же идентификатора. Один и тот
же «Крепкий меч редкой работы» одинаков у всех и после любого перезапуска
(``Claude.md``, правило 8).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from hashlib import blake2b

from mmorpg.domain.entities.content import (
    GameContent,
    GearArchetype,
    GearTier,
    Item,
    ItemKind,
    Rarity,
)
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.stats import StatCode

#: Как растут грани костей с уровнем вещи. Подобрано так, что средний бросок
#: оружия своей ступени идёт вровень с тем, что раньше называлось стандартным
#: ударом: около 8 на первом уровне и около 670 на трёхсотом (ADR 0015).
FACES_PER_LEVEL = 0.37

#: Броня доспеха своей ступени. Нагрудник тяжёлого доспеха на сотом уровне держит
#: около сотни — при смягчителе того же уровня это примерно четверть удара.
ARMOR_BASE = 5.5
ARMOR_PER_LEVEL = 0.32

#: Прибавка к одной характеристике: доля от уровня. Две прибавки редкой вещи на
#: сотом уровне — это шесть и шесть при своих двух сотнях, то есть заметно, но
#: не вместо вложенных очков.
STAT_PER_LEVEL = 0.06

# Цена вещи. Считается от ступени и редкости, и должна перекрывать сырьё, из
# которого её куют: работа, которая стоит дешевле материалов, - это ловушка, а не
# ремесло (``tests/content/test_crafts_content.py``).
PRICE_BASE = 30.0
PRICE_PER_LEVEL = 7.0

#: Из чего собран идентификатор вещи: вид, ступень, редкость.
#: ``sword@26#rare`` — «Добрый меч редкой работы».
LEVEL_MARK = "@"
RARITY_MARK = "#"


def gear_id(archetype_id: str, level: int, rarity_id: str) -> str:
    return f"{archetype_id}{LEVEL_MARK}{level}{RARITY_MARK}{rarity_id}"


def parse_gear_id(item_id: str) -> tuple[str, int, str] | None:
    """Разобрать идентификатор вещи. ``None`` — это не собранная вещь."""
    head, mark, rarity = item_id.partition(RARITY_MARK)
    if not mark:
        return None
    archetype_id, mark, level = head.partition(LEVEL_MARK)
    if not mark or not level.isdigit():
        return None
    return archetype_id, int(level), rarity


def _source(item_id: str) -> random.Random:
    """Случайность, выведенная из самой вещи: одинаковая у всех и навсегда."""
    return random.Random(blake2b(item_id.encode("utf-8"), digest_size=8).digest())


def _stat_codes(source: random.Random, count: int) -> tuple[StatCode, ...]:
    if count <= 0:
        return ()
    return tuple(source.sample(list(StatCode), k=min(count, len(StatCode))))


def build(
    content: GameContent,
    archetype: GearArchetype,
    level: int,
    rarity: Rarity,
    *,
    hero_level: int = 0,
) -> Item:
    """Собрать вещь. ``hero_level`` нужен только реликтовым — они растут по нему."""
    item_id = gear_id(archetype.id, level, rarity.id)
    counted = hero_level if rarity.scaling and hero_level > 0 else level
    source = _source(item_id)

    damage = None
    if archetype.weapon_type and content.has_weapon_type(archetype.weapon_type):
        kind = content.weapon_type(archetype.weapon_type)
        damage = kind.damage_at(1.0 + FACES_PER_LEVEL * (counted - 1))

    armor = 0
    if archetype.armor_type and content.has_armor_type(archetype.armor_type):
        share = content.slot(archetype.slot).armor_share if content.has_slot(archetype.slot) else 0
        armor = round(
            (ARMOR_BASE + ARMOR_PER_LEVEL * counted)
            * content.armor_type(archetype.armor_type).armor
            * share
        )

    amount = max(1, round(STAT_PER_LEVEL * counted))
    stat_bonuses = {code.value: amount for code in _stat_codes(source, rarity.stats)}

    modifiers: dict[str, float] = {}
    if rarity.special and content.special_properties:
        chosen = content.special_properties[source.randrange(len(content.special_properties))]
        modifiers[chosen.key] = chosen.value

    return Item(
        id=item_id,
        name=name_of(content, archetype, level, rarity),
        kind=ItemKind.EQUIPMENT,
        slot=archetype.slot,
        rarity=rarity.id,
        level=level,
        price=max(1, round((PRICE_BASE + PRICE_PER_LEVEL * level) * rarity.price_factor)),
        modifiers=modifiers,
        skill_modifiers={},
        damage=damage,
        armor=armor,
        stat_bonuses=stat_bonuses,
        weapon_type=archetype.weapon_type,
        armor_type=archetype.armor_type,
    )


def name_of(content: GameContent, archetype: GearArchetype, level: int, rarity: Rarity) -> str:
    """«Крепкий меч редкой работы». Ступень даёт прилагательное, редкость — след."""
    tier = tier_at(content, level)
    prefix = tier.named(archetype.gender) if tier is not None else ""
    head = f"{prefix} {archetype.noun}".strip() if prefix else archetype.noun
    return f"{head} {rarity.mark}".strip() if rarity.mark else head


def tier_at(content: GameContent, level: int) -> GearTier | None:
    """Ступень, на которой стоит вещь этого уровня."""
    found: GearTier | None = None
    for tier in content.gear_tiers:
        if tier.level <= level:
            found = tier
    return found or (content.gear_tiers[0] if content.gear_tiers else None)


def catalogue_ids(
    archetypes: Sequence[GearArchetype],
    tiers: Sequence[GearTier],
    rarities: Sequence[Rarity],
) -> set[str]:
    """Имена всех вещей мира, без самих вещей.

    Нужно проверке ссылок: задание, рецепт и добыча называют вещь по имени, а
    собирать ради этого две тысячи предметов незачем.
    """
    return {
        gear_id(archetype.id, tier.level, rarity.id)
        for archetype in archetypes
        for tier in tiers
        for rarity in rarities
    }


def catalogue(content: GameContent) -> tuple[Item, ...]:
    """Всё снаряжение мира: каждый вид на каждой ступени в каждой редкости.

    Список считается один раз при сборке содержимого и дальше только читается:
    ничего производного не хранится, но и пересчитывать две тысячи вещей на
    каждый показ прилавка незачем.
    """
    return tuple(
        build(content, archetype, tier.level, rarity)
        for archetype in content.gear_archetypes
        for tier in content.gear_tiers
        for rarity in content.rarities
    )


def worn(content: GameContent, item: Item, hero_level: int) -> Item:
    """Вещь такой, какая она на этом герое.

    Для всего, кроме реликтового, это она сама. Реликтовая пересобирается по
    уровню героя — в этом вся её суть, и потому её числа нигде не хранятся.
    """
    if not item.is_equipment or not content.has_rarity(item.rarity):
        return item
    rarity = content.rarity(item.rarity)
    if not rarity.scaling or hero_level <= 0:
        return item
    parsed = parse_gear_id(item.id)
    if parsed is None or not content.has_gear_archetype(parsed[0]):
        return item
    archetype = content.gear_archetype(parsed[0])
    return build(content, archetype, parsed[1], rarity, hero_level=hero_level)


# --- что падает ------------------------------------------------------

#: Насколько часто с побеждённого вообще падает снаряжение. Обычный противник
#: платит золотом и сырьём; вещь с него — находка, а не жалованье. С хозяина
#: логова она падает всегда: ради этого туда и идут.
DROP_CHANCE: dict[EnemyRank, float] = {
    EnemyRank.NORMAL: 0.12,
    EnemyRank.ELITE: 0.35,
    EnemyRank.BOSS: 1.0,
}

#: И насколько часто эта вещь оказывается реликтовой. Только с хозяина логова:
#: реликтовое не лежит на прилавке, не выпадает с волка и не куётся — его берут
#: с того, кто держит логово, или получают за пройденную цепочку заданий.
#: Число низкое нарочно (ADR 0052): реликтовая вещь растёт вместе с героем и не
#: устаревает никогда, поэтому она — редкая удача за много ходок к логову, а не
#: то, с чем выходит каждый третий.
RELIC_CHANCE: dict[EnemyRank, float] = {
    EnemyRank.NORMAL: 0.0,
    EnemyRank.ELITE: 0.0,
    EnemyRank.BOSS: 0.05,
}


#: Насколько сильно ``rarity_percent`` тянет вес редкой вещи. Прибавка бьёт по
#: весу, а не по шансу: «редкая добыча выпадает чаще» - это про то, какая вещь
#: выпала, а не про то, выпала ли она. Вес обычной вещи она не трогает вовсе,
#: иначе пятнадцать процентов удачи превращались бы в реликвии.
RARITY_PULL = 0.02


def roll_drop(
    content: GameContent,
    source: random.Random,
    *,
    level: int,
    rank: EnemyRank,
    drop_bonus: float = 0.0,
    rarity_bonus: float = 0.0,
) -> str | None:
    """Что падает с побеждённого: имя вещи или ``None``, если ничего.

    Ступень берётся по уровню противника, редкость — по весу, и только с хозяина
    логова редкость бывает реликтовой.

    ``drop_bonus`` и ``rarity_bonus`` — проценты со следопыта, разбойника и
    всего, что обещает добычу почаще и побогаче. Обещали давно, считаются здесь:
    до этого оба ключа лежали в словаре и не читались никем.
    """
    if not content.gear_archetypes or not content.gear_tiers:
        return None
    chance = DROP_CHANCE.get(rank, 0.0) * (1.0 + max(-1.0, drop_bonus / 100.0))
    if source.random() >= chance:
        return None

    tier = tier_at(content, level)
    if tier is None:
        return None
    archetype = content.gear_archetypes[source.randrange(len(content.gear_archetypes))]

    relics = [rarity for rarity in content.rarities if rarity.scaling]
    if relics and source.random() < RELIC_CHANCE.get(rank, 0.0):
        return gear_id(archetype.id, tier.level, relics[0].id)

    sellable = [rarity for rarity in content.rarities if rarity.weight > 0]
    if not sellable:
        return None
    heaviest = max(rarity.weight for rarity in sellable)
    weights = [
        rarity.weight * (1.0 + rarity_bonus * RARITY_PULL * (1.0 - rarity.weight / heaviest))
        for rarity in sellable
    ]
    picked = source.choices(sellable, weights=weights, k=1)[0]
    return gear_id(archetype.id, tier.level, picked.id)
