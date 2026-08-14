"""What a trip into a location does to a character.

The combat engine decides how a fight goes; this module decides what the fight
was *worth* - experience, gold, loot, contract counters, and the wounds carried
back out. The same goes for the quiet nodes: a cache pays, a shrine patches you
up, and both are rolled from the node seed rather than stored.

Everything here is pure. Nothing writes; each function returns the new character
and a structured description of what changed, and the caller stores it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import CombatState
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.location import LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import rng
from mmorpg.domain.rules import economy
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.progression import LevelUp, grant_experience
from mmorpg.domain.rules.quests import QuestStep
from mmorpg.domain.rules.stats import derived_stats

# Losing is expensive but never ruinous: a tenth of what is on you, and you wake
# up in the city with a quarter of your health. The Chamber does not confiscate,
# it just does not help either.
DEFEAT_GOLD_PERCENT = 10
DEFEAT_HEALTH_PERCENT = 25

# A quiet node pays a fraction of what a fight of the same level pays: searching
# is the safe way to spend a watch, so it is also the slower one.
CACHE_GOLD_BASE = 6.0
CACHE_GOLD_PER_LEVEL = 3.2
EVENT_GOLD_PER_LEVEL = 1.6
SEARCH_EXPERIENCE_BASE = 5
SEARCH_EXPERIENCE_PER_LEVEL = 3
SHRINE_HEAL_PERCENT = 35
CACHE_ITEM_CHANCE = 45.0
GATHER_ITEM_CHANCE = 80.0


@dataclass(frozen=True, slots=True)
class Aftermath:
    """The result of one fight, once it has been paid out."""

    character: Character
    experience: int = 0
    gold: int = 0
    loot: tuple[str, ...] = ()
    level_up: LevelUp | None = None
    quest_steps: tuple[QuestStep, ...] = ()
    gold_lost: int = 0

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What a node without a fight gave up."""

    character: Character
    kind: NodeKind
    gold: int = 0
    experience: int = 0
    item_id: str = ""
    healed: int = 0
    level_up: LevelUp | None = None
    quest_steps: tuple[QuestStep, ...] = ()

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


def carry_wounds(content: GameContent, character: Character, state: CombatState) -> Character:
    """Write the health the fight ended with back onto the character."""
    stats = derived_stats(content, character)
    return character.with_health(state.player.health, stats.max_health)


def resolve_victory(content: GameContent, character: Character, state: CombatState) -> Aftermath:
    """Pay for a won fight: experience, gold, loot and contract counters."""
    enemies = tuple(enemy.enemy for enemy in state.enemies)
    log, steps = quest_rules.record_kills(content, character, enemies)

    paid = replace(carry_wounds(content, character, state).with_gold(state.gold), quests=log)
    grown, level_up = grant_experience(content, paid, state.experience)
    return Aftermath(
        character=grown,
        experience=state.experience,
        gold=state.gold,
        loot=state.loot,
        level_up=level_up,
        quest_steps=steps,
    )


def resolve_defeat(content: GameContent, character: Character) -> Aftermath:
    """Losing costs gold and a watch of walking back, never a level."""
    stats = derived_stats(content, character)
    lost = character.gold * DEFEAT_GOLD_PERCENT // 100
    revived = character.with_gold(-lost).with_health(
        max(1, stats.max_health * DEFEAT_HEALTH_PERCENT // 100), stats.max_health
    )
    return Aftermath(character=revived, gold_lost=lost)


def resolve_search(
    content: GameContent,
    character: Character,
    node: LocationNode,
    seed: bytes,
) -> SearchResult:
    """Work through a node that holds no fight. Deterministic from the seed."""
    source = rng(seed)
    stats = derived_stats(content, character)
    log, steps = quest_rules.record_search(content, character, node.kind)
    working = replace(character, quests=log)

    gold = 0
    item_id = ""
    healed = 0
    experience = SEARCH_EXPERIENCE_BASE + SEARCH_EXPERIENCE_PER_LEVEL * node.level

    match node.kind:
        case NodeKind.CACHE:
            gold = max(1, round(CACHE_GOLD_BASE + CACHE_GOLD_PER_LEVEL * node.level))
            if source.uniform(0, 100) < CACHE_ITEM_CHANCE:
                item_id = _pick_item(content, source, node.level, materials_only=False)
        case NodeKind.GATHER:
            if source.uniform(0, 100) < GATHER_ITEM_CHANCE:
                item_id = _pick_item(content, source, node.level, materials_only=True)
        case NodeKind.SHRINE:
            current = working.health_or(stats.max_health)
            restored = round(stats.max_health * SHRINE_HEAL_PERCENT / 100)
            healed = min(restored, stats.max_health - current)
            working = working.with_health(current + healed, stats.max_health)
        case _:
            gold = max(1, round(EVENT_GOLD_PER_LEVEL * node.level))

    grown, level_up = grant_experience(content, working.with_gold(gold), experience)
    return SearchResult(
        character=grown,
        kind=node.kind,
        gold=gold,
        experience=experience,
        item_id=item_id,
        healed=healed,
        level_up=level_up,
        quest_steps=steps,
    )


@dataclass(frozen=True, slots=True)
class RestResult:
    """A night in a city: what it healed and what it cost."""

    character: Character
    healed: int
    cost: int
    refused: str = ""


def rest(content: GameContent, character: Character, *, paid: bool) -> RestResult:
    """Sleep it off. A paid bed heals everything; straw heals a part of it.

    The free bed exists so a broke character is never stuck: without it, a player
    who lost their last coin at one hit point would have no move left at all.
    """
    stats = derived_stats(content, character)
    current = character.health_or(stats.max_health)
    if current >= stats.max_health:
        return RestResult(character=character, healed=0, cost=0, refused="whole")

    if not paid:
        restored = min(
            stats.max_health - current,
            max(1, round(stats.max_health * economy.STRAW_HEAL_PERCENT / 100)),
        )
        healed = character.with_health(current + restored, stats.max_health)
        return RestResult(character=healed, healed=restored, cost=0)

    cost = economy.inn_price(character.level)
    if character.gold < cost:
        return RestResult(character=character, healed=0, cost=cost, refused="poor")
    rested = character.with_gold(-cost).with_health(stats.max_health, stats.max_health)
    return RestResult(character=rested, healed=stats.max_health - current, cost=cost)


def use_consumable(
    content: GameContent, character: Character, item_id: str
) -> tuple[Character, int]:
    """Drink a potion outside a fight. Returns the character and what it healed.

    Only healing works out here: a damage buff with nobody to hit would be a way
    to throw a potion away by accident.
    """
    item = content.item(item_id)
    if item.effect is None:
        return character, 0
    stats = derived_stats(content, character)
    current = character.health_or(stats.max_health)
    match item.effect.kind:
        case "heal_flat":
            restored = round(item.effect.power)
        case "heal_percent":
            restored = round(stats.max_health * item.effect.power / 100.0)
        case _:
            return character, 0
    restored = min(restored, stats.max_health - current)
    if restored <= 0:
        return character, 0
    return character.with_health(current + restored, stats.max_health), restored


def _pick_item(
    content: GameContent, source: random.Random, level: int, *, materials_only: bool
) -> str:
    """A find that suits the level. Empty when content has nothing that low."""
    pool = [
        item
        for item in content.items
        if item.level <= max(1, level)
        and (
            item.kind is ItemKind.MATERIAL if materials_only else item.kind is not ItemKind.MATERIAL
        )
    ]
    if not pool:
        return ""
    return source.choice(pool).id
