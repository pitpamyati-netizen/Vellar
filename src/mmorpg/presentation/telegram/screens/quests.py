"""Contract screens: the board, the conversation, and the ledger.

A contract is a conversation with a person who names a price: the giver speaks
first, the terms come second, and refusing is one of the buttons - it always is
(``Narrative.md``, section 4).

Everything else on these screens exists because of one play test. The first
contract said "Счёт: разобраться с местами без боя, всего 3" and players did not
know what that meant, where to go, or what to press. So a contract now spells out
three things in the player's own words: **что делать**, **куда идти** and **как
это засчитывается** - and the board keeps showing a contract after it is taken,
with its counter, instead of making it vanish the moment you agree to it.
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
from mmorpg.presentation.telegram.screens.format import head, plural
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

#: Что делать - глаголом, в единственном числе, чтобы вокруг него встал счёт.
OBJECTIVES: dict[ObjectiveKind, str] = {
    ObjectiveKind.KILL: "победить противников в бою",
    ObjectiveKind.ELITE: "победить сильных противников",
    ObjectiveKind.SEARCH: "обыскать места, где нет боя",
    # Раньше этой строки не было вовсе, и любое задание на изготовление роняло
    # разговор с нанимателем на KeyError.
    ObjectiveKind.CRAFT: "изготовить своими руками",
}

#: Как это засчитывается - одна фраза про то, что именно нажимать.
HOW: dict[ObjectiveKind, str] = {
    ObjectiveKind.KILL: (
        "Считается каждая выигранная схватка в узле локации: «Вступить в бой» и до победы."
    ),
    ObjectiveKind.ELITE: (
        "Считается победа над эпическим противником. Узел с ним карта локации "
        "называет по-своему, но перед боем прямо говорит, кто там стоит."
    ),
    ObjectiveKind.SEARCH: (
        "Считается узел, разобранный без боя: заросли, тайник, святилище, событие. "
        "Зайдите в узел и нажмите его действие."
    ),
    ObjectiveKind.CRAFT: (
        "Считается то, что вышло из работы: «Ремёсла» в главном меню, рецепт, «Изготовить». "
        "Купленное не считается."
    ),
}

TARGET_KINDS: dict[str, str] = {
    "beast": "зверьё",
    "humanoid": "людей",
    "undead": "мертвяков",
    "elemental": "стихийных",
    "aberration": "тварей",
    "gather": "заросли и жилы",
    "cache": "тайники",
    "shrine": "святилища",
    "event": "события",
}


def where_line(content: GameContent, quest: Quest) -> str:
    """Куда идти. Пусто, если задание не привязано к месту."""
    if quest.objective is ObjectiveKind.CRAFT:
        return "Где: за верстаком, в разделе «Ремёсла». Идти никуда не нужно."
    if not content.has_city(quest.city_id):
        return ""
    city = content.city(quest.city_id)
    if not quest.location_slot or not city.has_location(quest.location_slot):
        return f"Где: в локациях города {city.name}, любых по вашему уровню."
    location = city.location(quest.location_slot)
    return (
        f"Где: город {city.name}, «Локации», «{location.slot}. {location.name}». "
        f"Уровни там с {location.level_min} по {location.level_max}."
    )


def objective_line(content: GameContent, quest: Quest) -> str:
    """Что делать и сколько раз — одной фразой, без жаргона."""
    what = OBJECTIVES[quest.objective]
    if quest.target_kind:
        named = TARGET_KINDS.get(quest.target_kind, quest.target_kind)
        if quest.objective is ObjectiveKind.CRAFT:
            named = (
                content.item(quest.target_kind).name
                if content.has_item(quest.target_kind)
                else named
            )
        what = f"{what}, а именно {named}"
    times = plural(quest.target_count, "раз", "раза", "раз")
    return f"Что делать: {what}. Нужно {quest.target_count} {times}."


def instructions(content: GameContent, quest: Quest) -> tuple[str, ...]:
    """Три строки, которых на экране задания не хватало: что, где и как."""
    return tuple(
        line
        for line in (
            objective_line(content, quest),
            where_line(content, quest),
            HOW[quest.objective],
        )
        if line
    )


def reward_line(content: GameContent, quest: Quest) -> str:
    parts = [f"{quest.reward_gold} золота", f"{quest.reward_experience} опыта"]
    if quest.reward_item and content.has_item(quest.reward_item):
        parts.append(content.item(quest.reward_item).name)
    return f"Плата: {', '.join(parts)}."


def quest_button(quest: Quest) -> Label:
    return label(f"{quest.name} — уровень {quest.level}, плата {quest.reward_gold}")


def taken_button(quest: Quest, progress: int) -> Label:
    """Взятое задание остаётся на доске и называет свой счёт прямо на кнопке."""
    mark = "готово" if progress >= quest.target_count else "в работе"
    return label(f"{quest.name} — взято, {progress} из {quest.target_count}, {mark}")


def board_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
    city_id: str = "",
) -> Screen:
    """Что этот город даёт этому персонажу — и что тот уже взял.

    Взятое остаётся на доске нарочно: игрок соглашался на задание и через минуту
    не находил его там, где брал, и решал, что задание пропало.
    """
    city = city_id or character.city_id
    offered = quest_rules.available(content, character, city)
    working = tuple(
        step for step in quest_rules.taken(content, character) if step.quest.city_id == city
    )
    entries = [
        ListEntry(key=quest.id, text=quest_button(quest).text, detail=quest.giver)
        for quest in offered
    ]
    entries.extend(
        ListEntry(
            key=step.quest.id,
            text=taken_button(step.quest, step.progress).text,
            detail=step.quest.giver,
        )
        for step in working
    )
    ready = sum(1 for step in working if step.done)
    lead = [
        notice or "Доска заданий. Берут не всех и не всё.",
        f"Ваш уровень: {character.level}. Взято отсюда: {len(working)}.",
        "Нажмите задание, чтобы прочитать, что делать и куда идти.",
    ]
    if ready:
        lead.append(f"Готовы к сдаче: {ready}. Сдают у стойки таверны, кнопка «Сдать задание».")
    return paginated_screen(
        screen_id=ScreenId.QUEST_BOARD,
        title=f"Доска заданий, {content.city(city).name if content.has_city(city) else city}",
        entries=entries,
        state=state,
        lead_lines=tuple(lead),
        empty_text=(
            "Свободных заданий нет. Следующее откроется с уровнем или после того, "
            "как закроете начатое."
        ),
        show_filters=False,
    )


def offer_screen(
    content: GameContent,
    quest: Quest,
    character: Character | None = None,
    notice: str = "",
) -> Screen:
    """The conversation itself. Three answers, one of them is leaving."""
    progress = character.quests.progress(quest.id) if character is not None else 0
    is_taken = character is not None and character.quests.is_taken(quest.id)

    lines = [*head(f"{quest.giver}. {quest.intro}", notice), f"— {quest.terms}"]
    lines.extend(instructions(content, quest))
    lines.append(reward_line(content, quest))
    if is_taken:
        mark = "готово, можно сдавать" if progress >= quest.target_count else "в работе"
        lines.append(f"Задание уже взято: {progress} из {quest.target_count}, {mark}.")

    rows: list[tuple[Label, ...]] = []
    if not is_taken:
        rows.append((labels.QUEST_ACCEPT,))
    rows.append((labels.QUEST_ASK,))
    if is_taken:
        rows.append((labels.QUEST_ABANDON,))
    rows.append((labels.QUEST_LEAVE,))
    return Screen(id=ScreenId.QUEST_OFFER, lines=tuple(lines), rows=tuple(rows))


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
    lines = list(head(f"Задания. Взято: {len(steps)}.", notice))
    if not steps:
        lines.append("Ничего не взято. Задания дают в таверне города, «Доска заданий».")
    for step in steps:
        lines.append(step_line(content, step))
        lines.append(objective_line(content, step.quest))
        lines.append(where_line(content, step.quest))
    lines.append("Сдают там же, где брали: в таверне того города.")
    lines.append(f"Закрыто заданий за всё время: {len(character.quests.done)}.")
    return Screen(id=ScreenId.QUESTS, lines=tuple(line for line in lines if line))
