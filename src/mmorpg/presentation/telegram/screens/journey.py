"""Возвращение и история говорят о нынешнем деле без служебных номеров."""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.application.journey import Activity
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import tutorial
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.keyboards.labels import TUTORIAL, Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.play import standing_in
from mmorpg.presentation.telegram.screens.tutorial import CARDS

CONTINUE = label("Продолжить дело")
HISTORY = label("История действий")
RETURN = label("Вернуться к делу")


def activity_line(activity: Activity, content: GameContent | None = None) -> str:
    parts = []
    for name, value in (
        ("золото при себе", activity.gold),
        ("золото в банке", activity.bank),
        ("опыт", activity.experience),
        ("уровни", activity.levels),
        ("шаги обучения", activity.tutorial_steps),
        ("сданные задания", activity.quests),
    ):
        if value:
            parts.append(f"{name}: {'плюс' if value > 0 else 'минус'} {abs(value)}")
    for item_id, value in activity.items:
        name = content.item(item_id).name if content and content.has_item(item_id) else "Вещь"
        parts.append(f"{name}: {'получено' if value > 0 else 'отдано'} {abs(value)}")
    return "; ".join(parts) + "."


def returning_screen(
    content: GameContent,
    character: Character,
    flow: PlayState,
    *,
    battle: str = "",
    latest: Activity | None = None,
) -> Screen:
    city = standing_in(content, character)
    lines = [f"Возвращение. {character.name}, уровень {character.level}.", f"Город: {city.name}."]
    if battle:
        lines.append(battle)
    elif flow.descent.active:
        lines.append(
            f"Сохранён поход в подземелье: слой {flow.descent.layer + 1}. "
            "Продолжите с текущей комнаты."
        )
    elif flow.session.active and content.has_city(flow.session.city_id):
        saved_city = content.city(flow.session.city_id)
        if saved_city.has_location(flow.session.slot):
            place = saved_city.location(flow.session.slot)
            lines.append(
                f"Вылазка: {place.name}, узел {flow.session.node + 1}. "
                "Округа могла обновиться; продолжение покажет нынешнее состояние."
            )
    else:
        lines.append("Начатого боя или похода сейчас нет.")
    if latest:
        lines.append("Последнее сохранённое изменение: " + activity_line(latest, content))
    for quest_id, count in tuple(character.quests.taken.items())[:3]:
        if content.has_quest(quest_id):
            quest = content.quest(quest_id)
            lines.append(
                f"Задание «{quest.name}»: {count} из {quest.target_count}. "
                + (
                    (
                        "Можно сдать у наставника на экране «Ступень»."
                        if quest.is_trial
                        else "Можно сдать в таверне."
                    )
                    if count >= quest.target_count
                    else "Продолжите работу; подробности — в «Заданиях»."
                )
            )
    current = tutorial.next_task(character)
    if current:
        lines.append(
            f"Следующее дело обучения: {CARDS[current].title.lower()}. "
            "Нажмите «Обучение» или наберите /обучение."
        )
    else:
        lines.append("Обучение пройдено. Задания и локации доступны через главное меню.")
    lines.append("/дело — продолжить сохранённый экран; /история — результаты действий.")
    rows: list[tuple[Label, ...]] = [(CONTINUE, HISTORY)]
    if current and not battle:
        rows.append((TUTORIAL,))
    return Screen(ScreenId.RETURNING, tuple(lines), tuple(rows))


def history_screen(
    activities: Sequence[Activity],
    *,
    page: int = 1,
    more: bool = False,
    content: GameContent | None = None,
) -> Screen:
    lines = [
        f"История действий. Страница {page}.",
        "Сохранённые изменения героя, от новых к прежним. Частная переписка сюда не входит.",
    ]
    lines.extend(
        f"{(page - 1) * 5 + index}. {activity_line(activity, content)}"
        for index, activity in enumerate(activities, 1)
    )
    if not activities:
        lines.append("Сохранённых изменений пока нет. Продолжите обучение или начатое дело.")
    rows = [(CONTINUE,)]
    if page > 1:
        rows.append((label(f"/история {page - 1}"),))
    if more:
        rows.append((label(f"/история {page + 1}"),))
    return Screen(ScreenId.HISTORY, tuple(lines), tuple(rows))
