"""Строки и кнопки подземелий: список, сложности, развилки, прозвища.

Механику держат ``domain/rules/dungeon.py`` и ``domain/procgen/enemies.py``;
здесь только то, как она звучит для игрока. Имена сложностей и прозвищ стоят
рядом с ``RANK_NAMES`` из ``screens/combat.py`` по смыслу - это отображение
фиксированного перечня, а не правимый контент.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.content import EnemyAffix
from mmorpg.domain.rules.dungeon import Condition, Difficulty, RoomKind
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label

#: Порядок, в котором сложности предлагаются на экране.
DIFFICULTY_ORDER: tuple[Difficulty, ...] = (Difficulty.RECON, Difficulty.DELVE, Difficulty.GRIM)

DIFFICULTY_NAMES: dict[Difficulty, str] = {
    Difficulty.RECON: "разведка",
    Difficulty.DELVE: "тёмный ход",
    Difficulty.GRIM: "гиблый спуск",
}

DIFFICULTY_FLAVOUR: dict[Difficulty, str] = {
    Difficulty.RECON: "налегке, без условий",
    Difficulty.DELVE: (
        "враги крепче, плата щедрее, одно случайное условие; кое-кто из тварей с прозвищем"
    ),
    Difficulty.GRIM: (
        "самые сильные враги и самая высокая плата, два условия — беда и благо; "
        "прозвища у врагов встречаются чаще, по два сразу"
    ),
}

_DIFFICULTY_LABELS: dict[Difficulty, Label] = {
    Difficulty.RECON: labels.DIFFICULTY_RECON,
    Difficulty.DELVE: labels.DIFFICULTY_DELVE,
    Difficulty.GRIM: labels.DIFFICULTY_GRIM,
}

ROOM_LABELS: dict[RoomKind, Label] = {
    RoomKind.SKIRMISH: labels.ROOM_SKIRMISH,
    RoomKind.BEAST: labels.ROOM_BEAST,
    RoomKind.HOLLOW: labels.ROOM_HOLLOW,
    RoomKind.LAIR: labels.ROOM_LAIR,
    RoomKind.STAIRS: labels.ROOM_STAIRS,
}

ROOM_HINTS: dict[RoomKind, str] = {
    RoomKind.SKIRMISH: "обычная схватка",
    RoomKind.BEAST: "крупный зверь: тяжелее, но и добыча богаче",
    RoomKind.HOLLOW: "лёгкий бой и передышка: победа здесь латает раны",
    RoomKind.LAIR: "хозяин глубины и дно за ним: золото, опыт и находка",
    RoomKind.STAIRS: "выход: заход кончится с тем, что уже взято",
}

#: Что делает каждое прозвище, словами игрока (ADR 0042). Ключ - ``EnemyAffix.id``.
AFFIX_HINTS: dict[str, str] = {
    "ironhide": "крепкая броня и меньший получаемый урон",
    "thornback": "часть полученного урона возвращается бьющему",
    "bloodletter": "лечится от нанесённых вам ран",
    "venombite": "при попадании может отравить цель",
    "hoarfrost": "при попадании может замедлить цель",
    "sapping": "при попадании может наложить немощь и ослабить удар цели",
    "brutish": "крепче и бьёт тяжелее обычного",
    "nimble": "повышенная инициатива помогает раньше вступить в бой",
    "broodkeeper": "приводит с собой дополнительных противников",
    "stalking": "бьёт из темноты: не выбрать целью, пока сам не ударит, потом уходит из виду снова",
}


def difficulty_label(difficulty: Difficulty) -> Label:
    return _DIFFICULTY_LABELS[difficulty]


def room_label(kind: RoomKind) -> Label:
    return ROOM_LABELS[kind]


def fork_rows(options: Sequence[RoomKind]) -> tuple[tuple[Label, ...], ...]:
    """По кнопке на комнату: развилка - это ряд из двух-трёх дверей."""
    return tuple((room_label(kind),) for kind in options)


def fork_lines(options: Sequence[RoomKind]) -> tuple[str, ...]:
    """Что за каждой дверью, словами."""
    return tuple(f"{room_label(kind).text}: {ROOM_HINTS[kind]}." for kind in options)


def condition_lines(conditions: Sequence[Condition]) -> tuple[str, ...]:
    """Что несёт этот заход. Пусто - «разведка», условий нет."""
    return tuple(
        f"{'Благо' if one.good else 'Беда'} «{one.name}»: {one.text}" for one in conditions
    )


def affix_line(affix: EnemyAffix) -> str:
    """Строка о прозвище врага: прилагательное и что оно даёт."""
    hint = AFFIX_HINTS.get(affix.id, "дерётся не как обычная тварь")
    return f"{affix.adjective}: {hint}."
