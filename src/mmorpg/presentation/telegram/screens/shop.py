"""Inventory and shop, both built on the shared paginated list.

They look and behave identically to every other long list in the game: eight
entries per page, one per row, the same header wording and the same navigation
row in the same place (spec section 13).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.presentation.telegram.keyboards.labels import SELL as SELL_ITEMS
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import gold as gold_words
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

BUY_SUFFIX = "купить"
USE_SUFFIX = "использовать"
EQUIP_SUFFIX = "надеть"
SELL_SUFFIX = "продать"

# What a player can do with a thing, by its kind. Pressing a row no longer does
# it, though: it opens the thing's card, where the verb is a button and the
# numbers are spelled out (``screens/items.py``).
ITEM_VERBS: dict[str, str] = {
    "equipment": EQUIP_SUFFIX,
    "consumable": USE_SUFFIX,
    "material": "сырьё",
}

#: Разделы, по которым режется любой список вещей. Названия - те же слова, что
#: игрок слышит в карточке, а не ключи содержимого.
SECTION_BY_KIND: dict[str, str] = {
    "equipment": "Снаряжение",
    "consumable": "Расходники",
    "material": "Сырьё",
}
ITEM_SECTIONS: tuple[str, ...] = ("Снаряжение", "Расходники", "Сырьё")


@dataclass(frozen=True, slots=True)
class OwnedItem:
    item_id: str
    quantity: int


def matches_filters(item: Item, state: PageState, content: GameContent) -> bool:
    """Раздел, поиск и уровни. Поиск идёт и по названию, и по описанию.

    Игрок помнит вещь по тому, что она делает («лечение»), а не по её имени, и
    должен находить её именно так.
    """
    filters = state.filters
    if filters.rarity and content.rarity(item.rarity).name != filters.rarity:
        return False
    if filters.category and SECTION_BY_KIND.get(item.kind.value) != filters.category:
        return False
    if filters.query:
        needle = filters.query.casefold().strip()
        if needle not in item.name.casefold() and needle not in item.text.casefold():
            return False
    if filters.level_min and item.level < filters.level_min:
        return False
    return not (filters.level_max and item.level > filters.level_max)


def inventory_screen(
    content: GameContent,
    owned: Sequence[OwnedItem],
    state: PageState,
    *,
    gold: int,
    notice: str = "",
) -> Screen:
    entries = [
        ListEntry(
            key=content.item(held.item_id).id,
            text=inventory_text(content.item(held.item_id), held.quantity),
            detail=f"{content.rarity(content.item(held.item_id).rarity).name.lower()}, "
            f"уровень {content.item(held.item_id).level}",
        )
        for held in owned
        if matches_filters(content.item(held.item_id), state, content)
    ]
    return paginated_screen(
        screen_id=ScreenId.INVENTORY,
        title="Инвентарь",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"У вас {gold_words(gold)}.",
            "Нажмите вещь, чтобы открыть её карточку: что даёт, чем лучше "
            "надетого и что с ней сделать.",
        ),
        empty_text="В инвентаре пусто.",
        categories=ITEM_SECTIONS,
    )


def inventory_text(item: Item, quantity: int) -> str:
    """The button text of one bag row: the thing, how many, and what it can do."""
    verb = ITEM_VERBS.get(item.kind.value, "")
    tail = f" — {verb}" if verb else ""
    return f"{item.name}, штук {quantity}{tail}"


def sell_screen(
    content: GameContent,
    owned: Sequence[OwnedItem],
    prices: dict[str, int],
    state: PageState,
    *,
    gold: int,
    city_name: str,
    notice: str = "",
) -> Screen:
    """What the merchant pays for what is in the bag.

    Equipment that is worn is not here: it is not in the bag, and selling the
    boots off your own feet by a mis-press is exactly the accident to prevent.
    """
    entries = [
        ListEntry(
            key=content.item(held.item_id).id,
            text=sell_text(content.item(held.item_id), prices.get(held.item_id, 1)),
            detail=f"в сумке {held.quantity}",
        )
        for held in owned
        if matches_filters(content.item(held.item_id), state, content)
    ]
    return paginated_screen(
        screen_id=ScreenId.SELL,
        title=f"Скупка, {city_name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Лавочник берёт дешевле, чем продаёт. У вас {gold_words(gold)}.",
            "Продаётся по одной штуке за нажатие.",
        ),
        empty_text="Продавать нечего.",
        categories=ITEM_SECTIONS,
    )


def sell_text(item: Item, price: int) -> str:
    return f"{item.name} — {price} золота, {SELL_SUFFIX}"


def shop_screen(
    content: GameContent,
    stock: Sequence[Item],
    prices: dict[str, int],
    state: PageState,
    *,
    gold: int,
    city_name: str,
    notice: str = "",
) -> Screen:
    entries: list[ListEntry] = []
    for item in stock:
        if not matches_filters(item, state, content):
            continue
        price = prices.get(item.id, item.price)
        affordable = "хватает золота" if price <= gold else "не хватает золота"
        entries.append(
            ListEntry(
                key=item.id,
                text=f"{item.name} — {price} золота, {BUY_SUFFIX}",
                detail=f"уровень {item.level}, {content.rarity(item.rarity).name.lower()}, "
                f"{affordable}",
            )
        )
    return paginated_screen(
        screen_id=ScreenId.SHOP,
        title=f"Лавка города {city_name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"У вас {gold_words(gold)}.",
            "Нажмите товар, чтобы узнать, что он даёт и чем отличается от "
            "надетого. Ассортимент лавки меняется раз в полчаса.",
        ),
        empty_text="Сегодня лавка пуста.",
        extra_rows=((SELL_ITEMS,),),
        categories=ITEM_SECTIONS,
    )


def buy_label(item: Item, price: int) -> Label:
    return label(f"{item.name} — {price} золота, {BUY_SUFFIX}")


def item_from_button(content: GameContent, text: str, stock: Sequence[Item]) -> Item | None:
    """Match a pressed shop button back to its item."""
    for item in stock:
        if text.startswith(f"{item.name} — ") and text.endswith(BUY_SUFFIX):
            return item
    return None


def owned_from_button(content: GameContent, text: str, owned: Sequence[OwnedItem]) -> Item | None:
    for held in owned:
        item = content.item(held.item_id)
        if text.startswith(f"{item.name}, штук "):
            return item
    return None


def sold_from_button(content: GameContent, text: str, owned: Sequence[OwnedItem]) -> Item | None:
    """Match a pressed skup button back to its item."""
    for held in owned:
        item = content.item(held.item_id)
        if text.startswith(f"{item.name} — ") and text.endswith(SELL_SUFFIX):
            return item
    return None
