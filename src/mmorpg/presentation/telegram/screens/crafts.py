"""Экраны ремёсел: что приключенец умеет делать и как далеко зашёл.

Два экрана, и не больше:

- **Ремёсла** — все ремёсла, ранг каждого и работа, оставшаяся до следующего;
- **одно ремесло** — либо единственная кнопка сбора, либо рецепты, каждый из
  которых прямо на себе говорит, что берёт и что даёт (правило доступности 5).

Кнопка рецепта никогда не врёт о том, что её можно нажать: неоплатный рецепт
остаётся на своём месте и отвечает тем, чего не хватает, — по имени и по числу.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import Craft, Recipe
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.presentation.telegram.keyboards import labels
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
        "Сырьё собирают руками, из сырья делают вещи, вещи идут в дело или в лавку.",
        "Ремесло не выбирают раз и навсегда: беритесь за любое, ранг растёт от работы.",
        "Собирать можно раз в четверть часа: сырьё родится заново само.",
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
    now: int,
    cooldown: int,
    biomes: frozenset[str] = frozenset(),
    place: str = "",
    notice: str = "",
) -> Screen:
    """Одно ремесло: кнопка сбора или рецепты, которые оно знает.

    ``biomes`` и ``place`` - земля вокруг города, в котором стоит игрок, и её имя:
    что найдёт собирающее ремесло, зависит от того, где им работают
    (``domain/rules/crafts``).
    """
    rank = craft_rules.character_rank(content, character, craft.id)
    lines = [
        *head(f"{craft.name}: {rank_line(content, character, craft)}.", notice),
        craft.description,
    ]
    rows: list[tuple[Label, ...]] = []

    if craft.gathers:
        refused = craft_rules.can_gather(
            content, character, craft, now=now, cooldown=cooldown, biomes=biomes
        )
        brought = ", ".join(
            content.item(entry.item_id).name
            for entry in craft.yields
            if entry.level <= character.level and (not biomes or entry.found_in(biomes))
        )
        where = f" вокруг города {place}" if place else ""
        lines.append(f"Берут{where}: {brought}." if brought else f"Для вашего уровня{where} пусто.")
        elsewhere = ", ".join(
            content.item(entry.item_id).name
            for entry in craft.yields
            if entry.level <= character.level and biomes and not entry.found_in(biomes)
        )
        if elsewhere:
            lines.append(f"В других краях этим ремеслом берут и другое: {elsewhere}.")
        lines.append(refused or "Можно собирать: нажмите «Собрать сырьё».")
        rows.append((labels.GATHER,))
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


def gathered_line(content: GameContent, result: craft_rules.GatherResult) -> str:
    """Что принёс один сбор, одной фразой."""
    if not result.ok:
        return result.refused
    item = content.item(result.item_id)
    return f"Собрано: {item.name}, {result.count} штук. Работы записано: {result.experience}."


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
