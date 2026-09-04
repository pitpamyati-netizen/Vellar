"""Экраны ремёсел: что приключенец умеет делать и как далеко зашёл.

Два экрана, и не больше:

- **Ремёсла** — все ремёсла, ранг каждого и работа, оставшаяся до следующего;
- **одно ремесло** — либо рассказ о том, чем и где это ремесло работает, либо
  рецепты, каждый из которых прямо на себе говорит, что берёт и что даёт
  (правило доступности 5).

Кнопки сбора здесь нет. Сырьё берут там, где оно лежит, — в узлах локации и
только инструментом (``domain/rules/tools.py``, ADR 0056); экран собирающего
ремесла говорит, чем его берут и что за это записывают, но сам не работает
руками игрока.

Кнопка рецепта никогда не врёт о том, что её можно нажать: неоплатный рецепт
остаётся на своём месте и отвечает тем, чего не хватает, — по имени и по числу.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import Craft, Recipe
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.domain.rules import tools as tool_rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

# Что есть у игрока в том виде, в каком это передаёт ветка: вещь - и сколько её в сумке.
Owned = dict[str, int]


def rank_line(content: GameContent, character: Character, craft: Craft) -> str:
    """Сначала ранг, потом оставшаяся работа - два числа, о которых спрашивает игрок."""
    rules = content.craft_rules
    experience = character.crafts.progress(craft.id).experience
    rank = craft_rules.rank_of(rules, experience)
    name = craft_rules.rank_name(rules, rank)
    done, needed = craft_rules.into_rank(rules, experience)
    if not needed:
        return f"ранг {rank} из {rules.max_rank}, {name}, выше некуда"
    return f"ранг {rank} из {rules.max_rank}, {name}, до следующего {needed - done} работы"


def craft_button(content: GameContent, character: Character, craft: Craft) -> Label:
    return label(f"{craft.name} — {rank_line(content, character, craft)}")


def crafts_screen(content: GameContent, character: Character, notice: str = "") -> Screen:
    """Список ремёсел. Всё, что в нём есть, может выучить кто угодно."""
    lines = [
        *head("Ремёсла.", notice),
        "Сырьё берут в локациях, из сырья делают вещи, вещи идут в дело или в лавку.",
        "Ремесло не выбирают раз и навсегда: беритесь за любое, ранг растёт от работы.",
        "Собирают инструментом: без него жила не даётся. Инструменты есть в любой лавке.",
    ]
    rows = [(craft_button(content, character, craft),) for craft in content.crafts]
    return Screen(id=ScreenId.CRAFTS, lines=tuple(lines), rows=tuple(rows))


def recipe_button(content: GameContent, recipe: Recipe) -> Label:
    """Рецепт называет свою цену в сырье до того, как его нажали."""
    needs = ", ".join(f"{content.item(need.item_id).name} {need.count}" for need in recipe.inputs)
    return label(f"{content.item(recipe.output_id).name} — нужно: {needs}")


def craft_screen(
    content: GameContent,
    character: Character,
    craft: Craft,
    owned: Owned,
    *,
    biomes: frozenset[str] = frozenset(),
    place: str = "",
    notice: str = "",
) -> Screen:
    """Одно ремесло: чем и где в нём работают или какие рецепты оно знает.

    ``biomes`` и ``place`` - земля вокруг города, в котором стоит игрок, и её имя:
    что найдёт собирающее ремесло, зависит от того, где им работают
    (``domain/rules/crafts.yields_here``).
    """
    rank = craft_rules.character_rank(content, character, craft.id)
    lines = [
        *head(f"{craft.name}: {rank_line(content, character, craft)}.", notice),
        craft.description,
    ]
    rows: list[tuple[Label, ...]] = []

    if craft.gathers:
        lines.extend(gathering_lines(content, character, craft, biomes=biomes, place=place))
        return Screen(id=ScreenId.CRAFT, lines=tuple(lines), rows=tuple(rows))

    recipes = content.recipes_of(craft.id)
    open_now = [recipe for recipe in recipes if recipe.rank <= rank]
    later = len(recipes) - len(open_now)
    lines.append(f"Рецептов открыто: {len(open_now)} из {len(recipes)}.")
    if later:
        lines.append(f"Ещё {later} откроется с рангом.")
    lines.append("Материалы уходят из сумки, готовое кладут туда же.")
    rows.extend((recipe_button(content, recipe),) for recipe in open_now)
    return Screen(id=ScreenId.CRAFT, lines=tuple(lines), rows=tuple(rows))


def gathering_lines(
    content: GameContent,
    character: Character,
    craft: Craft,
    *,
    biomes: frozenset[str] = frozenset(),
    place: str = "",
) -> tuple[str, ...]:
    """Что это ремесло берёт, где и чем. Работать отсюда нельзя - и об этом сказано."""
    brought = ", ".join(
        content.item(entry.item_id).name
        for entry in craft.yields
        if entry.level <= character.level and (not biomes or entry.found_in(biomes))
    )
    where = f" вокруг города {place}" if place else ""
    lines = [f"Берут{where}: {brought}." if brought else f"Для вашего уровня{where} пусто."]
    elsewhere = ", ".join(
        content.item(entry.item_id).name
        for entry in craft.yields
        if entry.level <= character.level and biomes and not entry.found_in(biomes)
    )
    if elsewhere:
        lines.append(f"В других краях этим ремеслом берут и другое: {elsewhere}.")

    wanted = ", ".join(kind.name for kind in content.tool_types if kind.craft == craft.id)
    if wanted:
        lines.append(f"Работают этим: {wanted}. Инструмент покупают в лавке и надевают в слот.")
    lines.append(
        "Сырьё берут в локации: идите в узел, где есть чем поживиться, и нажмите "
        "«Собрать сырьё» там. Отсюда работать нельзя."
    )
    lines.append(tool_line(content, character))
    return tuple(line for line in lines if line)


def tool_line(content: GameContent, character: Character, source: str = "") -> str:
    """Что сейчас в слоте инструмента и надолго ли его хватит. Одна строка.

    ``source`` - сырьё, о котором спрашивают (``Item.source``): пусто значит «о
    любом». Отказ говорится теми же словами, что и на месте работы, - игрок не
    должен узнавать о пустом слоте, стоя над жилой.
    """
    refused = tool_rules.refusal(content, character, source)
    if refused:
        return refused
    item = tool_rules.tool_of(content, character)
    if item is None:  # pragma: no cover - отказ выше уже сказал бы об этом
        return ""
    left = tool_rules.left(content, character, item)
    return f"В руках: {item.name}. Сборов осталось {left} из {tool_rules.limit(item)}."


def gathered_line(content: GameContent, item_id: str, count: int, experience: int) -> str:
    """Что принесла одна отработанная жила, одной фразой."""
    item = content.item(item_id)
    work = f" Работы записано: {experience}." if experience else ""
    return f"Собрано: {item.name}, {count} штук.{work}"


def made_line(content: GameContent, result: craft_rules.CraftResult) -> str:
    """Что вышло из партии, сначала качество, потому что оно и есть неожиданность."""
    if not result.ok:
        return result.refused
    item = content.item(result.item_id)
    quality = result.quality.name.lower() if result.quality is not None else "обычное"
    spent = ", ".join(
        f"{content.item(item_id).name} {abs(count)}" for item_id, count in result.spent
    )
    return (
        f"Сделано: {item.name}, {result.count} штук, качество {quality}. "
        f"Ушло: {spent}. Работы записано: {result.experience}."
    )
