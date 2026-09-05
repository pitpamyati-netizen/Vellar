"""Снаряжение собирается, а не пишется.

В ``content/items.toml`` пишется **вид** (меч, кольчуга, оберег), а вещь
собирается из вида, ступени, редкости и **оттиска** - так же, как противник
собирается из породы и уровня (``procgen/enemies.py``).

- **Урон** - кости вида, у которых растёт среднее. Размах остаётся размахом
  рода и не плывёт со ступенью: верхняя граница нигде не выше полутора нижних
  (``entities/dice.py``, ``MAX_SPREAD``).
- **Броня** - число, считанное из ступени, рода доспеха и того, сколько
  прикрывает место.
- **Аффиксы** - прибавки, выпавшие вещи при сборке. Сколько их, решает
  редкость: обычная не несёт ни одной, необычная одну, редкая две, легендарная
  три, реликтовая четыре, и у двух старших сверх того есть особое свойство -
  аффикс полной силы. Аффиксом бывает и характеристика, и процент: пул один
  (ADR 0059).
- **Оттиск** - какой аффикс у вещи ведущий. Он же стоит в её имени («Крепкий
  меч ярости редкой работы») и в её идентификаторе, поэтому две находки одного
  вида и одной редкости - две **разные** вещи, а не две одинаковые кнопки.
- **Реликтовая вещь считается от уровня героя, а не от своего**: она растёт
  вместе с ним и потому не устаревает.

Величина аффикса берётся из опорной, растёт со ступенью вещи (``_tier_factor``)
и бросается в границах ``AFFIX_SPREAD``. Легендарной и реликтовой вещи прибавка
изредка выпадает **великой** - в полтора раза выше своего потолка; такие
называются вслух на карточке.

Сборка чистая и повторимая: всё, что в вещи «случайно», выведено из её же
идентификатора (``Claude.md``, правило 8).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
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

#: Как растут грани костей со ступенью вещи. Подобрано так, что средний бросок
#: оружия своей ступени идёт вровень со стандартным ударом: около 7 на первой
#: ступени и около 730 на последней (ADR 0015).
FACES_PER_LEVEL = 0.74

#: Броня доспеха своей ступени: нагрудник латного доспеха держит около 190 на
#: тринадцатой ступени и около 250 на последней.
ARMOR_BASE = 5.18
ARMOR_PER_LEVEL = 0.64

#: Прибавка к одной характеристике: доля от уровня вещи. Заметно, но не вместо
#: вложенных очков - две прибавки редкой вещи тринадцатой ступени дают по 11.
STAT_PER_LEVEL = 0.12

# Цена вещи. Считается от ступени и редкости и должна перекрывать сырьё, из
# которого её куют: работа дешевле материалов - ловушка, а не ремесло
# (``tests/content/test_crafts_content.py``).
PRICE_BASE = 30.0
PRICE_PER_LEVEL = 16.0

# Прочность снаряжения: растёт со ступенью вещи и множится редкостью
# (``Rarity.toughness``). Инструменту это число даёт одна редкость (ADR 0056),
# снаряжению - ступень и редкость вместе (ADR 0057).
DURABILITY_BASE = 40.0
DURABILITY_PER_LEVEL = 1.0

#: Насколько величина аффикса гуляет вокруг опорной. Четверть в обе стороны -
#: столько, чтобы находку стоило рассмотреть, и не столько, чтобы одна и та же
#: вещь у двоих значила разное (ADR 0059).
AFFIX_SPREAD = 0.25

#: Какая доля опорной величины достаётся аффиксу на первой ступени. Дальше
#: растёт до полной: иначе легендарка первой ступени даёт ровно то же, что
#: легендарка последней.
AFFIX_TIER_FLOOR = 0.5

#: Во сколько раз слабее процентный аффикс, выпавший обычным, а не особым
#: свойством. Особое свойство одно на вещь и бьёт в полную силу; иначе редкая
#: вещь с двумя процентами обгоняла бы легендарную.
ORDINARY_AFFIX_SCALE = 0.6

#: Великая прибавка: во сколько раз выше своего потолка и как часто выпадает.
#: Только легендарной и реликтовой вещи и не чаще, чем изредка.
GREAT_FACTOR = 1.5
GREAT_CHANCE = 0.12

#: Из чего собран идентификатор вещи: вид, ступень, редкость и оттиск.
#: ``sword@26#rare~4`` — «Добрый меч ярости редкой работы». Оттиск ноль не
#: пишется вовсе: эталон вещи называется так же, как назывался всегда.
LEVEL_MARK = "@"
RARITY_MARK = "#"
ROLL_MARK = "~"


@dataclass(frozen=True, slots=True)
class Affix:
    """Одна прибавка из пула: чем считается, как зовётся, сколько даёт.

    ``stat`` отделяет характеристику (её величина считается от уровня вещи) от
    процента (у него есть опорная величина, объявленная в содержимом).
    """

    key: str
    word: str
    value: float
    stat: bool


def gear_id(archetype_id: str, level: int, rarity_id: str, roll: int = 0) -> str:
    tail = f"{ROLL_MARK}{roll}" if roll else ""
    return f"{archetype_id}{LEVEL_MARK}{level}{RARITY_MARK}{rarity_id}{tail}"


def parse_gear_id(item_id: str) -> tuple[str, int, str, int] | None:
    """Разобрать идентификатор вещи. ``None`` — это не собранная вещь."""
    head, mark, tail = item_id.partition(RARITY_MARK)
    if not mark:
        return None
    rarity, roll_mark, roll = tail.partition(ROLL_MARK)
    if roll_mark and not roll.isdigit():
        return None
    archetype_id, mark, level = head.partition(LEVEL_MARK)
    if not mark or not level.isdigit():
        return None
    return archetype_id, int(level), rarity, int(roll) if roll_mark else 0


def _source(item_id: str) -> random.Random:
    """Случайность, выведенная из самой вещи: одинаковая у всех и навсегда."""
    return random.Random(blake2b(item_id.encode("utf-8"), digest_size=8).digest())


def affix_pool(content: GameContent) -> tuple[Affix, ...]:
    """Все прибавки, какие вещь может нести, в постоянном порядке.

    Порядок постоянен нарочно: **оттиск вещи - это номер её ведущего аффикса**, и
    сдвинься пул, у всех надетых вещей мира сменились бы имена. Сначала
    характеристики, потом особые свойства - как они объявлены в ``items.toml``.
    """
    stats = tuple(
        Affix(
            key=f"stat_{code.value}",
            word=content.stat_words.get(code.value, ""),
            value=0.0,
            stat=True,
        )
        for code in StatCode
    )
    specials = tuple(
        Affix(key=one.key, word=one.word, value=one.value, stat=False)
        for one in content.special_properties
    )
    return stats + specials


def rolls_of(content: GameContent) -> int:
    """Сколько у вещи бывает оттисков: по одному на каждый аффикс пула."""
    return len(affix_pool(content))


def _tier_factor(content: GameContent, level: int) -> float:
    """Какая доля опорной величины достаётся аффиксу на этом уровне вещи."""
    cap = max(1, content.rules.max_character_level)
    share = min(1.0, max(0, level) / cap)
    return AFFIX_TIER_FLOOR + (1.0 - AFFIX_TIER_FLOOR) * share


def _affixes_of(
    content: GameContent,
    source: random.Random,
    rarity: Rarity,
    *,
    roll: int,
    counted: int,
) -> tuple[dict[str, int], dict[str, float], tuple[str, ...], str]:
    """Что вещь этой редкости несёт: характеристики, проценты, великие, слово.

    Ведущий аффикс выбирается оттиском, остальные - самой вещью. Особое свойство
    легендарной и реликтовой берётся из процентных: характеристика в полную силу
    звучит не особым свойством, а крупной цифрой.
    """
    pool = affix_pool(content)
    if not pool or rarity.affixes <= 0:
        return {}, {}, (), ""

    lead = pool[roll % len(pool)]
    rest = [one for one in pool if one.key != lead.key]
    source.shuffle(rest)
    chosen = [lead, *rest[: max(0, rarity.affixes - 1)]]

    if rarity.special:
        taken = {one.key for one in chosen}
        extra = [one for one in pool if not one.stat and one.key not in taken]
        if extra:
            chosen.append(extra[source.randrange(len(extra))])

    stats: dict[str, int] = {}
    modifiers: dict[str, float] = {}
    great: list[str] = []
    tier = _tier_factor(content, counted)
    for index, affix in enumerate(chosen):
        shade = source.uniform(1.0 - AFFIX_SPREAD, 1.0 + AFFIX_SPREAD)
        if rarity.special and source.random() < GREAT_CHANCE:
            shade *= GREAT_FACTOR
            great.append(affix.key)
        if affix.stat:
            code = affix.key.removeprefix("stat_")
            stats[code] = max(1, round(STAT_PER_LEVEL * counted * shade))
            continue
        # Особое свойство - последнее в списке у редкостей, которые его несут:
        # только оно идёт в полную силу.
        special = rarity.special and index == len(chosen) - 1
        scale = 1.0 if special else ORDINARY_AFFIX_SCALE
        amount = affix.value * scale * tier * shade
        modifiers[affix.key] = float(round(amount)) or (1.0 if affix.value > 0 else -1.0)
    return stats, modifiers, tuple(great), lead.word


def build(
    content: GameContent,
    archetype: GearArchetype,
    level: int,
    rarity: Rarity,
    *,
    roll: int = 0,
    hero_level: int = 0,
) -> Item:
    """Собрать вещь. ``hero_level`` нужен только реликтовым — они растут по нему."""
    item_id = gear_id(archetype.id, level, rarity.id, roll)
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

    # Инструмент - вещь без чисел: он не бьёт, не прикрывает и не прибавляет
    # характеристик. Всё, что редкость даёт инструменту, - прочность (ADR 0056).
    tool = bool(archetype.tool_type)
    if tool:
        stat_bonuses: dict[str, int] = {}
        modifiers: dict[str, float] = {}
        great: tuple[str, ...] = ()
        word = ""
    else:
        stat_bonuses, modifiers, great, word = _affixes_of(
            content, source, rarity, roll=roll, counted=counted
        )

    return Item(
        id=item_id,
        name=name_of(content, archetype, level, rarity, word),
        kind=ItemKind.EQUIPMENT,
        slot=archetype.slot,
        rarity=rarity.id,
        level=level,
        # Инструмент редкостью не дорожает вдвойне: цену ему поднимает только лавка
        # (``economy.buy_price`` и так множит на редкость), поэтому один сбор стоит
        # одинаково любой киркой (ADR 0056).
        price=max(
            1,
            round((PRICE_BASE + PRICE_PER_LEVEL * level) * (1.0 if tool else rarity.price_factor)),
        ),
        modifiers=modifiers,
        skill_modifiers={},
        damage=damage,
        armor=armor,
        stat_bonuses=stat_bonuses,
        great=great,
        weapon_type=archetype.weapon_type,
        armor_type=archetype.armor_type,
        tool_type=archetype.tool_type,
        # Инструмент стачивается о сборы и считается по редкости; всё прочее надетое -
        # о бои и по ступени с поправкой на редкость. Реликтовая вещь и здесь идёт по
        # уровню героя (ADR 0057).
        durability=(
            rarity.durability
            if tool
            else max(
                1, round((DURABILITY_BASE + DURABILITY_PER_LEVEL * counted) * rarity.toughness)
            )
        ),
    )


def name_of(
    content: GameContent,
    archetype: GearArchetype,
    level: int,
    rarity: Rarity,
    word: str = "",
) -> str:
    """«Крепкий меч ярости редкой работы».

    Ступень даёт прилагательное, ведущий аффикс - слово, редкость - след. Слово
    не украшение: им два меча одной ступени и одной редкости различаются на слух.
    """
    tier = tier_at(content, level)
    prefix = tier.named(archetype.gender) if tier is not None else ""
    head = f"{prefix} {archetype.noun}".strip() if prefix else archetype.noun
    if word:
        head = f"{head} {word}"
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
    собирать ради этого две тысячи предметов незачем. Оттиски сюда не идут:
    содержимое ссылается на эталон, а оттиск — дело находки.
    """
    return {
        gear_id(archetype.id, tier.level, rarity.id)
        for archetype in archetypes
        for tier in tiers
        for rarity in rarities
    }


