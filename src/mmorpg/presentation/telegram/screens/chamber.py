"""Управа: новое имя и голос в Большом совете.

Три экрана. Управа говорит, кто ты сейчас и о чём спрашивает совет. Новое имя —
это одна кнопка и предупреждение перед ней: уровень падает до первого.
Голосование вынесено отдельно не для порядка — у вопроса столько строк, сколько
ответов, и в одном сообщении с управой он бы туда не влез (``docs/accessibility.md``,
правило 11).

Всё, что случится по нажатию, названо до него: сколько уровней теряешь, что
остаётся, сколько очков и какой титул прибавит уход (``domain/rules/turning.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head, plural


def answer_label(option_name: str) -> Label:
    return label(f"Ответить: {option_name}")


def standing_line(character: Character) -> str:
    """Кто игрок для Престола: сколько уходов и под каким титулом."""
    count = character.remorts
    if count <= 0:
        return "Нового имени вы ещё не просили."
    word = plural(count, "уход", "ухода", "уходов")
    return f"Уходов за плечами: {count} {word}. Титул: {turning_rules.title(count)}."


def chamber_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Управа: кто ты для Престола и о чём спрашивает совет."""
    turning = content.open_turning()
    refused = turning_rules.refusal(character)

    lines = [
        *head("Управа.", notice),
        "Престольная контора: книга, печать и вопросы совета.",
        "Новое имя просят со сто пятидесятого уровня: уровень падает до первого, а золото, "
        "вещи и изученные умения остаются при вас. За уход дают очки характеристик "
        "и титул.",
        standing_line(character),
    ]
    if turning is None:
        lines.append("Совет сейчас ни о чём не спрашивает.")
    else:
        lines.append(f"Открыт вопрос совета: {turning.name}. {turning.question}")
    if refused:
        lines.append(refused)

    rows: list[tuple[Label, ...]] = []
    if not refused:
        rows.append((labels.TURNING,))
    if turning is not None:
        rows.append((labels.TURNING_QUESTION,))
    return Screen(id=ScreenId.CHAMBER, lines=tuple(lines), rows=tuple(rows))


def remort_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Новое имя: что теряешь, что остаётся, что прибавит. Кнопка тут необратима."""
    refused = turning_rules.refusal(character)
    if refused:
        return Screen(id=ScreenId.CHAMBER_REMORT, lines=(refused,))

    gift = turning_rules.stat_gift(character.remorts)
    next_title = turning_rules.title(character.remorts + 1)
    gift_line = (
        f"Нераспределённых очков характеристик станет на {gift} больше."
        if gift
        else "Очков характеристик этот уход уже не прибавит: потолок прибавки взят."
    )
    lines = (
        *head("Новое имя.", notice),
        "Уровень упадёт до первого, опыт обнулится. Дорогу с первого до сто пятидесятого "
        "предстоит пройти заново.",
        "Останутся при вас: золото и банк, всё снаряжение, дерево умений с рангами "
        "и гранями, черты, ремёсла, задания и счёт арены.",
        gift_line,
        f"Титул после ухода: {next_title}.",
        "Нажмёте «Подтвердить» — уход совершится сразу.",
    )
    return Screen(
        id=ScreenId.CHAMBER_REMORT,
        lines=lines,
        rows=((labels.CONFIRM,),),
    )


def turning_screen(
    content: GameContent,
    character: Character,
    *,
    tally: Mapping[str, int] | None = None,
    notice: str = "",
) -> Screen:
    """Голосование совета: сам вопрос, счёт по нему и кнопка на каждый ответ."""
    turning = content.open_turning()
    counted = tally or {}
    if turning is None:
        return Screen(
            id=ScreenId.TURNING,
            lines=(
                notice or "Совет сейчас ни о чём не спрашивает.",
                "Совет считает прошлый цикл. Новое имя берут и без вопроса: голос за ним остаётся.",
            ),
        )

    total = sum(counted.values())
    lines = [
        *head(f"Голосование: {turning.name}.", notice),
        turning.question,
        turning.text,
        f"Подано голосов: {total}. Голос весит столько, сколько за ним уходов.",
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
        lines.append("Голос дают за уход: пока нового имени нет, совет слушает, но не считает.")

    rows: tuple[tuple[Label, ...], ...] = ()
    if turning_rules.may_answer(character):
        rows = tuple((answer_label(option.name),) for option in turning.options)
    return Screen(id=ScreenId.TURNING, lines=tuple(lines), rows=rows)


def reborn_line(result: turning_rules.Reborn) -> str:
    """Что сказать про совершённый уход, в одну строку."""
    got = f" Очков характеристик прибавилось: {result.stat_points}." if result.stat_points else ""
    return f"Новое имя взято. Престол вписал вас как «{result.title}». Уровень: 1.{got}"
