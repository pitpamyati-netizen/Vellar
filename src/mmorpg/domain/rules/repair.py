"""Прочность снаряжения и кузница, которая её возвращает.

Инструмент в Vellar стачивается о работу (ADR 0056), а всё остальное надетое до
сих пор не стачивалось ни обо что: доспех первой ступени доходил в целости до
трёхсотого уровня, и единственным, что уводило золото из кошелька бывалого
игрока, оставалась койка на постоялом дворе.

Здесь у снаряжения появляется тот же счёт, и он идёт по трём правилам.

1. **Прочность считается от уровня вещи и её редкости.** Число печётся в вещь
   при сборке (``procgen/items.build``): ступень даёт основу, редкость множит
   (``Rarity.toughness``). Ветхий меч обычной работы держит сорок боёв, кованый
   именной славы - под две сотни.
2. **Точит бой.** Выигранный - на единицу, проигранный - втрое
   (``domain/rules/adventure.py``). Точится всё надетое разом, кроме инструмента:
   его точит своя работа, и два счётчика на одну вещь разошлись бы в первый же
   день.
3. **Сточенная вещь не работает.** Ни брони, ни костей, ни прибавок, ни даже
   штрафа за чужой род: сломанный доспех - это доспех, которого на бойце нет.
   Он не пропадает и не снимается: его чинят в кузнице любого города, и цена
   починки идёт от цены самой вещи (``economy.repair_price``).

Инструмент кузница не берёт: сточенную кирку меняют в лавке, и это единственное,
чем прочность инструмента отличается от прочности меча.

Модуль чистый: ни времени, ни случая, ни ввода-вывода.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import economy
from mmorpg.domain.rules.tools import TOOL_SLOT

#: Чего стоит бой надетому. Проигранный дороже: с поля боя выносят не только
#: раны, и десятой доли кошелька за поражение мало, чтобы игрок берёгся.
WEAR_PER_FIGHT = 1
WEAR_ON_DEFEAT = 3

#: С какой доли прочности вещь считается изношенной настолько, что об этом стоит
#: сказать вслух. Сломаться в бою она может, а вот сломаться незаметно - нет.
LOW_PERCENT = 20


def worn_of(content: GameContent, character: Character, item_id: str) -> Item | None:
    """Надетая вещь такой, какая она на этом герое, или ``None``, если она не точится.

    Не точится то, чего в игре больше нет, инструмент (у него свой счёт) и всё,
    у чего прочности нет вовсе. Реликтовая вещь пересобирается по уровню героя:
    она растёт вместе с ним целиком, прочностью в том числе.
    """
    if not content.has_item(item_id):
        return None
    item = gear_procgen.worn(content, content.item(item_id), character.level)
    if not item.is_equipment or item.is_tool or item.durability <= 0:
        return None
    return item


def limit(item: Item) -> int:
    """Сколько боёв эта вещь держит всего."""
    return max(0, item.durability)


def spent(character: Character, item: Item) -> int:
    """Сколько прочности с неё уже сточено. Выше предела не поднимается."""
    return min(character.wear.spent(item.id), limit(item))


def left(character: Character, item: Item) -> int:
    """Сколько боёв в ней ещё осталось."""
    return max(0, limit(item) - character.wear.spent(item.id))


def is_broken(character: Character, item: Item) -> bool:
    """Сточена ли вещь до конца. Сломанная не даёт и не стоит ничего."""
    return limit(item) > 0 and left(character, item) <= 0


def gear_on(content: GameContent, character: Character) -> tuple[Item, ...]:
    """Всё надетое, что стачивается о бои, - в порядке слотов.

    Порядок берётся у содержимого, а не у записи персонажа: снаряжение лежит в
    отображении, и его порядок ничего не значит, а список на экране обязан быть
    одинаковым от нажатия к нажатию (правила доступности 6).
    """
    found: list[Item] = []
    for slot in content.slots:
        if slot.id == TOOL_SLOT:
            continue
        item_id = character.equipment.item_in(slot.id)
        if item_id is None:
            continue
        item = worn_of(content, character, item_id)
        if item is not None:
            found.append(item)
    return tuple(found)


def working_ids(content: GameContent, character: Character) -> tuple[str, ...]:
    """Что из надетого сейчас работает: сломанное в счёт не идёт.

    Через это проходят броня, прибавки и цена чужого рода: сломанная вещь должна
    исчезать из чисел вся целиком, иначе игрок читает на экране одно, а получает
    в бою другое (``Claude.md``, правило 7).
    """
    working: list[str] = []
    for item_id in character.equipment.item_ids():
        item = worn_of(content, character, item_id)
        if item is None or not is_broken(character, item):
            working.append(item_id)
    return tuple(working)


def broken_on(content: GameContent, character: Character) -> tuple[Item, ...]:
    """Что из надетого сломано прямо сейчас."""
    return tuple(item for item in gear_on(content, character) if is_broken(character, item))


def price_of(character: Character, item: Item) -> int:
    """Во сколько станет починить эту вещь до целой."""
    return economy.repair_price(item.price, spent(character, item), limit(item))


def bill(content: GameContent, character: Character) -> tuple[tuple[Item, int], ...]:
    """Счёт кузницы: что чинить и почём. Целые вещи в счёт не попадают."""
    return tuple(
        (item, price_of(character, item))
        for item in gear_on(content, character)
        if spent(character, item) > 0
    )


def total(entries: Iterable[tuple[Item, int]]) -> int:
    """Сколько стоит починить всё разом. Складывается, а не скидывается: кузнец
    берёт за работу, а не за визит."""
    return sum(price for _, price in entries)


def repaired(character: Character, items: Iterable[Item]) -> Character:
    """Вернуть названным вещам всю прочность. Запись об износе уходит целиком."""
    wear = character.wear
    for item in items:
        wear = wear.cleared(item.id)
    return replace(character, wear=wear)


def wear(
    content: GameContent, character: Character, amount: int = WEAR_PER_FIGHT
) -> tuple[Character, tuple[Item, ...]]:
    """Стереть всё надетое на ``amount``. Возвращает персонажа и то, что сломалось.

    Сломанное остаётся на бойце: снять его игра сама не вправе, а вот считать
    его - уже нет (``working_ids``). Уже сломанное дальше не точится: сточить
    вещь вдвое и брать за это вдвое было бы платой за то, чего не было.
    """
    if amount <= 0:
        return character, ()
    updated = character
    broke: list[Item] = []
    for item in gear_on(content, character):
        if is_broken(character, item):
            continue
        updated = replace(updated, wear=updated.wear.worn(item.id, amount))
        if is_broken(updated, item):
            broke.append(item)
    return updated, tuple(broke)
