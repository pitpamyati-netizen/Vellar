"""Вступление: подсказки прямо на экранах и награда за каждый шаг (ADR 0038).

Шесть шагов, каждый длиной в предложение, и каждый кончается там, где игра и так
находится: отдельного режима обучения нет, нет ни сценарного коридора, ни того,
что надо закончить, прежде чем игра отпустит. Экран, на котором живёт незакрытый
шаг, сам говорит, что сделать (``hint_line``), а кнопка «Обучение» в меню ведёт
туда напрямую — «идите в Персонаж, потом в Характеристики» игрок держать в голове
не должен.

Какая задача на каком экране, решается здесь; домен их только считает и платит
(``mmorpg.domain.rules.tutorial``, ``adventure.apply_tutorial_rewards``).
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
    #: Короткая строка, которую экран шага показывает первой, пока шаг не закрыт.
    hint: str


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
        hint="Шаг обучения: прочитайте, что даёт каждая характеристика.",
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
        hint="Шаг обучения: нажмите слот и положите в него умение — в бою работают только эти.",
    ),
    TutorialTask.QUEST: TaskCard(
        task=TutorialTask.QUEST,
        title="Взять задание",
        text="Задания берут в таверне. За работу платят золотом и опытом.",
        screen=ScreenId.QUEST_BOARD,
        done_line="Задание взято.",
        hint="Шаг обучения: возьмите задание с доски — за работу платят золотом и опытом.",
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
        hint="Шаг обучения: дойдите до узла с противником и выиграйте бой.",
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
        hint="Шаг обучения: купите или продайте вещь — лавочник берёт дешевле, чем продаёт.",
    ),
    TutorialTask.HAND_IN: TaskCard(
        task=TutorialTask.HAND_IN,
        title="Сдать задание",
        text="Сделанное задание сдают там же, где брали, — в таверне.",
        screen=ScreenId.TAVERN,
        done_line="Задание сдано.",
        hint="Шаг обучения: сдайте досчитанное задание здесь же, в таверне.",
    ),
}

DO_TASK = labels.label("Перейти к шагу", "▶️")

#: На каком экране показывать подсказку какого шага. Дочерние экраны шага
#: (карточка вещи, разговор о задании) считаются тем же шагом.
_HINT_SCREENS: dict[ScreenId, TutorialTask] = {
    ScreenId.STATS: TutorialTask.STATS,
    ScreenId.SKILL_SLOTS: TutorialTask.SKILL_SLOT,
    ScreenId.SKILL_PICK: TutorialTask.SKILL_SLOT,
    ScreenId.QUEST_BOARD: TutorialTask.QUEST,
    ScreenId.QUEST_OFFER: TutorialTask.QUEST,
    # На самом экране локации подсказки нет: он и так самый плотный (тропы, узлы,
    # босс, провал). Шаг «выиграть бой» объясняет список локаций.
    ScreenId.LOCATION_LIST: TutorialTask.FIGHT,
    ScreenId.SHOP: TutorialTask.TRADE,
    ScreenId.SHOP_ITEM: TutorialTask.TRADE,
    ScreenId.SELL: TutorialTask.TRADE,
    ScreenId.TAVERN: TutorialTask.HAND_IN,
}

#: Награда, чтобы называть её одинаково на экране обучения и в подтверждении шага.
_STEP_REWARD_LINE = (
    f"За каждый шаг — {rules.STEP_REWARD.experience} опыта и {rules.STEP_REWARD.gold} золота, "
    "за всё обучение — доспех первой ступени в пустые слоты, зелья, опыт и золото."
)


def hint_line(screen_id: ScreenId, character: Character) -> str:
    """Строка-подсказка для незакрытого шага, что живёт на этом экране. Пусто — нечего сказать."""
    task = _HINT_SCREENS.get(screen_id)
    if task is None or rules.finished(character) or rules.is_done(character, task):
        return ""
    return CARDS[task].hint


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
    lines.append(_STEP_REWARD_LINE)
    lines.append("Кнопка «Перейти к шагу» откроет нужный экран. Шаги можно не делать.")
    for task in rules.ORDER:
        if rules.is_done(character, task):
            lines.append(CARDS[task].done_line)

    rows: tuple[tuple[Label, ...], ...] = ((DO_TASK,),)
    return Screen(id=ScreenId.TUTORIAL, lines=tuple(lines), rows=rows)


def completion_line(task: TutorialTask, character: Character) -> str:
    """Говорится один раз, сразу после того, как дело закончено, на том экране, который его
    закончил. О самой награде говорит отдельная строка от ``apply_tutorial_rewards``.
    """
    done = rules.done_count(character)
    total = len(rules.ORDER)
    if done >= total:
        return f"Шаг обучения сделан: {CARDS[task].title.lower()}. Обучение пройдено."
    return (
        f"Шаг обучения сделан: {CARDS[task].title.lower()}. "
        f"Сделано {done} из {total}, дальше подскажет экран нужного дела."
    )
