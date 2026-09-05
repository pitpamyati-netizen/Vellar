"""Инструмент: чем берут сырьё и на сколько его хватает.

Сырьё лежит там, где ему и место, - в узлах локации, - и взять его можно
только тем, чем его берут (ADR 0056). Три правила, и все три здесь.

1. **Без инструмента сбора нет.** Пустой слот «Инструмент» - это отказ, а не
   меньшая добыча: голыми руками руду не выламывают.
2. **Род инструмента решает, что им берут.** Кирка берёт руду, серп - травы и
   волокно, нож - шкуры, удочка - рыбу, топор - лес (``items.toml``,
   ``tool_types``). Узел, который называет своё сырьё, требует и своего
   инструмента; узел, который его не называет, отдаёт то, что берёт инструмент
   в руках.
3. **Инструмент стачивается.** Каждый сбор стоит одной единицы прочности, а
   сколько её всего, решает редкость (``Rarity.durability``). Сточенный
   инструмент исчезает - в кузнице чинят снаряжение, а не его (ADR 0057), - и
   новый лежит в лавке всегда.

Модуль чистый: ни времени, ни случая, ни ввода-вывода.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item, ToolType

#: Слот, в котором лежит инструмент. Он не оружие: инструментом не дерутся, и в
#: руке он не мешает ничему.
TOOL_SLOT = "tool"


def tool_of(content: GameContent, character: Character) -> Item | None:
    """Инструмент, надетый сейчас, или ``None``, если слот пуст.

    Вещь, которой в содержимом больше нет, - это тоже пустой слот: сохранённому
    состоянию не верят (``Claude.md``, правило 8).
    """
    item_id = character.equipment.item_in(TOOL_SLOT)
    if item_id is None or not content.has_item(item_id):
        return None
    item = content.item(item_id)
    return item if item.is_tool else None


def _kind(content: GameContent, item: Item) -> ToolType | None:
    """Род этого инструмента, или ``None``, если это вообще не инструмент.

    Одна проверка на весь модуль: вещь без рода не берёт ничего, и каждый ответ
    о ней - «ничем и никак», а не исключение.
    """
    if not item.is_tool or not content.has_tool_type(item.tool_type):
        return None
    return content.tool_type(item.tool_type)


def limit(item: Item) -> int:
    """Сколько сборов этот инструмент держит всего. Ноль - это не инструмент.

    Число печётся в вещь при сборке из редкости (``procgen/items.build``):
    больше редкость инструменту не даёт ничего. Прочность есть теперь и у
    снаряжения, но её точат бои и возвращает кузница (``domain/rules/repair.py``),
    и сборов она не держит ни одного.
    """
    return item.durability if item.is_tool else 0


def left(content: GameContent, character: Character, item: Item) -> int:
    """Сколько сборов у этого инструмента осталось у этого персонажа."""
    return max(0, limit(item) - character.wear.spent(item.id))


def sources_of(content: GameContent, item: Item) -> tuple[str, ...]:
    """Какое сырьё берут этим инструментом."""
    kind = _kind(content, item)
    return kind.sources if kind is not None else ()


def craft_of(content: GameContent, item: Item) -> str:
    """В каком ремесле записывается работа этим инструментом. Пусто - ни в каком."""
    kind = _kind(content, item)
    return kind.craft if kind is not None else ""


def type_name(content: GameContent, item: Item) -> str:
    """Как называется род этого инструмента: «кирка», «серп»."""
    kind = _kind(content, item)
    return kind.name.lower() if kind is not None else "инструмент"


def takes(content: GameContent, item: Item, source: str) -> bool:
    """Берётся ли этим инструментом такое сырьё. Безымянное сырьё берут любым."""
    kind = _kind(content, item)
    return kind.takes(source) if kind is not None else False


def needed_for(content: GameContent, source: str) -> str:
    """Каким инструментом берут это сырьё - словами. Пусто - любым."""
    if not source:
        return ""
    names = [kind.name.lower() for kind in content.tool_types if source in kind.sources]
    return ", ".join(names)


def refusal(content: GameContent, character: Character, source: str = "") -> str:
    """Почему сейчас нельзя собирать - словами. Пусто, когда можно.

    ``source`` - сырьё, которое лежит в узле (``Item.source``). Пусто значит «что
    угодно»: такому узлу годится всякий инструмент, а что именно из него выйдет,
    решит уже инструмент.
    """
    item = tool_of(content, character)
    if item is None:
        return (
            "Собирать нечем: инструмент надевается в слот «Инструмент». "
            "Кирку, серп, нож, удочку и топор продают в любой лавке."
        )
    if left(content, character, item) <= 0:
        return f"{item.name}: сточен до конца. Нужен новый - их продают в лавке."
    if not takes(content, item, source):
        wanted = needed_for(content, source)
        held = type_name(content, item)
        tail = f" Здесь нужен другой инструмент: {wanted}." if wanted else ""
        return f"Этим не взять: в руках {held}, а тут {source}.{tail}"
    return ""


def wear(
    content: GameContent, character: Character, item: Item, amount: int = 1
) -> tuple[Character, bool]:
    """Стереть инструмент на одну работу. Возвращает персонажа и то, сточился ли он.

    Сточенный инструмент снимается и исчезает вместе со своей записью об износе:
    инструмент не чинят даже в кузнице (ADR 0057), а запись, пережившая вещь,
    сделала бы новую кирку сточенной с первого удара.
    """
    spent = character.wear.spent(item.id) + max(0, amount)
    if spent < limit(item):
        return replace(character, wear=character.wear.worn(item.id, amount)), False
    return (
        replace(
            character,
            equipment=character.equipment.unequip(TOOL_SLOT),
            wear=character.wear.cleared(item.id),
        ),
        True,
    )
