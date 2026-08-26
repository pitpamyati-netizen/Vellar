"""Отряд: экран, на котором его заводят, зовут в него и уходят из него.

Отряд - объединение игроков, и говорить о нём нечего, кроме того, кто в нём
стоит: мест в отряде нет, прибавок за место тоже, и обещать здесь нечего
(``domain/rules/party.py``). Экран отвечает на два вопроса - «есть ли у меня
отряд» и «кто в нём», - а кнопок на нём ровно столько, сколько сейчас работает.

Звать в отряд можно тремя дорогами, и все три названы вслух: именем с этого
экрана, кнопкой на своём узле локации и ответом на сообщение в игровой группе.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.rules.party import LEVEL_WINDOW, MAX_MEMBERS
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, head


@dataclass(frozen=True, slots=True)
class PartyView:
    """Что игра знает об отряде на момент отрисовки.

    Хендлер читает отряд из общего хранилища и приносит сюда уже имена: экран
    ничего не читает и ничего не пишет, как и всякий экран.
    """

    #: Имена в том порядке, в каком собрались; первое - того, кто завёл отряд.
    members: tuple[str, ...] = ()
    #: Отряд завёл тот, кто на него сейчас смотрит.
    leader: bool = False
    #: Кто зовёт этого игрока к себе. Пусто - никто не зовёт.
    caller: str = ""

    @property
    def gathered(self) -> bool:
        return bool(self.members)


def party_screen(view: PartyView, notice: str = "") -> Screen:
    """Отряд игрока целиком: кто в нём и что с ним можно сделать."""
    lines = [*head("Отряд.", notice)]
    rows: list[tuple[Label, ...]] = []

    if view.gathered:
        lines.append(f"В отряде: {amount(len(view.members), MAX_MEMBERS, with_percent=False)}.")
        for index, name in enumerate(view.members):
            lines.append(f"{name} — собрал отряд." if index == 0 else f"{name}.")
        lines.append(
            "Бой у отряда общий, а опыт и золото делятся поровну: впятером ходят ради "
            "боя, который в одиночку не берётся."
        )
        rows.append((labels.PARTY_INVITE,))
        rows.append((labels.PARTY_DISBAND,) if view.leader else (labels.PARTY_LEAVE,))
    else:
        lines.append("Вы идёте один.")
        lines.append(
            "Отряд — это те, с кем вы ходите вместе: подземелья, спуски и всё, что "
            "к ним прирастёт. Мест в отряде нет, каждый дерётся своим."
        )
        lines.append(f"Помещается в отряд: {MAX_MEMBERS} человек.")
        rows.append((labels.PARTY_CREATE,))

    if view.caller:
        lines.append(f"{view.caller} зовёт вас в отряд.")
        rows.append((labels.PARTY_ACCEPT, labels.PARTY_DECLINE))

    return Screen(id=ScreenId.PARTY, lines=tuple(lines), rows=tuple(rows))


def invite_screen(view: PartyView, notice: str = "") -> Screen:
    """Кого позвать. Имя набирают одним сообщением - других полей здесь нет."""
    lines = [
        *head("Пригласить в отряд.", notice),
        "Напишите имя того, кого зовёте, одним сообщением.",
        f"Звать можно того, чей уровень не дальше {LEVEL_WINDOW} от вашего, "
        f"пока в отряде меньше {MAX_MEMBERS} человек.",
        "Позванный соглашается сам: у него появится «Пойти вместе».",
        "Ещё две дороги: кнопка «Позвать в отряд» рядом с тем, кто стоит с вами "
        "на одном узле локации, и слово «пригласить» ответом на его сообщение в "
        "игровой группе.",
    ]
    if view.gathered:
        lines.append(f"Сейчас в отряде: {', '.join(view.members)}.")
    return Screen(id=ScreenId.PARTY_INVITE, lines=tuple(lines), rows=())
