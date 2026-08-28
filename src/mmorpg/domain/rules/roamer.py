"""Блуждающее подземелье: подземный ход, которого в сводке не было (ADR 0037).

Подземелье появляется само собой у одного из узлов локации. Заход в него устроен
тем же движком, что городской спуск (``domain/rules/dungeon.py``): слои,
развилки, логово, случайные условия - всё оттуда. Отличий три:

* его находят прямо в локации, а не в городе;
* оно либо **одиночное** (входит один), либо **на группу** (входит только
  отряд), и что именно - решает бросок при появлении;
* пока внутри кто-то есть, войти не может больше никто, а пройденное до логова
  подземелье осыпается и исчезает.

Всё здесь чистое: ни времени, ни ввода-вывода. Момент приходит как номер окна,
случайность - явным сидом (``Claude.md``, правило 1). Само появление и замок
живут в кэше локации (``domain/ports/repositories.LocationStateCache``).
"""

from __future__ import annotations

from mmorpg.domain.entities.location import GeneratedLocation, NodeKind, Roamer
from mmorpg.domain.procgen.seeds import derive, rng
from mmorpg.domain.rules.dungeon import Difficulty

#: Длина окна появления в секундах. Подземелье «уже здесь» с начала того окна, в
#: которое выпал бросок: четверть часа - достаточно, чтобы это было находкой, а
#: не рулеткой на каждом шаге.
ROAMER_WINDOW = 900

#: Сколько замок держится без продления. Полчаса: тот, кто вошёл и пропал,
#: освобождает вход для других, а само подземелье остаётся (ADR 0037).
ROAMER_HOLD_TTL = 1800

#: Шанс, что в пустой локации за одно окно объявится подземелье.
SPAWN_CHANCE = 0.35

#: Из выпавших подземелий - какая доля рассчитана на отряд.
GROUP_CHANCE = 0.5

#: Во сколько раз крепче враги и щедрее плата в подземелье на группу. Форма боя
#: та же, что у городского спуска, - меняется ставка
#: (``domain/entities.Enemy.stakes``).
GROUP_STAKES = 1.6

#: Какие сложности бросаются подземелью и с каким весом. «Разведки» тут нет
#: нарочно: подземелье само по себе уже риск, налегке в него не ходят.
_DIFFICULTY_WEIGHTS: tuple[tuple[Difficulty, int], ...] = (
    (Difficulty.DELVE, 3),
    (Difficulty.GRIM, 2),
)


def window_of(now: int) -> int:
    """К какому окну появления относится этот момент."""
    return now // ROAMER_WINDOW


def roll_spawn(
    location_seed_value: bytes,
    location: GeneratedLocation,
    *,
    epoch: int,
    window: int,
) -> Roamer | None:
    """Объявилось ли подземелье в этой локации к этому окну, и если да - какое.

    Детерминированно от места, поколения округи и номера окна: та же тройка
    всегда даёт тот же ответ. Подземелье встаёт у обычного узла - ни у входа, ни
    у выхода, ни у логова хозяина локации: чужой вход туда сбил бы навигацию.
    """
    source = rng(derive(location_seed_value, "roamer", epoch, window))
    if source.random() >= SPAWN_CHANCE:
        return None

    spots = [
        node.index
        for node in location.nodes
        if node.kind not in {NodeKind.ENTRANCE, NodeKind.EXIT, NodeKind.BOSS_BATTLE}
    ]
    if not spots:  # pragma: no cover - у локации всегда есть обычные узлы
        return None

    node = source.choice(spots)
    group = source.random() < GROUP_CHANCE
    pool = [kind for kind, _ in _DIFFICULTY_WEIGHTS]
    weights = [weight for _, weight in _DIFFICULTY_WEIGHTS]
    difficulty = source.choices(pool, weights=weights, k=1)[0]
    return Roamer(
        node=node,
        group=group,
        difficulty=difficulty.value,
        level=max(1, location.level_min),
        stamp=window,
    )


def run_seed(world_seed: str, city_id: str, slot: int, stamp: int, difficulty: Difficulty) -> bytes:
    """Сид всего захода в подземелье: из него растут и развилки, и условия.

    Аналог ``dungeon.run_seed``, но привязан к личности подземелья - месту, слоту
    и окну появления, - а не к городскому входу: два подземелья подряд у одного
    узла это два разных подземелья.
    """
    return derive(world_seed, "roamer-run", city_id, slot, stamp, difficulty.value)
