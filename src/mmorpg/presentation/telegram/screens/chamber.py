"""Управа: новое имя и наследие (ADR 0070).

Три экрана. Управа говорит, кто ты сейчас и что даст следующая ступень. Наследие —
это выбор вех, которые уйдут с тобой через сброс. Новое имя — одна кнопка и
предупреждение перед ней.

Наследие вынесено отдельным экраном не для порядка: вех у героя к сто пятидесятому
уровню пять, и строка с кнопкой у каждой в одном сообщении с управой не помещается
(``docs/accessibility.md``, правило 11).

Всё, что случится по нажатию, названо до него: что теряешь, что остаётся, сколько
очков и какой титул прибавит уход (``domain/rules/turning.py``).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import turning as turning_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head, plural


def carry_label(trait_name: str) -> Label:
    return label(f"Унести: {trait_name}")


def drop_label(trait_name: str) -> Label:
    return label(f"Оставить: {trait_name}")


def standing_line(content: GameContent, character: Character) -> str:
    """Кто игрок для Престола: сколько имён и под каким титулом."""
    count = character.remorts
    if count <= 0:
        return "Нового имени вы ещё не просили."
    word = plural(count, "имя", "имени", "имён")
    title = turning_rules.title(content, count)
    return f"Имён за плечами: {count} {word}. Титул: {title}."


def bonus_line(content: GameContent, character: Character) -> str:
    """Что взятые имена дают прямо сейчас. Пусто — ни одного не брали."""
    bonus = turning_rules.stat_bonus(content, character)
    if bonus <= 0:
        return ""
    return f"Ваши характеристики выше на {bonus:g} процентов — это плата за пройденное."


def chamber_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Управа: кто ты для Престола и что даст следующее имя."""
    step = turning_rules.next_rebirth(content, character)
    refused = turning_rules.refusal(content, character)

    lines = [
        *head("Управа.", notice),
        "Престольная контора: книга, печать и лист на каждое имя.",
        standing_line(content, character),
    ]
    standing = bonus_line(content, character)
    if standing:
        lines.append(standing)

    if step is None:
        lines.append("Ступеней больше нет: вы прошли все, какие Престол ведёт в книге.")
        return Screen(id=ScreenId.CHAMBER, lines=tuple(lines))

    lines.append(f"Следующее: «{step.name}», с {step.level} уровня. {step.text}")
    if step.lore:
        lines.append(step.lore)
    if step.unlocks:
        opened = [content.subclass(one).name for one in step.unlocks if content.has_subclass(one)]
        if opened:
            lines.append("Оно откроет ступени специализации: " + ", ".join(opened) + ".")
    if refused:
        lines.append(refused)

    rows: list[tuple[Label, ...]] = []
    if not refused:
        rows.append((labels.TURNING,))
    if step.legacy_slots:
        rows.append((labels.LEGACY,))
    return Screen(id=ScreenId.CHAMBER, lines=tuple(lines), rows=tuple(rows))


def legacy_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Наследие: какие вехи уйдут с вами через сброс.

    Называет игрок, а не порядок в файле: какая веха была для этой сборки
    главной, знает он один.
    """
    slots = turning_rules.legacy_slots(content, character)
    lines = [*head("Наследие.", notice)]
    if not slots:
        lines.append(
            "Наследие даёт новое имя, а ступеней у вас больше нет. Уносить некуда и нечего."
        )
        return Screen(id=ScreenId.LEGACY, lines=tuple(lines))

    word = plural(slots, "веху", "вехи", "вех")
    lines.append(
        f"Уход возвращает розданные очки нерозданными: характеристики упадут, и вехи "
        f"вместе с ними. Унести с собой можно {slots} {word} — они будут работать, "
        "чего бы ни показывали характеристики."
    )

    named = tuple(one for one in character.legacy_ids if content.has_trait(one))
    if named:
        lines.append("Названы: " + ", ".join(content.trait(one).name for one in named) + ".")
    else:
        lines.append("Пока не названо ничего.")
    lines.append(f"Занято мест: {len(named)} из {slots}.")

    rows: list[tuple[Label, ...]] = []
    for trait_id in named:
        rows.append((drop_label(content.trait(trait_id).name),))
    if len(named) < slots:
        for trait_id in turning_rules.may_carry(content, character):
            trait = content.trait(trait_id)
            lines.append(f"{trait.name}: {trait.text}")
            rows.append((carry_label(trait.name),))
    if not rows:
        lines.append("Вех вы пока не брали: их дают пороги характеристик.")
    return Screen(id=ScreenId.LEGACY, lines=tuple(lines), rows=tuple(rows))


def remort_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Новое имя: что теряешь, что остаётся, что прибавит. Кнопка тут необратима."""
    refused = turning_rules.refusal(content, character)
    if refused:
        return Screen(id=ScreenId.CHAMBER_REMORT, lines=(refused,))

    step = turning_rules.next_rebirth(content, character)
    assert step is not None  # refusal() сказал бы, что ступени нет
    points = content.rules.free_points_at_creation + step.stat_points
    carried = tuple(one for one in character.legacy_ids if content.has_trait(one))[
        : step.legacy_slots
    ]

    lines = [
        *head(f"Новое имя: {step.name}.", notice),
        "Уровень упадёт до первого, опыт обнулится. Дорогу предстоит пройти заново.",
        "Розданные очки характеристик вернутся нерозданными: собраться можно будет иначе.",
        "Останутся при вас: золото и банк, всё снаряжение, дерево умений с рангами, "
        "черты, ремёсла, задания, счёт арены, дом и взятые ступени специализации.",
        f"Характеристики станут выше на {step.stat_bonus:g} процентов — навсегда.",
        f"Нераспределённых очков на первом уровне будет {points}.",
    ]
    if carried:
        lines.append(
            "С вами уйдут вехи: " + ", ".join(content.trait(one).name for one in carried) + "."
        )
    elif step.legacy_slots:
        word = plural(step.legacy_slots, "веху", "вехи", "вех")
        lines.append(
            f"Наследие пусто, а унести можно {step.legacy_slots} {word}. "
            "Назвать их — на экране «Наследие»."
        )
    lines.append(f"Титул после ухода: {turning_rules.title(content, character.remorts + 1)}.")
    lines.append("Нажмёте «Подтвердить» — уход совершится сразу.")

    return Screen(
        id=ScreenId.CHAMBER_REMORT,
        lines=tuple(lines),
        rows=((labels.CONFIRM,),),
    )


def reborn_line(result: turning_rules.Reborn) -> str:
    """Что сказать про совершённый уход, в одну строку."""
    carried = f" Вех ушло с вами: {result.carried}." if result.carried else ""
    return (
        f"Новое имя взято: «{result.step.name}». Престол вписал вас как «{result.title}». "
        f"Уровень: 1. Нераспределённых очков: {result.stat_points}.{carried}"
    )