def catalogue(content: GameContent) -> tuple[Item, ...]:
    """Эталоны всего снаряжения: каждый вид на каждой ступени в каждой редкости.

    Оттиск здесь нулевой. Вещь с другим оттиском собирается по требованию
    (:func:`assemble`): выкладывать все оттиски значило бы держать в памяти
    десятки тысяч предметов.
    """
    return tuple(
        build(content, archetype, tier.level, rarity)
        for archetype in content.gear_archetypes
        for tier in content.gear_tiers
        for rarity in content.rarities
    )


def assemble(content: GameContent, item_id: str) -> Item | None:
    """Собрать вещь по её имени. ``None`` — такой вещи в мире нет.

    Это дверь для ленивой сборки: реестр держит эталоны, а всё, у чего есть
    оттиск, собирается здесь и запоминается (``GameContent.item``).
    """
    parsed = parse_gear_id(item_id)
    if parsed is None:
        return None
    archetype_id, level, rarity_id, roll = parsed
    if not content.has_gear_archetype(archetype_id) or not content.has_rarity(rarity_id):
        return None
    if not any(tier.level == level for tier in content.gear_tiers):
        return None
    if roll < 0 or roll >= max(1, rolls_of(content)):
        return None
    return build(
        content, content.gear_archetype(archetype_id), level, content.rarity(rarity_id), roll=roll
    )


