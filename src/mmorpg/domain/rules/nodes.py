"""Что стоит в узле локации и когда оно появляется снова.

Локация — место, а не бросок костей: её карта не меняется никогда
(``domain/procgen/location.py``). Меняется наполнение узлов, и вот его правила.

Узел держит **волну**: несколько стычек в засаде, несколько горстей руды в жиле,
пару свёртков в тайнике. Каждое действие забирает одну единицу, а не весь узел
разом, — поэтому флага «кто-то здесь прошёл» больше нет: есть счёт того, сколько
осталось. Когда волна кончилась, узел пуст, и через :data:`RESPAWN_SECONDS`
на его месте встаёт следующая волна — другие противники, другие находки.

Всё здесь — чистая арифметика: момент приходит аргументом, случайность —
явным сидом (``Claude.md``, правило 1).
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.location import (
    GeneratedLocation,
    LocationState,
    NodeKind,
    NodeState,
)
from mmorpg.domain.procgen.seeds import rng, wave_seed

#: Через сколько секунд после того, как узел вычистили, встаёт следующая волна.
#: Три минуты: достаточно, чтобы обойти соседние узлы и вернуться, и мало,
#: чтобы локация не стояла пустой для того, кто вошёл вторым.
RESPAWN_SECONDS = 180

#: Сколько волн, снятых со всех узлов локации вместе, сменяет поколение округи:
#: конкретные виды узлов переставляются внутри их категорий, а короткие тропы
#: ложатся заново (``domain/procgen/location.py``, ADR 0032). Считается по
#: выработке, а не по часам: пока в округе есть что брать, она стоит как стояла,
#: а выбитую заселяет заново. Локацию, которую сутками никто не трогал, кэш
#: забывает целиком, и её поколение откатывается к нулевому само.
REGROWTH_WAVES = 40

#: Сколько единиц держит волна, по разновидности узла: от и до включительно.
#: Двери не держат ничего, хозяин логова стоит один — на то он и хозяин.
WAVE_SIZE: dict[NodeKind, tuple[int, int]] = {
    NodeKind.ENTRANCE: (0, 0),
    NodeKind.EXIT: (0, 0),
    NodeKind.BATTLE: (2, 4),
    NodeKind.ELITE_BATTLE: (1, 2),
    NodeKind.BOSS_BATTLE: (1, 1),
    NodeKind.GATHER: (3, 5),
    NodeKind.CACHE: (1, 3),
    NodeKind.EVENT: (1, 2),
    NodeKind.SHRINE: (1, 2),
}


def wave_size(location_seed_value: bytes, index: int, kind: NodeKind, wave: int) -> int:
    """Сколько единиц встало в этом узле в этой волне."""
    low, high = WAVE_SIZE[kind]
    if high <= low:
        return low
    return rng(wave_seed(location_seed_value, index, wave)).randint(low, high)


def location_epoch(state: LocationState) -> int:
    """Какое поколение округи стоит сейчас. По выработке, не по часам.

    Сумма волн, снятых со всех узлов, поделённая на :data:`REGROWTH_WAVES`.
    Растёт, пока локацию работают, и оседает сама, когда кэш забывает
    нетронутую локацию (``Claude.md``, правило 8: у каждого ключа есть срок).
    """
    return sum(node.wave for node in state.nodes.values()) // REGROWTH_WAVES


def refreshed(state: NodeState, now: int) -> NodeState:
    """Узел, каким он стал к моменту ``now``.

    Пустой узел, простоявший срок, отдаёт следующую волну. Всё остальное
    возвращается как есть — функция ничего не решает за игрока.
    """
    if state.emptied_at and now - state.emptied_at >= RESPAWN_SECONDS:
        return NodeState(wave=state.wave + 1, taken=0, emptied_at=0)
    return state


def remaining(state: NodeState, size: int) -> int:
    """Сколько единиц в узле ещё не забрали."""
    if state.emptied_at:
        return 0
    return max(0, size - state.taken)


def taken_one(state: NodeState, size: int, now: int) -> NodeState:
    """Забрать одну единицу. Последняя ставит отметку времени: узел опустел."""
    if state.emptied_at or size <= 0:
        return state
    count = state.taken + 1
    if count >= size:
        return NodeState(wave=state.wave, taken=size, emptied_at=max(1, now))
    return NodeState(wave=state.wave, taken=count, emptied_at=0)


def seconds_until_refill(state: NodeState, now: int) -> int:
    """Сколько секунд ещё стоять пустым. Ноль — узел не пуст или уже готов."""
    if not state.emptied_at:
        return 0
    return max(0, state.emptied_at + RESPAWN_SECONDS - now)


@dataclass(frozen=True, slots=True)
class Standing:
    """Сколько всего стоит в узле, сколько осталось и когда встанет новое."""

    index: int
    size: int
    left: int
    taken: int
    wave: int
    refill_in: int

    @property
    def empty(self) -> bool:
        return self.left <= 0


def standing_at(
    location_seed_value: bytes,
    location: GeneratedLocation,
    state: LocationState,
    index: int,
    now: int,
) -> Standing:
    """Состояние одного узла: волна, счёт и срок до следующей волны."""
    node = refreshed(state.node(index), now)
    size = wave_size(location_seed_value, index, location.node(index).kind, node.wave)
    return Standing(
        index=index,
        size=size,
        left=remaining(node, size),
        taken=node.taken,
        wave=node.wave,
        refill_in=seconds_until_refill(node, now),
    )


def standing(
    location_seed_value: bytes,
    location: GeneratedLocation,
    state: LocationState,
    now: int,
) -> dict[int, Standing]:
    """То же самое для всей локации разом: экран читает её целиком."""
    return {
        node.index: standing_at(location_seed_value, location, state, node.index, now)
        for node in location.nodes
    }
