"""Состояние округи: как она звучит по тому, сколько по ней ходили (ADR 0055).

Локация в Vellar — не декорация: узлы держат волны, волны считает выработка, а
выработка меняет состояние округи, сохраняя её карту (ADR 0108). Всё это
уже посчитано и лежит в ``LocationState``. Здесь — одно слово поверх: нетронута,
хожена, выработана или встревожена. Слово читает игрок на экране локации, а по
нему правятся цели сводки, шанс прозвищ врагов и цены ближайшего города
(ADR 0055).

Чистая функция от состояния — ни времени, ни ввода-вывода: и момент, и волны уже
внутри ``LocationState``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from mmorpg.domain.entities.location import LocationState
from mmorpg.domain.rules.nodes import REGROWTH_WAVES


class LocationMood(StrEnum):
    """Насколько густо по округе ходили. Порядок — от свежей к вычищенной."""

    UNTOUCHED = "untouched"  # нетронута: волн почти не снято
    WORKED = "worked"  # хожена: округу работают
    DEPLETED = "depleted"  # выработана: сняли не меньше двенадцати волн
    RESTLESS = "restless"  # встревожена: в локации осел блуждающий ход


#: Сколько снятых единиц волн уже считается «по округе ходят». Половина порога
#: прежней перестройки (``REGROWTH_WAVES``); карта теперь остаётся на месте.
WORKED_AT = REGROWTH_WAVES // 2


def worked_units(state: LocationState) -> int:
    """Сколько всего снято с узлов локации: прошедшие волны плюс взятое из текущих."""
    return sum(node.wave + node.taken for node in state.nodes.values())


def mood_of(state: LocationState) -> LocationMood:
    """Как звучит округа сейчас.

    Блуждающий ход перебивает всё — это самый громкий след. Дальше: выработанная
    округа «выработана», крепко хоженная — «хожена», всё прочее — «нетронута».
    """
    if state.roamer is not None:
        return LocationMood.RESTLESS
    if sum(node.wave for node in state.nodes.values()) >= REGROWTH_WAVES:
        return LocationMood.DEPLETED
    if worked_units(state) >= WORKED_AT:
        return LocationMood.WORKED
    return LocationMood.UNTOUCHED


#: Вклад каждого состояния в «нужду» ближайшего города (ADR 0055). Выбитая округа
#: кормит город хуже всего; встревоженная — почти так же плохо, но по другой
#: причине (по дорогам ходит подземная дрянь).
_STRAIN: dict[LocationMood, float] = {
    LocationMood.UNTOUCHED: 0.0,
    LocationMood.WORKED: 0.15,
    LocationMood.DEPLETED: 0.5,
    LocationMood.RESTLESS: 0.4,
}


def city_strain(moods: Iterable[LocationMood]) -> float:
    """Насколько туго ближайшему городу по округе вокруг: 0 (всё тихо) … 1.

    Средняя «нужда» по локациям города. Лавка от неё дорожает и сужается
    (``domain/rules/economy.py``), а больше пока ничего.
    """
    values = list(moods)
    if not values:
        return 0.0
    return min(1.0, sum(_STRAIN.get(one, 0.0) for one in values) / len(values))
