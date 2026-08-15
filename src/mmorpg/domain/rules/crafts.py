"""What a watch of craft work is worth.

Pure, like every rule module: no clock, no global random, no I/O. The seed of a
batch arrives as an argument, so the same character working the same recipe in
the same watch always gets the same result and a test can name it.

Two shapes of work, and both answer the same question - what comes out:

- **сбор**: one gathering per craft per watch, and the amount grows with rank;
- **изготовление**: materials in, an item out, and the quality of the batch
  decides whether anything extra comes with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.craft import (
    Craft,
    CraftRules,
    CraftYield,
    QualityTier,
    Recipe,
)
from mmorpg.domain.procgen.seeds import rng
from mmorpg.domain.rules.modifiers import collect_modifiers, percent

GATHER_YIELD_KEY = "gather_yield_percent"
CRAFT_QUALITY_KEY = "craft_quality_percent"


def rank_of(rules: CraftRules, experience: int) -> int:
    """Rank one is where everybody starts; work is what moves it."""
    earned = 1 + max(0, experience) // rules.experience_per_rank
    return min(rules.max_rank, earned)


def rank_name(rules: CraftRules, rank: int) -> str:
    index = min(max(rank, 1), len(rules.rank_names)) - 1
    return rules.rank_names[index] if rules.rank_names else str(rank)


def into_rank(rules: CraftRules, experience: int) -> tuple[int, int]:
    """Work done inside the current rank, and how much the rank takes.

    At the last rank there is nothing left to fill, and the pair is ``(0, 0)`` -
    the screen says "выше некуда" instead of showing a bar that never moves.
    """
    if rank_of(rules, experience) >= rules.max_rank:
        return 0, 0
    return max(0, experience) % rules.experience_per_rank, rules.experience_per_rank


def character_rank(content: GameContent, character: Character, craft_id: str) -> int:
    return rank_of(content.craft_rules, character.crafts.progress(craft_id).experience)


# --- gathering --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatherResult:
    """What one watch of gathering brought back."""

    item_id: str = ""
    count: int = 0
    experience: int = 0
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


def can_gather(content: GameContent, character: Character, craft: Craft, cycle: int) -> str:
    """Empty when gathering is allowed now, otherwise the reason it is not."""
    if not craft.gathers:
        return "Это ремесло ничего не собирает: из сырья делают вещи."
    if character.crafts.progress(craft.id).gathered_cycle == cycle:
        return "Здесь уже собрано в эту стражу. Сырьё родится к следующей."
    if not _reachable_yields(craft, character.level):
        return "Для вашего уровня тут пока нечего брать."
    return ""


def gather(
    content: GameContent,
    character: Character,
    craft: Craft,
    *,
    cycle: int,
    seed: bytes,
) -> tuple[Character, GatherResult]:
    """Work a gathering craft for one watch. The character comes back changed."""
    refused = can_gather(content, character, craft, cycle)
    if refused:
        return character, GatherResult(refused=refused)

    rules = content.craft_rules
    rank = character_rank(content, character, craft.id)
    reachable = _reachable_yields(craft, character.level)
    picked = rng(seed).choice(reachable)

    modifiers = collect_modifiers(content, character)
    amount = rules.gather_base + rules.gather_per_rank * (rank - 1)
    count = max(1, round(amount * percent(modifiers, GATHER_YIELD_KEY)))

    log = character.crafts.with_experience(craft.id, rules.gather_experience)
    log = log.with_gathered_cycle(craft.id, cycle)
    return (
        character.with_crafts(log),
        GatherResult(
            item_id=picked.item_id,
            count=count,
            experience=rules.gather_experience,
        ),
    )


def _reachable_yields(craft: Craft, level: int) -> list[CraftYield]:
    return [entry for entry in craft.yields if entry.level <= level]


# --- making -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CraftResult:
    """What came out of one batch, and what it took."""

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
    """Empty when the batch can be made, otherwise what is missing, by name."""
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
    """Spend the materials and produce the batch. Returns the changed character."""
    refused = can_make(content, character, recipe, owned)
    if refused:
        return character, CraftResult(recipe_id=recipe.id, refused=refused)

    rules = content.craft_rules
    rank = character_rank(content, character, recipe.craft_id)
    modifiers = collect_modifiers(content, character)
    quality = _roll_quality(rules, rank, percent(modifiers, CRAFT_QUALITY_KEY), seed)

    spent = tuple(
        (need.item_id, -_spend(need.count, quality.refund_percent)) for need in recipe.inputs
    )
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


def _spend(count: int, refund_percent: int) -> int:
    """Materials actually used up. A refund never gives work away for free."""
    kept = count * refund_percent // 100
    return max(1, count - kept)


def _roll_quality(rules: CraftRules, rank: int, bonus: float, seed: bytes) -> QualityTier:
    """Best tier first: an excellent batch is also a good one, never both."""
    roll = rng(seed).uniform(0, 100)
    fine = (rules.fine_chance_base + rules.fine_chance_per_rank * (rank - 1)) * bonus
    good = (rules.good_chance_base + rules.good_chance_per_rank * (rank - 1)) * bonus
    if roll < fine:
        return rules.quality("fine")
    if roll < fine + good:
        return rules.quality("good")
    return rules.quality("plain")
