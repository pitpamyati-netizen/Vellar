"""Гильдия: экраны, на которых её заводят, ведут и держат казну.

Гильдия - объединение надолго (``domain/rules/guild.py``, ADR 0030). Экран
отвечает на «в гильдии ли я», «какое у меня звание» и «что в казне», а кнопок на
нём ровно столько, сколько даёт звание смотрящего: участник кладёт в казну, но
не берёт; звания раздаёт основатель.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.rules.guild import FOUND_COST, FOUND_LEVEL, MAX_MEMBERS, GuildRank
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold, head
from mmorpg.presentation.telegram.screens.paginated import PageState, paging_row, total_pages

#: Суммы, которыми двигают казну - те же, что у банка.
VAULT_STEPS: tuple[int, ...] = (50, 250, 1000)


@dataclass(frozen=True, slots=True)
class GuildView:
    """Что игра знает о гильдии на момент отрисовки. Экран ничего не читает."""

    name: str = ""
    my_rank: GuildRank | None = None
    #: (имя, звание) в порядке: основатель, офицеры, участники.
    members: tuple[tuple[str, GuildRank], ...] = ()
    vault_gold: int = 0
    my_gold: int = 0
    #: Имя гильдии, которая зовёт этого игрока. Пусто - никто не зовёт.
    caller: str = ""

    @property
    def joined(self) -> bool:
        return self.my_rank is not None

    @property
    def founder(self) -> bool:
        return self.my_rank is GuildRank.FOUNDER

    @property
    def officer(self) -> bool:
        return self.my_rank is not None and self.my_rank >= GuildRank.OFFICER


def guild_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [*head("Гильдия.", notice)]
    rows: list[tuple[Label, ...]] = []

    if view.my_rank is not None:
        lines.append(f"«{view.name}». Ваше звание: {view.my_rank.title}.")
        lines.append(f"В гильдии: {amount(len(view.members), MAX_MEMBERS, with_percent=False)}.")
        lines.append(f"В казне: {gold(view.vault_gold)}.")
        rows.append((labels.GUILD_ROSTER, labels.GUILD_VAULT))
        if len(view.members) > 1:
            rows.append((labels.GUILD_TRANSFER,))
        if view.officer:
            rows.append((labels.GUILD_INVITE,))
        rows.append((labels.GUILD_DISBAND,) if view.founder else (labels.GUILD_LEAVE,))
    else:
        lines.append("Вы не в гильдии.")
        lines.append(
            "Гильдия - это надолго: десятки человек, звания и общая казна, из "
            "которой берут по званию. Отряд собирают на бой, гильдию - на месяцы."
        )
        lines.append(
            f"Основать свою можно с {FOUND_LEVEL} уровня, грамота стоит {FOUND_COST} золота."
        )
        rows.append((labels.GUILD_FOUND,))

    if view.caller:
        lines.append(f"Гильдия «{view.caller}» зовёт вас к себе.")
        rows.append((labels.GUILD_ACCEPT, labels.GUILD_DECLINE))

    return Screen(id=ScreenId.GUILD, lines=tuple(lines), rows=tuple(rows))


def found_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head("Основать гильдию.", notice),
        "Напишите имя гильдии одним сообщением.",
        f"Нужен {FOUND_LEVEL} уровень и {FOUND_COST} золота на грамоту. "
        f"У вас {gold(view.my_gold)}.",
        "Вы станете основателем: только он раздаёт звания, зовёт офицеров и распускает гильдию.",
    ]
    return Screen(id=ScreenId.GUILD_FOUND, lines=tuple(lines), rows=())


def invite_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head("Позвать в гильдию.", notice),
        "Напишите имя того, кого зовёте, одним сообщением.",
        "Звать может основатель и офицер. Позванный соглашается сам.",
    ]
    if view.members:
        lines.append(f"Сейчас в гильдии: {len(view.members)} из {MAX_MEMBERS}.")
    return Screen(id=ScreenId.GUILD_INVITE, lines=tuple(lines), rows=())


#: Сколько человек показывает одна страница состава. В гильдии их до тридцати, а
#: одним сообщением тридцать имён со званиями и тридцатью рядами кнопок не
#: читаются: список режется, как режется всякий длинный список в игре
#: (``docs/accessibility.md``, правило 7).
ROSTER_PAGE = 8


def roster_screen(view: GuildView, page: PageState | None = None, notice: str = "") -> Screen:
    """Состав гильдии, страницами. Кнопки - только у тех, кто на этой странице.

    Звание раздаёт основатель, выгоняет и офицер; кнопки несут имя, поэтому
    страница их не путает: человек остаётся собой на любой странице.
    """
    pages = total_pages(len(view.members), ROSTER_PAGE)
    state = (page or PageState()).clamped(pages)
    first = (state.page - 1) * ROSTER_PAGE
    visible = view.members[first : first + ROSTER_PAGE]

    lines = [*head(f"Состав гильдии «{view.name}».", notice)]
    lines.append(
        f"В гильдии: {amount(len(view.members), MAX_MEMBERS, with_percent=False)}, "
        f"страница {state.page} из {pages}."
        if pages > 1
        else f"В гильдии: {amount(len(view.members), MAX_MEMBERS, with_percent=False)}."
    )
    rows: list[tuple[Label, ...]] = []
    for name, rank in visible:
        lines.append(f"{name} — {rank.title}.")
    if view.founder:
        lines.append("Вы основатель: можно поднять до офицера, опустить до участника, выгнать.")
        for name, rank in visible:
            if rank is GuildRank.FOUNDER:
                continue
            controls: list[Label] = []
            if rank is GuildRank.MEMBER:
                controls.append(labels.guild_promote_label(name))
            else:
                controls.append(labels.guild_demote_label(name))
            controls.append(labels.guild_kick_label(name))
            rows.append(tuple(controls))
    elif view.officer:
        lines.append("Вы офицер: можно выгнать участника.")
        for name, rank in visible:
            if rank is GuildRank.MEMBER:
                rows.append((labels.guild_kick_label(name),))
    else:
        lines.append("Звания раздаёт основатель.")
    if pages > 1:
        rows.append(paging_row(state.page, pages))
    # Число страниц объявляет сам экран: по нему их листает общий разбор
    # (``flows/play.advance``, ``LIST_PAGE_FIELD``), и второй раз его никто не считает.
    return Screen(
        id=ScreenId.GUILD_ROSTER,
        lines=tuple(lines),
        rows=tuple(rows),
        metadata={"page": str(state.page), "pages": str(pages), "count": str(len(view.members))},
    )


def vault_screen(view: GuildView, notice: str = "") -> Screen:
    lines = [
        *head(f"Казна гильдии «{view.name}».", notice),
        f"В казне: {gold(view.vault_gold)}. У вас на руках: {gold(view.my_gold)}.",
        "Класть может каждый в гильдии. Берёт основатель и офицер.",
    ]
    rows: list[tuple[Label, ...]] = [
        tuple(labels.guild_deposit_label(step) for step in VAULT_STEPS)
    ]
    if view.officer:
        rows.append(tuple(labels.guild_withdraw_label(step) for step in VAULT_STEPS))
    return Screen(id=ScreenId.GUILD_VAULT, lines=tuple(lines), rows=tuple(rows))
