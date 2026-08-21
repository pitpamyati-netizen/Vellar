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
from mmorpg.domain.entities.combat import BattleState
from mmorpg.domain.entities.content import GameContent, ItemKind
from mmorpg.domain.entities.location import LocationNode, NodeKind
from mmorpg.domain.procgen.seeds import rng
from mmorpg.domain.rules import economy
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.progression import (
    LevelUp,
    earned,
    experience_reward,
    grant_experience,
)
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

#: Прибавка к тому, что приносит тихий узел: тайник, находка, развилка. Ключ
#: обещали «Рассказчик», «Разведчик» и «Звезда странника», и до сих пор его не
#: читал никто (``Roadmap.md``, ADR 0018). Святилище он не трогает: оно лечит, а
#: не платит, и «награда за событие» лечением не бывает.
EVENT_REWARD_KEY = "event_reward_percent"

#: Что лежит в узле для сбора, по его имени. Из зарослей не выкапывают руду, а с
#: останков не срезают травы: узел, который отдаёт железный лом вместо трав,
#: читается как ошибка - ровно так же, как волчья шкура с кабана.
#: Имена приходят из ``procgen/location.NODE_NAMES``; узел, которого тут нет,
#: отдаёт любое сырьё по уровню.
GATHER_SOURCES: dict[str, str] = {
    "Заросли": "травы",
    "Полезные травы": "травы",
    "Жила руды": "руда",
    "Останки": "шкуры",
}


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


def carry_wounds(
    content: GameContent, character: Character, state: BattleState, combatant_id: int
) -> Character:
    """Записать на персонажа то здоровье, с которым он вышел из боя.

    Ниже единицы оно не опускается. Павший в отряде, который бой всё-таки
    выиграл, встаёт на ноги: персонаж с нулём здоровья не может ни драться, ни
    дойти до лекаря, и это была бы не цена поражения, а тупик.
    """
    stats = derived_stats(content, character)
    one = state.by_id(combatant_id)
    health = one.health if one is not None else character.health_or(stats.max_health)
    return character.with_health(max(1, health), stats.max_health)


def resolve_victory(
    content: GameContent,
    character: Character,
    state: BattleState,
    combatant_id: int,
    *,
    experience: int | None = None,
    gold: int | None = None,
    loot: tuple[str, ...] | None = None,
) -> Aftermath:
    """Заплатить за выигранный бой: опыт, золото, добыча и счёт по заданиям.

    Доли приходят снаружи: сколько именно досталось этому герою, решает отряд, а
    не бой (``domain/rules/party.split``). Не переданы - значит герой дрался
    один и забирает всё.
    """
    fallen = tuple(one.enemy for one in state.combatants if one.enemy is not None and not one.alive)
    log, steps = quest_rules.record_kills(content, character, fallen)

    share_gold = state.gold if gold is None else gold
    share_experience = state.experience if experience is None else experience
    share_loot = state.loot if loot is None else loot

    paid = replace(
        carry_wounds(content, character, state, combatant_id).with_gold(share_gold), quests=log
    )
    grown, level_up = grant_experience(content, paid, share_experience)
    return Aftermath(
        character=grown,
        experience=earned(content, paid, share_experience),
        gold=share_gold,
        loot=share_loot,
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
                item_id = _pick_item(
                    content,
                    source,
                    node.level,
                    materials_only=True,
                    of_source=GATHER_SOURCES.get(node.name, ""),
                )
        case NodeKind.SHRINE:
            current = working.health_or(stats.max_health)
            restored = round(stats.max_health * SHRINE_HEAL_PERCENT / 100)
            healed = min(restored, stats.max_health - current)
            working = working.with_health(current + healed, stats.max_health)
        case _:
            gold = max(1, round(EVENT_GOLD_PER_LEVEL * node.level))

    share = max(0.0, mods.percent(mods.collect_modifiers(content, character), EVENT_REWARD_KEY))
    if node.kind is not NodeKind.SHRINE:
        gold = round(gold * share)
        experience = max(1, round(experience * share))
    paid = working.with_gold(gold)
    grown, level_up = grant_experience(content, paid, experience)
    return SearchResult(
        character=grown,
        kind=node.kind,
        gold=gold,
        experience=earned(content, paid, experience),
        item_id=item_id,
        healed=healed,
        level_up=level_up,
        quest_steps=steps,
    )


# --- the bottom of a descent ------------------------------------------
#
# A descent used to be three fights that happened to be in a row: the epic
# opponent on the last floor was the only thing separating it from three fights
# in a location, and the screen promised a reward "внизу и целиком" that nothing
# in the code paid (Roadmap, "Риски"). This is that reward.
#
# It is worth about another epic fight - enough that walking down wounded is a
# real decision and that leaving on the second floor costs something.
DESCENT_GOLD_BASE = 25.0
DESCENT_GOLD_PER_LEVEL = 9.0
#: The bottom pays experience like one more opponent of the depth's own level.
DESCENT_EXPERIENCE_BASE = 30


@dataclass(frozen=True, slots=True)
class DescentPrize:
    """What the bottom of a descent handed over."""

    character: Character
    gold: int = 0
    experience: int = 0
    item_id: str = ""
    level_up: LevelUp | None = None

    @property
    def levelled(self) -> bool:
        return self.level_up is not None and self.level_up.levels_gained > 0


def descent_gold(level: int) -> int:
    """What the bottom pays, before anything is rolled."""
    return max(1, round(DESCENT_GOLD_BASE + DESCENT_GOLD_PER_LEVEL * max(1, level)))


def descent_prize(
    content: GameContent, character: Character, *, level: int, seed: bytes
) -> DescentPrize:
    """Pay for a descent walked to the bottom. Deterministic from the seed.

    The find is never a material: the bottom of a hole in the ground is where a
    thing is, not where herbs grow, and a descent that paid in wolf pelts would
    read as a bug.
    """
    source = rng(seed)
    gold = descent_gold(level)
    item_id = _pick_item(content, source, level, materials_only=False)
    experience = max(
        1,
        DESCENT_EXPERIENCE_BASE
        + experience_reward(enemy_level=max(1, level), character_level=character.level),
    )
    paid = character.with_gold(gold)
    grown, level_up = grant_experience(content, paid, experience)
    return DescentPrize(
        character=grown,
        gold=gold,
        experience=earned(content, paid, experience),
        item_id=item_id,
        level_up=level_up,
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
    content: GameContent,
    source: random.Random,
    level: int,
    *,
    materials_only: bool,
    of_source: str = "",
) -> str:
    """A find that suits the level. Empty when content has nothing that low.

    ``of_source`` narrows a gather to what the node actually holds, and it is
    never given up on: if content has nothing of that kind low enough, the node
    hands over the cheapest thing of the right kind instead of the wrong kind.
    An ore vein that pays in herbs is the same bug as a boar wearing a wolf's
    skin, and the whole point of ``source`` is not to have it.
    """
    fits_kind = [
        item
        for item in content.items
        if (item.kind is ItemKind.MATERIAL)
        is materials_only  # материалы для сбора, всё остальное - для тайников
    ]
    pool = [item for item in fits_kind if item.level <= max(1, level)]
    if of_source:
        right = [item for item in fits_kind if item.source == of_source]
        by_level = [item for item in right if item.level <= max(1, level)]
        if by_level:
            pool = by_level
        elif right:
            lowest = min(item.level for item in right)
            pool = [item for item in right if item.level == lowest]
        else:
            pool = []
    if not pool:
        return ""
    return source.choice(pool).id
