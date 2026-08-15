"""Craft screens: what the adventurer can make, and how far they have got.

Two screens, and no more:

- **Ремёсла** - every craft, its rank and the work left to the next one;
- **одно ремесло** - either the one gathering button, or the recipes, each of
  which says on its face what it takes and what it gives (accessibility rule 5).

A recipe button never lies about being pressable: an unaffordable one stays in
its place and answers with what is missing, by name and by count.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import Craft, Recipe
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

# What the player owns, as the flow hands it in: item id to count in the bag.
Owned = dict[str, int]


def rank_line(content: GameContent, character: Character, craft: Craft) -> str:
    """Rank first, then the work left - the two numbers a player asks for."""
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
    """The list of crafts. Everything on it is learnable by anybody."""
    lines = [
        notice or "Ремёсла. Сырьё собирают, из сырья делают вещи.",
        "Ремесло не выбирают раз и навсегда: работайте любым, ранг растёт от работы.",
        "Собирать можно раз в четверть часа: сырьё родится заново само.",
    ]
    rows = [(craft_button(content, character, craft),) for craft in content.crafts]
    return Screen(id=ScreenId.CRAFTS, lines=tuple(lines), rows=tuple(rows))


def recipe_button(content: GameContent, recipe: Recipe) -> Label:
    """A recipe says its price in materials before it is pressed."""
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
    notice: str = "",
) -> Screen:
    """One craft: the gathering button, or the recipes it knows."""
    rank = craft_rules.character_rank(content, character, craft.id)
    lines = [
        notice or f"{craft.name}: {rank_line(content, character, craft)}.",
        craft.description,
    ]
    rows: list[tuple[Label, ...]] = []

    if craft.gathers:
        refused = craft_rules.can_gather(content, character, craft, now=now, cooldown=cooldown)
        brought = ", ".join(
            content.item(entry.item_id).name
            for entry in craft.yields
            if entry.level <= character.level
        )
        lines.append(f"Берут отсюда: {brought}." if brought else "Для вашего уровня тут пусто.")
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
    """What one gathering brought back, in one sentence."""
    if not result.ok:
        return result.refused
    item = content.item(result.item_id)
    return f"Собрано: {item.name}, {result.count} штук. Работы записано: {result.experience}."


def made_line(content: GameContent, result: craft_rules.CraftResult) -> str:
    """What came out of the batch, quality first, because it is the surprise."""
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
