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
        "враги вдвое опаснее, плата вдвое выше, два условия — беда и благо; "
        "прозвища у тварей чаще и по два"
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
    "ironhide": "бьёт по нему слабее: шкура держит удар",
    "thornback": "часть полученного урона возвращается бьющему",
    "bloodletter": "лечится от нанесённых вам ран",
    "venombite": "по попаданию травит ядом",
    "hoarfrost": "по попаданию студит: цель ходит реже",
    "sapping": "по попаданию вешает немощь: удар цели слабее",
    "brutish": "крепче и бьёт тяжелее обычного",
    "nimble": "успевает раньше и в ход, и в ответ",
    "broodkeeper": "приводит с собой лишние тела",
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
