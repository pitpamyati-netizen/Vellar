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
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.craft import Craft, Recipe
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import crafts as craft_rules
from mmorpg.domain.rules import tools as tool_rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

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


def tier_line(content: GameContent, rank: int) -> str:
    """Какой ступени вещи выходят из-под рук на этом ранге. Пусто - ступеней нет.

    Ранг в Велларе - это не число рецептов, а ступень изделия (ADR 0062): одна и
    та же кольчуга у ученика ветхая, а у гранд-мастера немеркнущая.
    """
    level = craft_rules.tier_of_rank(content.craft_rules, rank)
    tier = gear_procgen.tier_at(content, level) if level else None
    if tier is None:
        return ""
    return f"Ступень работы: {tier.named('f').lower()}, вещи {level} уровня."


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


def output_name(content: GameContent, recipe: Recipe) -> str:
    """Как называется то, что выйдет из этой работы.

    У снаряжения имя берётся без ведущего аффикса: какой он будет, решает бросок
    при работе (ADR 0059), и обещать «печатку силача» там, где из-под рук выйдет
    «печатка удачи», - это врать кнопкой.
    """
    parsed = gear_procgen.parse_gear_id(recipe.output_id)
    if parsed is None:
        return content.item(recipe.output_id).name
    archetype_id, level, rarity_id, _ = parsed
    if not content.has_gear_archetype(archetype_id) or not content.has_rarity(rarity_id):
        return content.item(recipe.output_id).name
    return gear_procgen.name_of(
        content, content.gear_archetype(archetype_id), level, content.rarity(rarity_id)
    )


def recipe_button(content: GameContent, recipe: Recipe) -> Label:
    """Рецепт называет свою цену в сырье до того, как его нажали."""
    needs = ", ".join(f"{content.item(need.item_id).name} {need.count}" for need in recipe.inputs)
    return label(f"{output_name(content, recipe)} — нужно: {needs}")


#: Разделы списка работ. Ремесло делает разное - сырьё, доспех, оружие, мелочь, -
#: и мастер, которому нужно одно точило, не должен слушать сорок кольчуг
#: (правило доступности 13).
CRAFT_SECTIONS: tuple[str, ...] = ("Сырьё", "Доспех", "Оружие", "Украшения", "Расходники")


def section_of(content: GameContent, recipe: Recipe) -> str:
    """В каком разделе списка стоит эта работа."""
    item = content.item(recipe.output_id)
    if item.kind is ItemKind.MATERIAL:
        return "Сырьё"
    if item.kind is ItemKind.CONSUMABLE:
        return "Расходники"
    if item.is_weapon:
        return "Оружие"
    if item.is_armor:
        return "Доспех"
    return "Украшения"


def open_recipes(content: GameContent, character: Character, craft: Craft) -> tuple[Recipe, ...]:
    """Работы, что этому мастеру по руке, начиная с последних (ADR 0062).

    Ранг открывает ступень, но не закрывает пройденных: точило ученика куют и
    гранд-мастером, и заказчик со сводки просит именно его. Порядок обратный
    рангу нарочно - то, ради чего экран открыли, лежит на первой странице.
    """
    rank = craft_rules.character_rank(content, character, craft.id)
    return tuple(
        sorted(
            (recipe for recipe in content.recipes_of(craft.id) if recipe.rank <= rank),
            key=lambda recipe: (-recipe.rank, output_name(content, recipe)),
        )
    )


def craft_screen(
    content: GameContent,
    character: Character,
    craft: Craft,
    owned: Owned,
    *,
    page: PageState | None = None,
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
    if craft.gathers:
        lines = [
            *head(f"{craft.name}: {rank_line(content, character, craft)}.", notice),
            craft.description,
            *gathering_lines(content, character, craft, biomes=biomes, place=place),
        ]
        return Screen(id=ScreenId.CRAFT, lines=tuple(lines), rows=())

    recipes = content.recipes_of(craft.id)
    open_now = open_recipes(content, character, craft)
    state = page or PageState()
    query = state.filters.query.casefold()
    section = state.filters.category
    listed = [
        recipe
        for recipe in open_now
        if (not query or query in output_name(content, recipe).casefold())
        and (not section or section_of(content, recipe) == section)
    ]
    later = len(recipes) - len(open_now)
    lead = [
        notice,
        craft.description,
        tier_line(content, rank),
        f"Работ открыто: {len(open_now)} из {len(recipes)}."
        + (f" Ещё {later} откроется с рангом." if later else ""),
        "Материалы уходят из сумки, готовое кладут туда же.",
    ]
    return paginated_screen(
        screen_id=ScreenId.CRAFT,
        title=f"{craft.name}: {rank_line(content, character, craft)}",
        entries=tuple(
            ListEntry(key=recipe.id, text=recipe_button(content, recipe).text) for recipe in listed
        ),
        state=state,
        lead_lines=tuple(line for line in lead if line),
        empty_text="Работ, что вам по руке, здесь пока нет.",
        categories=CRAFT_SECTIONS,
    )


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
