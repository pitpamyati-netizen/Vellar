"""Contract screens: the board, the conversation, and the ledger.

A contract is a conversation with a person who names a price: the giver speaks
first, the terms come second, and refusing is one of the buttons - it always is
(``Narrative.md``, section 4).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.quest import ObjectiveKind, Quest
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.quests import QuestStep
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

OBJECTIVES: dict[ObjectiveKind, str] = {
    ObjectiveKind.KILL: "победить противников",
    ObjectiveKind.ELITE: "победить сильных противников",
    ObjectiveKind.SEARCH: "разобраться с местами без боя",
}

TARGET_KINDS: dict[str, str] = {
    "beast": "зверьё",
    "humanoid": "людей",
    "undead": "мертвяков",
    "elemental": "стихийных",
    "aberration": "тварей",
    "gather": "сборы",
    "cache": "тайники",
    "shrine": "святилища",
    "event": "события",
}


def objective_line(quest: Quest) -> str:
    """What the contract counts, in one sentence and without jargon."""
    what = OBJECTIVES[quest.objective]
    if quest.target_kind:
        what = f"{what}, а именно {TARGET_KINDS.get(quest.target_kind, quest.target_kind)}"
    return f"Счёт: {what}, всего {quest.target_count}."


def reward_line(content: GameContent, quest: Quest) -> str:
    parts = [f"{quest.reward_gold} золота", f"{quest.reward_experience} опыта"]
    if quest.reward_item and content.has_item(quest.reward_item):
        parts.append(content.item(quest.reward_item).name)
    return f"Плата: {', '.join(parts)}."


def quest_button(quest: Quest) -> Label:
    return label(f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}")


def board_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Everything this city will hand out to this character right now."""
    offered = quest_rules.available(content, character)
    entries = [
        ListEntry(
            key=quest.id,
            text=quest_button(quest).text,
            detail=f"{quest.giver}",
        )
        for quest in offered
    ]
    return paginated_screen(
        screen_id=ScreenId.QUEST_BOARD,
        title=f"Доска подрядов, {content.city(character.city_id).name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or "Доска подрядов. Берут не всех и не всё.",
            f"Ваш уровень: {character.level}.",
        ),
        empty_text="Свободных подрядов нет. Возвращайтесь с уровнем или закройте начатое.",
        show_filters=False,
    )


def offer_screen(content: GameContent, quest: Quest) -> Screen:
    """The conversation itself. Three answers, one of them is leaving."""
    return Screen(
        id=ScreenId.QUEST_OFFER,
        lines=(
            f"{quest.giver}. {quest.intro}",
            f"— {quest.terms}",
            objective_line(quest),
            reward_line(content, quest),
        ),
        rows=((labels.QUEST_ACCEPT,), (labels.QUEST_ASK,), (labels.QUEST_LEAVE,)),
    )


def step_line(content: GameContent, step: QuestStep) -> str:
    mark = "готово, можно сдавать" if step.done else "в работе"
    city = content.city(step.quest.city_id).name
    return f"{step.quest.name}: {step.progress} из {step.quest.target_count}, {mark}. {city}."


def journal_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """The ledger: what is taken, how far it has got, and where to hand it in."""
    steps = quest_rules.taken(content, character)
    lines = [notice or f"Подряды. Взято: {len(steps)}."]
    if not steps:
        lines.append("Ничего не взято. Подряды дают в таверне города.")
    lines.extend(step_line(content, step) for step in steps)
    lines.append("Сдают там же, где брали: в таверне того города.")
    lines.append(f"Закрыто подрядов за всё время: {len(character.quests.done)}.")
    return Screen(id=ScreenId.QUESTS, lines=tuple(lines))
