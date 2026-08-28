"""Строки и кнопки данжа: сложности и развилки.

Механику сложностей и комнат держит ``domain/rules/dungeon.py``; здесь только
то, как она звучит для игрока. Имена сложностей стоят рядом с ``RANK_NAMES``
из ``screens/combat.py`` по смыслу - это отображение фиксированного перечня,
а не правимый контент.
"""

from __future__ import annotations

from collections.abc import Sequence

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
    Difficulty.DELVE: "враги крепче, плата щедрее, одно случайное условие",
    Difficulty.GRIM: "враги вдвое опаснее, плата вдвое выше, два условия — беда и благо",
}

_ENTER_LABELS: dict[tuple[int, Difficulty], Label] = {
    (1, Difficulty.RECON): labels.DUNGEON_RECON,
    (1, Difficulty.DELVE): labels.DUNGEON_DELVE,
    (1, Difficulty.GRIM): labels.DUNGEON_GRIM,
    (2, Difficulty.RECON): labels.DUNGEON_DEEP_RECON,
    (2, Difficulty.DELVE): labels.DUNGEON_DEEP_DELVE,
    (2, Difficulty.GRIM): labels.DUNGEON_DEEP_GRIM,
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


def enter_label(tier: int, difficulty: Difficulty) -> Label:
    return _ENTER_LABELS[(tier, difficulty)]


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