def worn(content: GameContent, item: Item, hero_level: int) -> Item:
    """Вещь такой, какая она на этом герое.

    Для всего, кроме реликтового, это она сама. Реликтовая пересобирается по
    уровню героя - в этом её суть, и потому её числа нигде не хранятся.
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
    return build(content, archetype, parsed[1], rarity, roll=parsed[3], hero_level=hero_level)


# --- что падает ------------------------------------------------------

#: Насколько часто с побеждённого вообще падает снаряжение. Обычный противник
#: платит золотом и сырьём; вещь с него — находка, а не жалованье. С хозяина
#: логова она падает всегда: ради этого туда и идут.
DROP_CHANCE: dict[EnemyRank, float] = {
    EnemyRank.NORMAL: 0.09,
    EnemyRank.ELITE: 0.30,
    EnemyRank.BOSS: 1.0,
}

#: И насколько часто эта вещь оказывается реликтовой. Только с хозяина логова:
#: реликтовое не лежит на прилавке, не выпадает с волка и не куётся. Число
#: низкое нарочно (ADR 0052) - реликтовая вещь не устаревает никогда.
RELIC_CHANCE: dict[EnemyRank, float] = {
    EnemyRank.NORMAL: 0.0,
    EnemyRank.ELITE: 0.0,
    EnemyRank.BOSS: 0.05,
}


#: Насколько сильно ``rarity_percent`` тянет вес редкой вещи. Прибавка бьёт по
#: весу, а не по шансу: «редкая добыча выпадает чаще» - это про то, какая вещь
#: выпала, а не про то, выпала ли она. Вес обычной вещи она не трогает.
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

    Ступень берётся по уровню противника, редкость - по весу, оттиск - броском,
    и только с хозяина логова редкость бывает реликтовой. Оттиск и есть то, чем
    две находки одного вида отличаются друг от друга (ADR 0059).

    ``drop_bonus`` и ``rarity_bonus`` - проценты со следопыта, разбойника и всего,
    что обещает добычу почаще и побогаче.
    """
    if not content.gear_archetypes or not content.gear_tiers:
        return None
    chance = DROP_CHANCE.get(rank, 0.0) * (1.0 + max(-1.0, drop_bonus / 100.0))
    if source.random() >= chance:
        return None

    tier = tier_at(content, level)
    if tier is None:
        return None
    # Инструмент с побеждённого не падает: его покупают, и покупают всегда
    # (``rules/economy.tool_stock``, ADR 0056). Кирка вместо меча с хозяина
    # логова была бы не находкой, а промахом таблицы добычи.
    droppable = [one for one in content.gear_archetypes if not one.tool_type]
    if not droppable:
        return None
    archetype = droppable[source.randrange(len(droppable))]
    roll = source.randrange(max(1, rolls_of(content)))

    relics = [rarity for rarity in content.rarities if rarity.scaling]
    if relics and source.random() < RELIC_CHANCE.get(rank, 0.0):
        return gear_id(archetype.id, tier.level, relics[0].id, roll)

    sellable = [rarity for rarity in content.rarities if rarity.weight > 0]
    if not sellable:
        return None
    heaviest = max(rarity.weight for rarity in sellable)
    weights = [
        rarity.weight * (1.0 + rarity_bonus * RARITY_PULL * (1.0 - rarity.weight / heaviest))
        for rarity in sellable
    ]
    picked = source.choices(sellable, weights=weights, k=1)[0]
    return gear_id(archetype.id, tier.level, picked.id, roll if picked.affixes else 0)
