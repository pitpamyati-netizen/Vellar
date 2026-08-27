"""Адресная передача вещи внутри отряда или гильдии.

Три экрана, общих для обоих объединений: кому передать, что передать и — если
это стопка — сколько. Какой это контекст (отряд или гильдия), экран не знает и
знать не должен: он получает уже готовый список имён и сумку, а связывает всё
хендлер (``handlers/play._transfer_step``).

Передача мгновенна и получателя ни о чём не спрашивает — как и `передать` в
игровой группе (``Narrative.md``, раздел 9). Пошлины и эскроу здесь нет:
передача из рук в руки ими не облагается.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.content import GameContent, Item
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head, plural
from mmorpg.presentation.telegram.screens.paginated import ListEntry, PageState, paginated_screen
from mmorpg.presentation.telegram.screens.shop import ITEM_SECTIONS, OwnedItem, matches_filters

#: Как называется объединение в тексте экрана.
SCOPE_WORD: dict[str, str] = {"party": "отряд", "guild": "гильдия"}

_LEAD = {
    "party": "Передать можно любому, кто идёт с вами в отряде.",
    "guild": "Передать можно любому соклановцу.",
}


def recipients_screen(
    scope: str, names: Sequence[str], state: PageState, notice: str = ""
) -> Screen:
    """Кому передать: по кнопке на каждого соратника или соклановца."""
    entries = [ListEntry(key=name, text=name) for name in names]
    return paginated_screen(
        screen_id=ScreenId.TRANSFER_TO,
        title="Кому передать вещь",
        entries=entries,
        state=state,
        lead_lines=(notice or _LEAD.get(scope, _LEAD["party"]),),
        empty_text="Передавать некому: рядом никого нет.",
    )


def bag_screen(
    content: GameContent,
    owned: Sequence[OwnedItem],
    to_name: str,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Что передать: вещи из сумки. Стопку экран количества спросит следом."""
    entries = [
        ListEntry(
            key=content.item(held.item_id).id,
            text=item_button_text(content.item(held.item_id), held.quantity),
            detail=f"{content.rarity(content.item(held.item_id).rarity).name.lower()}, "
            f"уровень {content.item(held.item_id).level}",
        )
        for held in owned
        if content.has_item(held.item_id)
        and matches_filters(content.item(held.item_id), state, content)
    ]
    return paginated_screen(
        screen_id=ScreenId.TRANSFER_ITEM,
        title="Что передать",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Получатель: {to_name}.",
            "Нажмите вещь. Стопку — потом спросит, сколько передать.",
        ),
        empty_text="В сумке пусто: передавать нечего.",
        categories=ITEM_SECTIONS,
    )


def item_button_text(item: Item, quantity: int) -> str:
    """Текст кнопки вещи в списке передачи."""
    return f"{item.name}, штук {quantity}"


def item_from_button(content: GameContent, text: str, owned: Sequence[OwnedItem]) -> Item | None:
    """Свести нажатую кнопку обратно к вещи из сумки."""
    for held in owned:
        if not content.has_item(held.item_id):
            continue
        item = content.item(held.item_id)
        if text.startswith(f"{item.name}, штук "):
            return item
    return None


def amount_screen(item: Item, held: int, to_name: str, notice: str = "") -> Screen:
    """Сколько передать. Число вводят сообщением; «Передать всё» — быстрый путь."""
    lines = [
        *head(f"Сколько передать: {item.name}.", notice),
        f"Получатель: {to_name}. В сумке {held} {plural(held, 'штука', 'штуки', 'штук')}.",
        "Наберите число сообщением или нажмите «Передать всё».",
    ]
    rows: tuple[tuple[Label, ...], ...] = ((labels.TRANSFER_ALL,),)
    return Screen(id=ScreenId.TRANSFER_AMOUNT, lines=tuple(lines), rows=rows)
