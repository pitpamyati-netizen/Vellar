"""Палата: Оборот, Печать и счётный вопрос.

Три экрана, по делу на каждый. Палата говорит, что берёт и что за это открывает;
заклад — список того, что можно отдать; счётный вопрос — сам вопрос, счёт голосов
по нему и кнопка на каждый ответ. Вопрос вынесен отдельно не для порядка: у него
столько строк, сколько ответов, и в одном сообщении с Палатой он бы туда не влез
(``docs/accessibility.md``, правило 11).

Всё, что решается вещами, названо до нажатия: чего Палата просит сейчас, сколько
у вас Печатей, насколько глубже стал спуск и на каком ранге теперь открывается
грань (``domain/rules/turning.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head, plural
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

#: Как заклад назван на кнопке: разновидность видна до нажатия.
ITEM_PREFIX = "Вещь: "
EDGE_PREFIX = "Грань: "


def answer_label(option_name: str) -> Label:
    return label(f"Ответить: {option_name}")


def seals_line(character: Character) -> str:
    count = character.seals
    word = plural(count, "Печать", "Печати", "Печатей")
    return f"У вас {count} {word}."


def opened_lines(content: GameContent, character: Character) -> tuple[str, ...]:
    """Что Печати уже открыли. Пусто у того, кто ни одного Оборота не совершил."""
    if character.seals <= 0:
        return ()
    return (
        f"Спуск идёт глубже: схваток подряд {turning_rules.descent_depth(character)}.",
        f"Грань умения открывается с ранга {skill_rules.edge_rank_for(content, character)}.",
        "Характеристики Печать не растит и не растила: она открывает доступы.",
    )


def chamber_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Палата: что она берёт, что открывает и о чём сейчас спрашивает."""
    turning = content.open_turning()
    refused = turning_rules.refusal(character)
    wanted = turning_rules.asking(character.seals)

    lines = [
        *head("Дорожная палата.", notice),
        "Весы, книга и печать, которую признают все пятнадцать городов.",
        "Оборот — это заклад: вы отдаёте надетую вещь или грань умения и получаете "
        "Печать. Уровень и опыт остаются при вас.",
        seals_line(character),
        *opened_lines(content, character),
        f"Сейчас Палата примет вещь не ниже уровня {wanted} или грань умения "
        f"полного ранга {turning_rules.PLEDGE_EDGE_RANK}.",
        "Заложенное не возвращают и второй раз не принимают.",
    ]
    if turning is None:
        lines.append("Счётного вопроса сейчас нет: Палата считает прошлый цикл.")
    else:
        lines.append(f"Открыт счётный вопрос: {turning.name}. {turning.question}")
    if refused:
        lines.append(refused)

    rows: list[tuple[Label, ...]] = []
    if not refused:
        rows.append((labels.TURNING,))
    if turning is not None:
        rows.append((labels.TURNING_QUESTION,))
    return Screen(id=ScreenId.CHAMBER, lines=tuple(lines), rows=tuple(rows))


def turning_screen(
    content: GameContent,
    character: Character,
    *,
    tally: Mapping[str, int] | None = None,
    notice: str = "",
) -> Screen:
    """Счётный вопрос: сам вопрос, счёт по нему и кнопка на каждый ответ."""
    turning = content.open_turning()
    counted = tally or {}
    if turning is None:
        return Screen(
            id=ScreenId.TURNING,
            lines=(
                notice or "Счётного вопроса сейчас нет.",
                "Палата считает прошлый цикл. Обороты совершают и без вопроса: "
                "Печати остаются при вас и голос свой не теряют.",
            ),
        )

    total = sum(counted.values())
    lines = [
        *head(f"Счётный вопрос: {turning.name}.", notice),
        turning.question,
        turning.text,
        f"Подано голосов: {total}. Голос весит столько, сколько Оборотов за ним.",
    ]
    lines.extend(
        f"{option.name}: голосов {counted.get(option.id, 0)}. {option.text}"
        for option in turning.options
    )

    ahead = turning_rules.leading(counted)
    if ahead and turning.has_option(ahead):
        lines.append(f"Впереди: {turning.option(ahead).name}.")
    elif total:
        lines.append("Впереди никто: голоса разошлись поровну.")

    mine = turning_rules.answered(character, turning)
    if mine and turning.has_option(mine):
        lines.append(f"Ваш голос отдан за: {turning.option(mine).name}. Его можно переменить.")
    elif turning_rules.may_answer(character):
        lines.append("Ваш голос ещё не подан.")
    else:
        lines.append("Голос дают за Оборот: пока Печати нет, Палата слушает, но не считает.")

    rows: tuple[tuple[Label, ...], ...] = ()
    if turning_rules.may_answer(character):
        rows = tuple((answer_label(option.name),) for option in turning.options)
    return Screen(id=ScreenId.TURNING, lines=tuple(lines), rows=rows)


def pledge_entries(content: GameContent, character: Character) -> tuple[ListEntry, ...]:
    """Всё, что Палата примет прямо сейчас, одним списком."""
    entries = [
        ListEntry(
            key=turning_rules.pledge_key(turning_rules.ITEM_PLEDGE, item.id),
            text=f"{ITEM_PREFIX}{item.name}",
            detail=f"уровень {item.level}",
        )
        for item in turning_rules.pledgeable_items(content, character)
    ]
    entries.extend(
        ListEntry(
            key=turning_rules.pledge_key(turning_rules.EDGE_PLEDGE, skill.code),
            text=f"{EDGE_PREFIX}{skill.name}",
            detail=_edge_detail(character, skill.code, content),
        )
        for skill in turning_rules.pledgeable_edges(content, character)
    )
    return tuple(entries)


def _edge_detail(character: Character, skill_code: str, content: GameContent) -> str:
    edge_code = character.loadout.edge_of(skill_code)
    if edge_code is None:
        return "грань не выбрана"
    return f"грань «{content.skill(skill_code).edge(edge_code).name}»"


def pledge_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Что отдать. Нажатие здесь — это уже Оборот, и экран говорит об этом до него."""
    entries = pledge_entries(content, character)
    return paginated_screen(
        screen_id=ScreenId.CHAMBER_PLEDGE,
        title="Заклад Палате",
        entries=entries,
        state=state,
        lead_lines=(
            notice or "Выбранное уходит Палате сразу: подтверждения не спросят.",
            f"Вещь принимают не ниже уровня {turning_rules.asking(character.seals)}, "
            f"грань — с ранга {turning_rules.PLEDGE_EDGE_RANK}.",
        ),
        empty_text=(
            "Отдать сейчас нечего. Наденьте вещь нужного уровня или доведите "
            "умение с выбранной гранью до полного ранга."
        ),
        show_filters=False,
    )


def entry_for(content: GameContent, character: Character, pressed: str) -> str:
    """Ключ заклада, который назвала нажатая кнопка. Пусто — не назвала ничего."""
    for entry in pledge_entries(content, character):
        if entry.as_label().matches(pressed):
            return entry.key
    return ""


def sealed_line(result: turning_rules.Sealed) -> str:
    """Что сказать про совершённый Оборот, в одну строку."""
    return (
        f"Оборот совершён. Палата приняла: {result.given}. "
        f"{seals_line(result.character)} Уровень остался прежним."
    )
