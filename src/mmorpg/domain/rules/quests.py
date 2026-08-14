"""Contracts: which ones a city offers, what counts towards them, what they pay.

Pure arithmetic over the character's ledger. Nothing here writes anything: every
function returns a new ledger or a new character, and the handler stores it.

The counter only ever moves forward, and only for a contract the character
actually took - a kill made before taking the job is a kill, not a favour.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import Enemy, NodeKind
from mmorpg.domain.entities.quest import ObjectiveKind, Quest, QuestLog
from mmorpg.domain.rules.progression import LevelUp, grant_experience

# How far above the contract's level a character may still take it. Below the
# level it is simply not offered; there is no ceiling, because an easy contract
# already pays its fixed price and nothing more.
LEVEL_SLACK = 0


@dataclass(frozen=True, slots=True)
class QuestStep:
    """One counter that moved, ready to be turned into a sentence."""

    quest: Quest
    progress: int

    @property
    def done(self) -> bool:
        return self.progress >= self.quest.target_count


def is_open(quest: Quest, character: Character) -> bool:
    """Whether the board shows this contract to this character right now."""
    log = character.quests
    if log.is_done(quest.id) or log.is_taken(quest.id):
        return False
    if character.level + LEVEL_SLACK < quest.level:
        return False
    return not quest.follows or log.is_done(quest.follows)


def available(content: GameContent, character: Character, city_id: str = "") -> tuple[Quest, ...]:
    """Contracts this city will hand out to this character, easiest first."""
    city = city_id or character.city_id
    return tuple(quest for quest in content.quests_in(city) if is_open(quest, character))


def taken(content: GameContent, character: Character) -> tuple[QuestStep, ...]:
    """Everything in the ledger right now, with its counter."""
    return tuple(
        QuestStep(quest=content.quest(quest_id), progress=progress)
        for quest_id, progress in sorted(character.quests.taken.items())
        if content.has_quest(quest_id)
    )


def ready_to_hand_in(
    content: GameContent, character: Character, city_id: str = ""
) -> tuple[QuestStep, ...]:
    """Counted-out contracts belonging to the city the character is standing in."""
    city = city_id or character.city_id
    return tuple(
        step for step in taken(content, character) if step.done and step.quest.city_id == city
    )


def _counts_kill(quest: Quest, enemy: Enemy) -> bool:
    if quest.objective is ObjectiveKind.ELITE:
        return enemy.is_elite
    if quest.objective is not ObjectiveKind.KILL:
        return False
    return not quest.target_kind or enemy.kind.value == quest.target_kind


def _counts_search(quest: Quest, node: NodeKind) -> bool:
    if quest.objective is not ObjectiveKind.SEARCH:
        return False
    return not quest.target_kind or node.value == quest.target_kind


def record_kills(
    content: GameContent, character: Character, enemies: tuple[Enemy, ...]
) -> tuple[QuestLog, tuple[QuestStep, ...]]:
    """Count defeated opponents into every contract that asked for them."""
    log = character.quests
    moved: dict[str, int] = {}
    for quest_id in tuple(log.taken):
        if not content.has_quest(quest_id):
            continue
        quest = content.quest(quest_id)
        counted = sum(1 for enemy in enemies if _counts_kill(quest, enemy))
        if not counted:
            continue
        # Never past the target: an overshoot would read as "12 из 10".
        room = max(0, quest.target_count - log.progress(quest_id))
        gained = min(counted, room)
        if gained:
            log = log.advanced(quest_id, gained)
            moved[quest_id] = log.progress(quest_id)
    return log, tuple(
        QuestStep(quest=content.quest(quest_id), progress=progress)
        for quest_id, progress in moved.items()
    )


def record_search(
    content: GameContent, character: Character, node: NodeKind
) -> tuple[QuestLog, tuple[QuestStep, ...]]:
    """Count one node worked through without a fight."""
    log = character.quests
    moved: dict[str, int] = {}
    for quest_id in tuple(log.taken):
        if not content.has_quest(quest_id):
            continue
        quest = content.quest(quest_id)
        if not _counts_search(quest, node):
            continue
        if log.progress(quest_id) >= quest.target_count:
            continue
        log = log.advanced(quest_id)
        moved[quest_id] = log.progress(quest_id)
    return log, tuple(
        QuestStep(quest=content.quest(quest_id), progress=progress)
        for quest_id, progress in moved.items()
    )


def take(content: GameContent, character: Character, quest: Quest) -> Character:
    if not is_open(quest, character):
        return character
    return replace(character, quests=character.quests.take(quest.id))


def abandon(character: Character, quest: Quest) -> Character:
    return replace(character, quests=character.quests.abandon(quest.id))


@dataclass(frozen=True, slots=True)
class QuestPayout:
    """What handing a contract in changed. The item is added by the handler."""

    character: Character
    quest: Quest
    gold: int
    experience: int
    level_up: LevelUp
    item_id: str = ""


def hand_in(content: GameContent, character: Character, quest: Quest) -> QuestPayout | None:
    """Close a counted-out contract and pay for it. ``None`` if it is not due."""
    log = character.quests
    if not log.is_taken(quest.id) or log.progress(quest.id) < quest.target_count:
        return None
    paid, level_up = grant_experience(
        content, character.with_gold(quest.reward_gold), quest.reward_experience
    )
    return QuestPayout(
        character=replace(paid, quests=log.complete(quest.id)),
        quest=quest,
        gold=quest.reward_gold,
        experience=quest.reward_experience,
        level_up=level_up,
        item_id=quest.reward_item,
    )
