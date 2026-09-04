"""Чего стоит работа в ремесле.

Чисто, как и всякий модуль правил: ни часов, ни глобальной случайности, ни
ввода-вывода. Сид партии и нынешний момент приходят аргументами, поэтому один и
тот же персонаж по одному и тому же рецепту всегда получает один и тот же
результат, а тест может его назвать.

Два вида работы, и оба отвечают на один вопрос — что выйдет:

- **сбор**: сколько сырья приносит одна отработанная жила, и растёт это с
  рангом. Где именно это происходит, решает не ремесло: сырьё берут в узлах
  локации и только инструментом (``domain/rules/tools.py``, ADR 0056), а
  ремесло отвечает лишь за то, сколько его вышло и что вообще лежит в этой
  земле;
- **изготовление**: сырьё внутрь, вещь наружу, а качество партии решает, придёт
  ли с ней что-то сверху.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import CraftRules, QualityTier, Recipe
from mmorpg.domain.procgen.seeds import rng
from mmorpg.domain.rules.modifiers import collect_modifiers, percent

GATHER_YIELD_KEY = "gather_yield_percent"
CRAFT_QUALITY_KEY = "craft_quality_percent"
#: Насколько больше материалов уцелевает при работе. Ключ обещал «Разборщик» и
#: не читал его никто (``Roadmap.md``, ADR 0018). Место у него было готово всё
#: это время: качество партии и так решает, сколько материалов не ушло в дело.
SALVAGE_YIELD_KEY = "salvage_yield_percent"


def rank_of(rules: CraftRules, experience: int) -> int:
    """Первый ранг - там, где начинают все; двигает его работа."""
    earned = 1 + max(0, experience) // rules.experience_per_rank
    return min(rules.max_rank, earned)


def rank_name(rules: CraftRules, rank: int) -> str:
    index = min(max(rank, 1), len(rules.rank_names)) - 1
    return rules.rank_names[index] if rules.rank_names else str(rank)


def into_rank(rules: CraftRules, experience: int) -> tuple[int, int]:
    """Работа, сделанная внутри нынешнего ранга, и сколько ранг берёт.

    На последнем ранге заполнять нечего, и пара — это ``(0, 0)``: экран говорит «выше
    некуда» вместо полосы, которая никогда не двигается.
    """
    if rank_of(rules, experience) >= rules.max_rank:
        return 0, 0
    return max(0, experience) % rules.experience_per_rank, rules.experience_per_rank


def character_rank(content: GameContent, character: Character, craft_id: str) -> int:
    return rank_of(content.craft_rules, character.crafts.progress(craft_id).experience)


# --- сбор ------------------------------------------------------------


def gather_amount(content: GameContent, character: Character, craft_id: str) -> int:
    """Сколько сырья выносит один отработанный узел этому персонажу.

    Ранг ремесла - единственное, что здесь растёт: место решает, что лежит, а
    ремесло - сколько его вынесли. Прибавки к сбору читаются тем же ключом, что
    и всегда (``GATHER_YIELD_KEY``).
    """
    rules = content.craft_rules
    rank = character_rank(content, character, craft_id)
    amount = rules.gather_base + rules.gather_per_rank * (rank - 1)
    modifiers = collect_modifiers(content, character)
    return max(1, round(amount * percent(modifiers, GATHER_YIELD_KEY)))


def yields_here(
    content: GameContent,
    *,
    level: int,
    biomes: frozenset[str] = frozenset(),
    sources: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Какое сырьё лежит в этой земле на этом уровне.

    Собирается по всем собирающим ремёслам разом: узел не знает, кто в него
    пришёл, а ``crafts.toml`` - единственное место, где сказано, что где лежит.
    ``sources`` сужает список тем, что берёт инструмент в руках; пустой - не
    сужает. Пустые ``biomes`` значат «не спрашивать, где это».
    """
    found: list[str] = []
    for craft in content.crafts:
        if not craft.gathers:
            continue
        for entry in craft.yields:
            if entry.level > level or entry.item_id in found:
                continue
            if biomes and not entry.found_in(biomes):
                continue
            if sources and content.item(entry.item_id).source not in sources:
                continue
            found.append(entry.item_id)
    return tuple(found)


def craft_of_source(content: GameContent, item_id: str) -> str:
    """Ремесло, в котором записывается эта находка. Пусто - ни в каком."""
    for craft in content.crafts:
        if craft.gathers and any(entry.item_id == item_id for entry in craft.yields):
            return craft.id
    return ""


# --- making -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CraftResult:
    """Что вышло из одной партии и чего это стоило."""

    recipe_id: str = ""
    item_id: str = ""
    count: int = 0
    quality: QualityTier | None = None
    experience: int = 0
    spent: tuple[tuple[str, int], ...] = ()
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


def can_make(
    content: GameContent,
    character: Character,
    recipe: Recipe,
    owned: Mapping[str, int],
) -> str:
    """Пусто, когда партию можно сделать, иначе - чего не хватает, по именам."""
    rank = character_rank(content, character, recipe.craft_id)
    if rank < recipe.rank:
        return f"Нужен ранг {recipe.rank}, у вас {rank}."
    missing = [
        f"{content.item(need.item_id).name}: нужно {need.count}, есть {owned.get(need.item_id, 0)}"
        for need in recipe.inputs
        if owned.get(need.item_id, 0) < need.count
    ]
    if missing:
        return "Не хватает материалов. " + "; ".join(missing) + "."
    return ""


def make(
    content: GameContent,
    character: Character,
    recipe: Recipe,
    owned: Mapping[str, int],
    *,
    seed: bytes,
) -> tuple[Character, CraftResult]:
    """Истратить сырьё и сделать партию. Возвращает изменённого персонажа."""
    refused = can_make(content, character, recipe, owned)
    if refused:
        return character, CraftResult(recipe_id=recipe.id, refused=refused)

    rules = content.craft_rules
    rank = character_rank(content, character, recipe.craft_id)
    modifiers = collect_modifiers(content, character)
    quality = _roll_quality(rules, rank, percent(modifiers, CRAFT_QUALITY_KEY), seed)

    kept = quality.refund_percent * max(0.0, percent(modifiers, SALVAGE_YIELD_KEY))
    spent = tuple((need.item_id, -_spend(need.count, kept)) for need in recipe.inputs)
    count = recipe.output_count + quality.extra
    log = character.crafts.with_experience(recipe.craft_id, recipe.experience)
    return (
        character.with_crafts(log),
        CraftResult(
            recipe_id=recipe.id,
            item_id=recipe.output_id,
            count=count,
            quality=quality,
            experience=recipe.experience,
            spent=spent,
        ),
    )


def _spend(count: int, refund_percent: float) -> int:
    """Сырьё, которое действительно ушло. Возврат никогда не отдаёт работу даром."""
    kept = int(count * max(0.0, refund_percent) // 100)
    return max(1, count - kept)


def _roll_quality(rules: CraftRules, rank: int, bonus: float, seed: bytes) -> QualityTier:
    """Сначала лучшая ступень: отличная партия она же и хорошая, но не обе разом."""
    roll = rng(seed).uniform(0, 100)
    fine = (rules.fine_chance_base + rules.fine_chance_per_rank * (rank - 1)) * bonus
    good = (rules.good_chance_base + rules.good_chance_per_rank * (rank - 1)) * bonus
    if roll < fine:
        return rules.quality("fine")
    if roll < fine + good:
        return rules.quality("good")
    return rules.quality("plain")
