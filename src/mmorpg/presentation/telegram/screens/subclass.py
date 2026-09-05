"""Ступень специализации: кем этот класс стал (ADR 0069).

Один экран на все три ступени. Показывается та, на которой игроку есть что
решать; уже взятые названы строкой сверху, потому что решение необратимо и
игрок обязан видеть, что именно он уже выбрал.

Все ступени этого уровня показываются всегда - и доступные, и закрытые.
Закрытая ступень с названной ценой это цель; ступень, вычеркнутая из списка до
того, как её можно взять, - это то, о чём игрок никогда не узнает и к чему,
значит, не станет готовиться.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Subclass
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

#: Как называется каждая ступень словами игрока. Читается из содержимого нельзя -
#: имена ступеней это часть разговора, а не часть баланса.
TIER_NAMES: dict[int, str] = {0: "Путь", 1: "Гибрид", 3: "Венец"}


def choose_label(name: str) -> Label:
    return label(f"Стать: {name}")


def tier_name(tier: int) -> str:
    return TIER_NAMES.get(tier, f"Ступень {tier}")


def _offer_lines(content: GameContent, character: Character, one: Subclass) -> tuple[str, ...]:
    """Одна ступень: что она делает и чего стоит, если ещё не взята."""
    lines = [f"{one.name} — {one.role}. {one.text}"]
    if one.lore:
        lines.append(one.lore)
    refused = subclass_rules.refusal(content, character, one)
    if refused:
        lines.append(f"Пока нельзя: {refused}")
        for code, have, need in subclass_rules.stat_gate_progress(content, character, one):
            lines.append(f"{code.value}: {have} из {need}.")
    return tuple(lines)


def subclass_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Ступени специализации: взятые, открытая и то, что на ней предлагают."""
    steps = subclass_rules.taken(content, character)
    tier = subclass_rules.open_tier(content, character)

    lines = [*head("Ступень.", notice)]
    if steps:
        lines.append(
            "Вы уже: " + ", ".join(f"{one.name} ({tier_name(one.tier)})" for one in steps) + "."
        )
    else:
        lines.append("Ступеней вы пока не брали: класс есть, а имени внутри класса ещё нет.")

    if tier is None:
        lines.append("Все ступени пройдены. Дальше растёт только то, что вы вложили.")
        return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines))

    offered = subclass_rules.offered(content, character, tier)
    if not offered:
        lines.append("На этой ступени выбирать пока не из чего.")
        return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines))

    lines.append(f"Открыта ступень «{tier_name(tier)}». Выбранное на ней не меняют.")
    rows: list[tuple[Label, ...]] = []
    for one in offered:
        lines.extend(_offer_lines(content, character, one))
        if not subclass_rules.refusal(content, character, one):
            rows.append((choose_label(one.name),))
    if not rows:
        lines.append("Ни одна из них вам сейчас не открыта. Цена каждой названа выше.")
    return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines), rows=tuple(rows))


def chosen_line(one: Subclass) -> str:
    """Что сказать про взятую ступень, в одну строку."""
    return f"Вы стали: {one.name}. {one.text}"
