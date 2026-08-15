"""The introduction screen: what to do next, and one button that goes there.

Six tasks, each one sentence long, each ending where the game already is - there
is no separate tutorial mode, no scripted corridor and nothing that has to be
finished before the game will let go. The button «Выполнить задание» opens the
screen the task lives on, because "go to Персонаж, then Характеристики" is a
route the player should not have to hold in their head.

Which task is which screen is decided here; the domain only counts them
(``mmorpg.domain.rules.tutorial``).
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import tutorial as rules
from mmorpg.domain.rules.tutorial import TutorialTask
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId


@dataclass(frozen=True, slots=True)
class TaskCard:
    """One task as the player hears it."""

    task: TutorialTask
    title: str
    text: str
    screen: ScreenId
    done_line: str


CARDS: dict[TutorialTask, TaskCard] = {
    TutorialTask.STATS: TaskCard(
        task=TutorialTask.STATS,
        title="Посмотреть характеристики",
        text=(
            "Откройте характеристики и прочитайте, что даёт каждая. "
            "Очки за уровень вкладываются там же."
        ),
        screen=ScreenId.STATS,
        done_line="Характеристики прочитаны.",
    ),
    TutorialTask.SKILL_SLOT: TaskCard(
        task=TutorialTask.SKILL_SLOT,
        title="Положить умение в слот",
        text=(
            "В бою работают только умения из панели. Положите умение в боевой слот: "
            "первое умение у вас уже есть."
        ),
        screen=ScreenId.SKILL_SLOTS,
        done_line="Умение лежит в слоте.",
    ),
    TutorialTask.QUEST: TaskCard(
        task=TutorialTask.QUEST,
        title="Взять подряд",
        text="Подряды берут в таверне. За работу платят золотом и опытом.",
        screen=ScreenId.QUEST_BOARD,
        done_line="Подряд взят.",
    ),
    TutorialTask.FIGHT: TaskCard(
        task=TutorialTask.FIGHT,
        title="Выиграть бой в локации",
        text=(
            "Локация — это узлы, соединённые тропами. Дойдите до узла с противником "
            "и выиграйте бой. Раны останутся с вами до лекаря."
        ),
        screen=ScreenId.LOCATION_LIST,
        done_line="Первый бой выигран.",
    ),
    TutorialTask.TRADE: TaskCard(
        task=TutorialTask.TRADE,
        title="Купить или продать в лавке",
        text="В лавке покупают снаряжение и сдают лишнее. Лавочник берёт дешевле, чем продаёт.",
        screen=ScreenId.SHOP,
        done_line="В лавке побывали.",
    ),
    TutorialTask.HAND_IN: TaskCard(
        task=TutorialTask.HAND_IN,
        title="Сдать подряд",
        text="Сделанный подряд сдают там же, где брали, — в таверне.",
        screen=ScreenId.TAVERN,
        done_line="Подряд сдан.",
    ),
}

DO_TASK = labels.label("Выполнить задание", "▶️")


def card_for(task: TutorialTask) -> TaskCard:
    return CARDS[task]


def tutorial_screen(character: Character, notice: str = "") -> Screen:
    """Where the introduction stands: what is done, what is next, one way there."""
    current = rules.next_task(character)
    done = rules.done_count(character)
    total = len(rules.ORDER)

    lines = [
        notice or f"Обучение. Сделано заданий: {done} из {total}.",
    ]
    if current is None:
        lines.append("Все задания сделаны, обучение больше не понадобится.")
        lines.append("Дальше игра идёт как идёт: подряды, локации, ремёсла, лавка.")
        return Screen(id=ScreenId.TUTORIAL, lines=tuple(lines), rows=())

    card = CARDS[current]
    lines.append(f"Задание: {card.title}.")
    lines.append(card.text)
    lines.append("Кнопка «Выполнить задание» откроет нужный экран.")
    lines.append("Задания можно не делать: обучение ничего не запирает.")
    for task in rules.ORDER:
        if rules.is_done(character, task):
            lines.append(CARDS[task].done_line)

    rows: tuple[tuple[Label, ...], ...] = ((DO_TASK,),)
    return Screen(id=ScreenId.TUTORIAL, lines=tuple(lines), rows=rows)


def completion_line(task: TutorialTask, character: Character) -> str:
    """Said once, right after a task is finished, on whatever screen finished it."""
    done = rules.done_count(character)
    total = len(rules.ORDER)
    if done >= total:
        return f"Задание обучения сделано: {CARDS[task].title.lower()}. Обучение пройдено."
    return (
        f"Задание обучения сделано: {CARDS[task].title.lower()}. "
        f"Сделано {done} из {total}, следующее — в разделе «Обучение»."
    )
