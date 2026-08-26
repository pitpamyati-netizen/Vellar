"""Экран вступления: что делать дальше и одна кнопка, которая туда ведёт.

Шесть шагов, каждый длиной в предложение, и каждый кончается там, где игра и так
находится: отдельного режима обучения нет, нет ни сценарного коридора, ни того,
что надо закончить, прежде чем игра отпустит. Кнопка «Перейти к шагу» открывает
тот экран, на котором шаг живёт, потому что «идите в Персонаж, потом в
Характеристики» — это дорога, которую игрок не должен держать в голове.

Какая задача на каком экране, решается здесь; домен их только считает
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
    """Один шаг в том виде, в каком его слышит игрок."""

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
        text=(
            "В лавке покупают снаряжение и сдают лишнее. Лавочник берёт дешевле, чем продаёт. "
            "Надеть можно любую вещь, даже не для вашего класса: запрета нет, "
            "но за чужое оружие и чужой доспех вы платите точностью и инициативой, "
            "а умения, которые просят своё оружие, с чужим не сработают. "
            "Карточка вещи говорит об этом до того, как вы её наденете."
        ),
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
    """Где стоит вступление: что сделано, что дальше и одна дорога туда."""
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
    """Говорится один раз, сразу после того, как дело закончено, на том экране, который его
    закончил.
    """
    done = rules.done_count(character)
    total = len(rules.ORDER)
    if done >= total:
        return f"Шаг обучения сделан: {CARDS[task].title.lower()}. Обучение пройдено."
    return (
        f"Шаг обучения сделан: {CARDS[task].title.lower()}. "
        f"Сделано {done} из {total}, следующее — в разделе «Обучение»."
    )
