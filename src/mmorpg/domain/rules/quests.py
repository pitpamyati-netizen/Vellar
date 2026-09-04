"""Задания: какие предлагает город, что в них засчитывается, сколько они платят.

Чистая арифметика по журналу персонажа. Ничто здесь ничего не пишет: каждая
функция возвращает новый журнал или нового персонажа, а сохраняет хендлер.

Счётчик двигается только вперёд и только по заданию, которое персонаж
действительно взял: убитый до того, как работа взята, - это убитый, а не
одолжение.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import Enemy, NodeKind
from mmorpg.domain.entities.quest import ObjectiveKind, Quest, QuestLog
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules.progression import LevelUp, earned, grant_experience

# Насколько выше уровня задания персонаж всё ещё может его взять. Ниже уровня оно просто
# не предлагается; потолка нет, потому что лёгкое задание и так платит свою постоянную
# цену и ничего сверх.
LEVEL_SLACK = 0


@dataclass(frozen=True, slots=True)
class QuestStep:
    """Один сдвинувшийся счётчик, готовый превратиться во фразу."""

    quest: Quest
    progress: int

    @property
    def done(self) -> bool:
        return self.progress >= self.quest.target_count


def is_open(quest: Quest, character: Character) -> bool:
    """Показывает ли доска это задание этому персонажу прямо сейчас."""
    log = character.quests
    if log.is_done(quest.id) or log.is_taken(quest.id):
        return False
    if character.level + LEVEL_SLACK < quest.level:
        return False
    return not quest.follows or log.is_done(quest.follows)


def available(content: GameContent, character: Character, city_id: str = "") -> tuple[Quest, ...]:
    """Задания, которые этот город выдаст этому персонажу, от простых к сложным."""
    city = city_id or character.city_id
    return tuple(quest for quest in content.quests_in(city) if is_open(quest, character))


def taken(content: GameContent, character: Character) -> tuple[QuestStep, ...]:
    """Всё, что сейчас в журнале, вместе со счётчиками."""
    return tuple(
        QuestStep(quest=content.quest(quest_id), progress=progress)
        for quest_id, progress in sorted(character.quests.taken.items())
        if content.has_quest(quest_id)
    )


def ready_to_hand_in(
    content: GameContent, character: Character, city_id: str = ""
) -> tuple[QuestStep, ...]:
    """Досчитанные задания, принадлежащие городу, в котором стоит персонаж."""
    city = city_id or character.city_id
    return tuple(
        step for step in taken(content, character) if step.done and step.quest.city_id == city
    )


def _counts_kill(quest: Quest, enemy: Enemy) -> bool:
    """Засчитан ли этот побеждённый в это задание.

    ``target_kind`` называет либо породу («зверьё»), либо конкретного противника
    («кабан»). Пустое поле считает любого: задание на «пятерых кого угодно» — это
    нормальное задание, а не недописанное.
    """
    if quest.objective is ObjectiveKind.ELITE:
        return enemy.is_elite and _named(quest, enemy)
    if quest.objective is not ObjectiveKind.KILL:
        return False
    return _named(quest, enemy)


def _named(quest: Quest, enemy: Enemy) -> bool:
    """Тот ли это, кого заказывали: по породе или поимённо."""
    wanted = quest.target_kind
    return not wanted or enemy.kind.value == wanted or enemy.archetype_id == wanted


def _counts_search(quest: Quest, node: NodeKind) -> bool:
    if quest.objective is not ObjectiveKind.SEARCH:
        return False
    return not quest.target_kind or node.value == quest.target_kind


def record_kills(
    content: GameContent, character: Character, enemies: tuple[Enemy, ...]
) -> tuple[QuestLog, tuple[QuestStep, ...]]:
    """Засчитать побеждённых противников во все задания, которые их просили."""
    log = character.quests
    moved: dict[str, int] = {}
    for quest_id in tuple(log.taken):
        if not content.has_quest(quest_id):
            continue
        quest = content.quest(quest_id)
        counted = sum(1 for enemy in enemies if _counts_kill(quest, enemy))
        if not counted:
            continue
        # Никогда за цель: перебор читался бы как «12 из 10».
        room = max(0, quest.target_count - log.progress(quest_id))
        gained = min(counted, room)
        if gained:
            log = log.advanced(quest_id, gained)
            moved[quest_id] = log.progress(quest_id)
    return log, tuple(
        QuestStep(quest=content.quest(quest_id), progress=progress)
        for quest_id, progress in moved.items()
    )


def _counts_craft(quest: Quest, item_id: str) -> bool:
    """Та ли это работа, которую заказывали.

    Снаряжение сверяется видом и ступенью, а не именем целиком: ладная работа
    выходит редкостью выше рецепта и со своим оттиском (ADR 0059, 0060), и
    заказчик, попросивший кольчугу, не должен отказываться от кольчуги получше.
    """
    if quest.objective is not ObjectiveKind.CRAFT:
        return False
    if not quest.target_kind or quest.target_kind == item_id:
        return True
    wanted = gear_procgen.parse_gear_id(quest.target_kind)
    made = gear_procgen.parse_gear_id(item_id)
    return bool(wanted and made and wanted[:2] == made[:2])


def record_craft(
    content: GameContent, character: Character, item_id: str, count: int = 1
) -> tuple[QuestLog, tuple[QuestStep, ...]]:
    """Засчитать вышедшую из ремесла партию в задания, которые её просили.

    Сделано, а не куплено: счётчик двигается там, где случается работа, поэтому
    задание на три точильных камня - это три камня, которые кто-то и правда выковал.
    Из сумки при этом не вынимается ничего: сделанное остаётся сделанным, а плата
    приходит при сдаче задания, как и у всех прочих.
    """
    log = character.quests
    moved: dict[str, int] = {}
    for quest_id in tuple(log.taken):
        if not content.has_quest(quest_id):
            continue
        quest = content.quest(quest_id)
        if not _counts_craft(quest, item_id):
            continue
        room = max(0, quest.target_count - log.progress(quest_id))
        gained = min(max(0, count), room)
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
    """Засчитать один узел, отработанный без боя."""
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
    """Что изменила сдача задания. Вещь добавляет хендлер."""

    character: Character
    quest: Quest
    gold: int
    experience: int
    level_up: LevelUp
    item_id: str = ""


#: Прибавка к плате за задание. Её обещали «Серебряный язык» и
#: «Дипломатическая неприкосновенность», а считал её никто (``Roadmap.md``).
#: Считается здесь: задание закрывается в одном месте, значит и плата за него.
QUEST_REWARD_KEY = "quest_reward_percent"


def hand_in(content: GameContent, character: Character, quest: Quest) -> QuestPayout | None:
    """Закрыть досчитанное задание и заплатить за него. ``None``, если срок не подошёл."""
    log = character.quests
    if not log.is_taken(quest.id) or log.progress(quest.id) < quest.target_count:
        return None
    share = max(0.0, mods.percent(mods.collect_modifiers(content, character), QUEST_REWARD_KEY))
    gold = round(quest.reward_gold * share)
    experience = round(quest.reward_experience * share)
    given = character.with_gold(gold)
    paid, level_up = grant_experience(content, given, experience)
    return QuestPayout(
        character=replace(paid, quests=log.complete(quest.id)),
        quest=quest,
        gold=gold,
        experience=earned(content, given, experience),
        level_up=level_up,
        item_id=quest.reward_item,
    )
