"""Что стоит в узле локации и когда оно появляется снова.

Карта локации перекладывается поколениями по выработке
(``domain/procgen/location.py``, ADR 0035). Здесь - правила наполнения узлов:
именно суммарная выработка узлов и двигает поколение.

Узел держит **волну**: несколько стычек в засаде, несколько горстей руды в жиле,
пару свёртков в тайнике. Каждое действие забирает одну единицу, а не весь узел
разом, — поэтому флага «кто-то здесь прошёл» больше нет: есть места волны и то,
какие из них опустели. Место, а не очередь: стая с третьего места остаётся
третьей и после того, как пала первая (ADR 0065). Когда волна кончилась, узел
пуст, и через :data:`RESPAWN_SECONDS` на его месте встаёт следующая волна —
другие противники, другие находки.

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
#: остовное дерево троп перекладывается, места встают в другом порядке, короткие
#: тропы ложатся заново, у мест другие имена (``domain/procgen/location.py``,
#: ADR 0035). Считается по выработке, а не по часам: пока в округе есть что
#: брать, она стоит как стояла, а выбитую заселяет заново. Число невелико
#: нарочно - за одну плотную вылазку округа успевает переложиться хотя бы раз,
#: иначе перекладки не видит никто. Локацию, которую сутками никто не трогал, кэш
#: забывает целиком, и её поколение откатывается к нулевому само.
REGROWTH_WAVES = 12

#: Сколько единиц держит волна, по разновидности узла: от и до включительно.
#: Двери не держат ничего. Сильный одиночка и хозяин логова стоят по одному — на
#: узле-эпике теперь ровно один бой, а не два подряд (ADR 0034).
WAVE_SIZE: dict[NodeKind, tuple[int, int]] = {
    NodeKind.ENTRANCE: (0, 0),
    NodeKind.EXIT: (0, 0),
    NodeKind.BATTLE: (2, 4),
    NodeKind.ELITE_BATTLE: (1, 1),
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
        return NodeState(wave=state.wave + 1, taken_slots=0, emptied_at=0)
    return state


def free_slots(state: NodeState, size: int) -> tuple[int, ...]:
    """Места волны, на которых ещё кто-то стоит, по порядку (ADR 0065).

    Место — это и есть стая: её собирают из сида места, а не из счётчика, и
    убитая вторая не превращает третью во вторую.
    """
    if state.emptied_at or size <= 0:
        return ()
    return tuple(slot for slot in range(size) if state.holds(slot))


def remaining(state: NodeState, size: int) -> int:
    """Сколько единиц в узле ещё не забрали."""
    return len(free_slots(state, size))


def taken_one(state: NodeState, size: int, now: int, slot: int = -1) -> NodeState:
    """Забрать одну единицу. Последняя ставит отметку времени: узел опустел.

    ``slot`` — какое именно место волны освободилось; ``-1`` значит «первое
    занятое», и так берут те узлы, где выбирать не из чего: жила, тайник,
    святилище. Место, с которого уже взяли, второй раз не считается: два боя за
    одну стаю не вычистят узел вдвое быстрее.
    """
    if state.emptied_at or size <= 0:
        return state
    standing = free_slots(state, size)
    if not standing:
        return state
    picked = standing[0] if slot < 0 else slot
    if picked not in standing:
        return state
    marks = state.taken_slots | 1 << picked
    if len(standing) <= 1:
        return NodeState(wave=state.wave, taken_slots=marks, emptied_at=max(1, now))
    return NodeState(wave=state.wave, taken_slots=marks, emptied_at=0)


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
    #: Места волны, на которых ещё кто-то стоит (ADR 0065). Пусто у пустого узла.
    free: tuple[int, ...] = ()

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
    standing = free_slots(node, size)
    return Standing(
        index=index,
        size=size,
        left=len(standing),
        taken=node.taken,
        wave=node.wave,
        refill_in=seconds_until_refill(node, now),
        free=standing,
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
