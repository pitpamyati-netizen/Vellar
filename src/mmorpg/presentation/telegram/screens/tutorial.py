"""The introduction screen: what to do next, and one button that goes there.

Six steps, each one sentence long, each ending where the game already is - there
is no separate tutorial mode, no scripted corridor and nothing that has to be
finished before the game will let go. The button «Перейти к шагу» opens the
screen the step lives on, because "go to Персонаж, then Характеристики" is a
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
from mmorpg.presentation.telegram.screens.format import head


@dataclass(frozen=True, slots=True)
class TaskCard:
    """One step as the player hears it."""

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
        title="Взять задание",
        text="Задания берут в таверне. За работу платят золотом и опытом.",
        screen=ScreenId.QUEST_BOARD,
        done_line="Задание взято.",
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
        title="Сдать задание",
        text="Сделанное задание сдают там же, где брали, — в таверне.",
        screen=ScreenId.TAVERN,
        done_line="Задание сдано.",
    ),
}

DO_TASK = labels.label("Перейти к шагу", "▶️")


def card_for(task: TutorialTask) -> TaskCard:
    return CARDS[task]


def tutorial_screen(character: Character, notice: str = "") -> Screen:
    """Where the introduction stands: what is done, what is next, one way there."""
    current = rules.next_task(character)
    done = rules.done_count(character)
    total = len(rules.ORDER)

    lines = list(head(f"Обучение. Сделано шагов: {done} из {total}.", notice))
    if current is None:
        lines.append("Все шаги сделаны, обучение больше не понадобится.")
        lines.append("Дальше игра идёт своим ходом: задания, локации, ремёсла, лавка.")
        return Screen(id=ScreenId.TUTORIAL, lines=tuple(lines), rows=())

    card = CARDS[current]
    lines.append(f"Шаг: {card.title}.")
    lines.append(card.text)
    lines.append("Кнопка «Перейти к шагу» откроет нужный экран.")
    lines.append("Шаги можно не делать: обучение ничего не запирает.")
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
        return f"Шаг обучения сделан: {CARDS[task].title.lower()}. Обучение пройдено."
    return (
        f"Шаг обучения сделан: {CARDS[task].title.lower()}. "
        f"Сделано {done} из {total}, следующее — в разделе «Обучение»."
    )
