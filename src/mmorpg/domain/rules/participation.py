"""Участие и конечный общий фонд награды. Без времени и ввода-вывода."""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.combat import Combatant
from mmorpg.domain.rules.party import split


def eligible(one: Combatant) -> bool:
    """Защита, поддержка, предмет и промах тоже являются участием."""
    return one.is_hero and one.live and not one.left and one.actions > 0


def reward_shares(amount: int, members: int) -> tuple[int, ...]:
    """Общий фонд: по 20 процентов за дополнительного действующего, максимум 80."""
    if members <= 0:
        return ()
    if members > 5:
        raise ValueError("At most five participants")
    pool = max(0, amount) * (100 + 20 * (members - 1)) // 100
    return split(pool, members)


def room_credit(
    previous: Sequence[tuple[int, int]],
    participants: Sequence[Combatant],
    layer: int,
) -> tuple[tuple[int, int], ...]:
    """Непрерывно пройденные комнаты; позднее вступление не возвращает пропуск."""
    counts = dict(previous)
    return tuple(
        (one.character_id, counts.get(one.character_id, 0) + 1)
        for one in participants
        if eligible(one) and counts.get(one.character_id, 0) == layer
    )
