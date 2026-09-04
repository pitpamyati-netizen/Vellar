"""Прилавок лавки, цены и сбор со сделки между игроками.

Прилавок - чистая функция от ``(город, переворот, репутация)``: он бросается из
сида, не хранится нигде, и переворачивается каждые полчаса - единственная
оставшаяся причина возвращаться в город по часам (``docs/procgen.md``).
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.procgen.seeds import rng, shop_seed

STOCK_MIN = 6
STOCK_MAX = 12

#: Доброе имя. ``reputation`` - это проценты ключа ``reputation_percent``, и
#: каждые десять процентов кладут на полку одну вещь и поднимают её потолок на
#: уровень. Ключ обещало «Знакомое лицо» и не читал никто: сама по себе
#: репутация в игре не растёт, а прибавка к ней была прибавкой к ничему
#: (``Roadmap.md``, ADR 0018). Теперь она делает ровно то, что говорит, -
#: держит лавку открытой чуть шире.
REPUTATION_KEY = "reputation_percent"
REPUTATION_PER_STEP = 10.0
LEVEL_WINDOW_BELOW = 6
LEVEL_WINDOW_ABOVE = 4
CHARISMA_DISCOUNT_PER_POINT = 0.4
MAX_CHARISMA_DISCOUNT = 15.0
SELL_FRACTION = 0.35

#: Что делает с лавкой «нужда» ближайшего города (``mood.city_strain``, 0…1,
#: ADR 0055). При полной нужде цена растёт наполовину, а прилавок теряет половину
#: позиций — но не опускается ниже ``STOCK_MIN``: голодный город всё же торгует.
STRAIN_PRICE_MARKUP = 0.5
STRAIN_STOCK_LOSS = 0.5

#: Инструменты, которые лежат на прилавке всегда, - по редкости. Без инструмента
#: сырьё не взять вовсе (ADR 0056), поэтому его наличие не бросают из сида: игрок,
#: у которого сточилась кирка, обязан купить новую в любом городе и в любой
#: переворот. Дороже этих двух инструмент бывает - но уже удачей общего прилавка,
#: а не полкой.
TOOL_RARITIES: tuple[str, ...] = ("common", "uncommon")

# Престол берёт сбор с каждой сделки между игроками (``Narrative.md``, раздел 9).
# Это число - вся причина, по которой золото не копится вечно: единственный отток,
# который растёт вместе с тем, сколько игроки на самом деле торгуют.
TRADE_TAX_PERCENT = 5


def roll_assortment(
    content: GameContent,
    *,
    world_seed: str,
    city_id: str,
    rotation: int,
    character_level: int,
    reputation: float = 0.0,
    strain: float = 0.0,
) -> tuple[Item, ...]:
    """Чем торгует этот город в этот переворот своего прилавка.

    Чем выше репутация, тем шире прилавок, и постоянный покупатель видит больше:
    ``reputation`` приходит процентами ``reputation_percent`` со всего, что на
    персонаже надето и выбрано (``REPUTATION_PER_STEP``).

    ``strain`` (0…1, ``mood.city_strain``, ADR 0055) — «нужда» города по состоянию
    округи вокруг: сужает прилавок, но не ниже ``STOCK_MIN``.

    Редкость решает, что вообще может лечь на прилавок. Снаряжение собирается во
    всех редкостях сразу (``domain/procgen/items.py``), и без веса лавка
    выкладывала бы легендарное так же часто, как обычное, а реликтовое — которое
    вообще не продаётся — заодно с ними.
    """
    source = rng(shop_seed(world_seed, city_id, rotation))
    low = max(1, character_level - LEVEL_WINDOW_BELOW)
    widened = int(max(0.0, reputation) / REPUTATION_PER_STEP)
    high = character_level + LEVEL_WINDOW_ABOVE + widened

    # Полка инструментов стоит до всякого броска и не зависит ни от сида, ни от
    # нужды города: сточенную кирку меняют там, где стоят.
    shelf = tool_stock(content, character_level=character_level)
    on_shelf = {item.id for item in shelf}

    candidates = [
        item
        for item in content.items
        if low <= item.level <= high
        and content.rarity(item.rarity).weight > 0
        and item.id not in on_shelf
    ]
    if not candidates:
        candidates = sorted(
            (item for item in content.items if content.rarity(item.rarity).weight > 0),
            key=lambda item: abs(item.level - character_level),
        )[:8]

    size = min(len(candidates), source.randint(STOCK_MIN, STOCK_MAX) + widened)
    pinch = max(0.0, min(1.0, strain))
    if pinch:
        size = max(min(STOCK_MIN, len(candidates)), size - round(size * STRAIN_STOCK_LOSS * pinch))
    chosen: list[Item] = []
    pool = list(candidates)
    weights = [content.rarity(item.rarity).weight for item in pool]
    for _ in range(size):
        picked = source.choices(range(len(pool)), weights=weights, k=1)[0]
        chosen.append(pool.pop(picked))
        weights.pop(picked)
    return tuple(sorted((*shelf, *chosen), key=lambda item: (item.level, item.name)))


def tool_stock(content: GameContent, *, character_level: int) -> tuple[Item, ...]:
    """Инструменты, которые лежат в лавке всегда: по два на каждое ремесло.

    Ступень берётся по уровню покупателя, как и всё на прилавке, а редкость
    решает одно - на сколько сборов инструмента хватит. Вида, которого нет в
    содержимом, здесь просто не будет: полка называет только то, что собрано.
    """
    tier = gear_procgen.tier_at(content, character_level)
    if tier is None:
        return ()
    stock: list[Item] = []
    for archetype in content.gear_archetypes:
        if not archetype.tool_type:
            continue
        for rarity_id in TOOL_RARITIES:
            item_id = gear_procgen.gear_id(archetype.id, tier.level, rarity_id)
            if content.has_item(item_id):
                stock.append(content.item(item_id))
    return tuple(stock)


def buy_price(
    content: GameContent,
    item: Item,
    *,
    modifiers: dict[str, float] | None = None,
    charisma: int = 0,
    strain: float = 0.0,
) -> int:
    """Цена в лавке после редкости, нужды города, харизмы и скидок от черт."""
    rarity = content.rarity(item.rarity)
    price = item.price * rarity.price_factor
    price *= 1.0 + STRAIN_PRICE_MARKUP * max(0.0, min(1.0, strain))

    discount = min(MAX_CHARISMA_DISCOUNT, charisma * CHARISMA_DISCOUNT_PER_POINT)
    if modifiers:
        # shop_price_percent отрицателен, когда он помогает, поэтому вычитается прямо.
        discount += -modifiers.get("shop_price_percent", 0.0)
    return max(1, round(price * max(0.4, 1.0 - discount / 100.0)))


def sell_price(
    content: GameContent, item: Item, *, modifiers: dict[str, float] | None = None
) -> int:
    """Сколько торговец платит за вещь, которую игрок принёс."""
    rarity = content.rarity(item.rarity)
    price = item.price * rarity.price_factor * SELL_FRACTION
    bonus = 1.0 + (modifiers.get("sell_price_percent", 0.0) if modifiers else 0.0) / 100.0
    return max(1, round(price * bonus))


def trade_tax(price: int, *, percent: int = TRADE_TAX_PERCENT) -> int:
    """Пошлина с закрытой сделки между двумя игроками.

    Берётся один раз, с цены, и уходит из игры целиком - её никто не получает.
    Передача даром (``передать``) сделкой не считается и не облагается: облагать
    подарок значило бы наказывать игроков за помощь друг другу, да и две передачи
    всё равно заменяют продажу. Это не лазейка, которую надо закрыть, а сделка без
    защиты подтверждением.

    Сделка с ценой всегда стоит хотя бы одну золотую, поэтому ливень продаж по
    монете не проведёт ценность мимо пошлины.
    """
    if price <= 0:
        return 0
    return max(1, round(price * percent / 100))


def payout(price: int, *, percent: int = TRADE_TAX_PERCENT) -> int:
    """Что продавец получает на самом деле: цена за вычетом пошлины."""
    return max(0, price - trade_tax(price, percent=percent))


def refund(price: int, tax: int) -> int:
    """Что возвращается плательщику, когда смотритель откатывает закрытую сделку.

    Ровно то, что было выдано продавцу, и ни монетой больше. Пошлины нет - она ушла
    из игры, когда сделка закрылась, и никто её не держит, - поэтому возврат полной
    цены напечатал бы эту пошлину из ничего, а это единственное, чего ни одному
    правилу в этом модуле делать нельзя (``Claude.md``, правило 8). Смотритель
    говорит игрокам об этом; разница мала, а другой выход - экономика, растущая
    каждый раз, когда кого-то обманули.
    """
    return max(0, price - max(0, tax))


# --- за что берёт город ---------------------------------------------
# Постель, учитель и сундук - три способа, которыми золото уходит от игрока, не
# доставаясь другому игроку. Все три растут с уровнем, потому что персонаж сорокового
# уровня зарабатывает за стражу столько, сколько персонаж четвёртого за десять.

INN_PRICE_BASE = 5
INN_PRICE_PER_LEVEL = 3
STRAW_HEAL_PERCENT = 30
MENTOR_PRICE_BASE = 40
MENTOR_PRICE_PER_LEVEL = 10
BANK_DEPOSIT_STEP = 50
# Дорога до другого города: платят за наёмную повозку и охрану в дорогу
# (``Narrative.md``, Дом Порубежья). Цена растёт и с уровнем, как постель и учитель,
# и с числом городов между тем, где стоишь, и тем, куда идёшь: дальний конец дороги
# стоит дороже ближнего соседа. Таймеров нет - дорога проходится сразу (ADR 0051).
TRAVEL_PRICE_BASE = 15
TRAVEL_PRICE_PER_LEVEL = 5


def travel_price(level: int, distance: int) -> int:
    """Плата за переход в другой город: ``distance`` - сколько городов между ними по дороге."""
    per_city = TRAVEL_PRICE_BASE + TRAVEL_PRICE_PER_LEVEL * max(0, level - 1)
    return max(1, per_city * max(1, distance))


def inn_price(level: int) -> int:
    """Ночь на постоялом дворе: полное здоровье по цене, которую гость потянет."""
    return max(1, INN_PRICE_BASE + INN_PRICE_PER_LEVEL * max(0, level - 1))


def mentor_price(level: int) -> int:
    """Сколько берёт учитель за то, чтобы распустить ранг или грань и вернуть очко."""
    return max(1, MENTOR_PRICE_BASE + MENTOR_PRICE_PER_LEVEL * max(0, level - 1))


def affordable(items: Sequence[Item], gold: int, prices: dict[str, int]) -> tuple[Item, ...]:
    return tuple(item for item in items if prices.get(item.id, 0) <= gold)


def charisma_of(stats: object) -> int:
    """Прочитать харизму из набора характеристик, не импортируя StatCode у вызывающего."""
    from mmorpg.domain.entities.stats import StatBlock

    if isinstance(stats, StatBlock):
        return stats[StatCode.CHA]
    return 0
