"""Что кузница делает с вещью, кроме починки: разбирает и перековывает (ADR 0060).

Обе работы про одно - что снаряжение не тупик.

- **Разбор** (``salvage``) возвращает вещь в сырьё: род вещи решает, во что она
  разбирается, ступень - какое сырьё это будет, а редкость и цена - сколько его
  выйдет. Платит меньше скупки: разбирают не ради выгоды, а ради того, чего в
  лавке нет.
- **Перековка** (``reforge``) меняет вещи оттиск: тот же вид, та же ступень, та
  же редкость - другой ведущий аффикс, другое имя, другие числа (ADR 0059). Это
  ответ на «выпало не то».

Модуль чистый: ни времени, ни хранилищ, ни глобальной случайности - источник
броска приходит аргументом.
"""

from __future__ import annotations

from collections.abc import Mapping
from random import Random

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules.crafts import SALVAGE_YIELD_KEY
from mmorpg.domain.rules.modifiers import percent

#: Какая доля цены вещи возвращается сырьём. Меньше, чем платит скупка
#: (``economy.SELL_FRACTION``): разбор - это способ получить материал, а не
#: способ получить золото окольным путём.
SALVAGE_SHARE = 0.08

#: Больше этого одна вещь сырья не даёт, сколько бы она ни стоила. Без потолка
#: легендарка последней ступени высыпала бы сотню кусков руды, и весь смысл
#: собирать её самому пропал бы.
SALVAGE_MAX = 12

#: Во что разбирается вещь: род - источник сырья. Железо идёт в железо, кожа в
#: кожу, тканое в волокно, а украшение - в руду, потому что ничего другого в нём
#: нет. Цеха и здесь не путаются: что кузнец сковал, то в руду и вернётся
#: (ADR 0062).
_ARMOR_SOURCES: dict[str, str] = {
    "heavy": "руда",
    "medium": "руда",
    "light": "шкуры",
    "cloth": "волокно",
}
#: Оружие, у которого главное - древко или ложе, разбирается в древесину, а не в
#: железо: с лука снимают не сталь.
_WOODEN_WEAPONS: frozenset[str] = frozenset({"bow", "crossbow", "staff", "wand", "totem"})
_WEAPON_SOURCE = "руда"
_WOOD_SOURCE = "древесина"
_FALLBACK_SOURCE = "руда"

#: Сколько стоит перековка - долей от цены самой вещи. Дорого нарочно: перековка
#: не должна быть дешевле того, чтобы сходить и выбить другую вещь.
REFORGE_SHARE = 0.35


def source_of(content: GameContent, item: Item) -> str:
    """Каким сырьём эта вещь была до того, как стала вещью."""
    if item.is_armor:
        return _ARMOR_SOURCES.get(item.armor_type, _FALLBACK_SOURCE)
    if item.is_weapon:
        return _WOOD_SOURCE if item.weapon_type in _WOODEN_WEAPONS else _WEAPON_SOURCE
    return _FALLBACK_SOURCE


def _material(content: GameContent, source: str, level: int) -> str:
    """Лучшее сырьё этого рода, какое берут на такой глубине. Пусто - никакого."""
    found = ""
    best = -1
    for craft in content.crafts:
        if not craft.gathers:
            continue
        for entry in craft.yields:
            if entry.level > level or entry.level <= best:
                continue
            if not content.has_item(entry.item_id):
                continue
            if content.item(entry.item_id).source != source:
                continue
            found, best = entry.item_id, entry.level
    return found


def can_salvage(content: GameContent, character: Character, item: Item) -> str:
    """Пусто, когда вещь можно разобрать, иначе - почему нельзя, словами."""
    if not item.is_equipment:
        return "Разбирают снаряжение: сырьё и зелья разбирать не во что."
    if item.is_tool:
        return "Инструмент не разбирают: сточенный меняют в лавке."
    if character.equipment.item_in(item.slot) == item.id:
        return "Эта вещь на вас надета. Снимите её, потом разбирайте."
    if not yield_of(content, item):
        return "Из этой вещи ничего не выходит: разбирать её незачем."
    return ""


def yield_of(
    content: GameContent, item: Item, *, modifiers: Mapping[str, float] | None = None
) -> tuple[tuple[str, int], ...]:
    """Что выйдет из разобранной вещи: сырьё и сколько его.

    ``salvage_yield_percent`` читается здесь - тот самый ключ, который «Разборщик»
    обещал с самого начала и который до сих пор считало только качество партии
    (``rules/crafts``).
    """
    # Своего сырья на мелкой ступени может не быть вовсе - руду берут глубже
    # третьего уровня, - и тогда вещь разбирается в лом: то, что остаётся от
    # всего и всегда.
    material = _material(content, source_of(content, item), item.level) or _material(
        content, _FALLBACK_SOURCE, item.level
    )
    if not material or not content.has_item(material):
        return ()
    price = max(1, content.item(material).price)
    bonus = percent(modifiers or {}, SALVAGE_YIELD_KEY)
    amount = round(item.price * SALVAGE_SHARE * max(0.0, bonus) / price)
    return ((material, max(1, min(SALVAGE_MAX, amount))),)


def reforge_price(content: GameContent, item: Item) -> int:
    """Во что обойдётся перековка этой вещи. Ноль - перековывать нечего."""
    if not can_reforge(content, item):
        return 0
    return max(1, round(item.price * REFORGE_SHARE))


def can_reforge(content: GameContent, item: Item) -> bool:
    """Есть ли у этой вещи что перековывать: прибавки и оттиск."""
    if not item.is_equipment or item.is_tool:
        return False
    if not content.has_rarity(item.rarity) or not content.rarity(item.rarity).affixes:
        return False
    return gear_procgen.parse_gear_id(item.id) is not None


def reforged(content: GameContent, item_id: str, *, source: Random) -> str:
    """Та же вещь с другим оттиском. Тот же id - перековывать было нечего.

    Оттиск всегда новый: заплатить за работу и получить ровно то же, что принёс,
    - это не работа, а пошлина.
    """
    parsed = gear_procgen.parse_gear_id(item_id)
    if parsed is None:
        return item_id
    archetype_id, level, rarity_id, roll = parsed
    rolls = max(1, gear_procgen.rolls_of(content))
    if rolls < 2:
        return item_id
    picked = source.randrange(rolls - 1)
    return gear_procgen.gear_id(
        archetype_id, level, rarity_id, picked if picked < roll else picked + 1
    )
