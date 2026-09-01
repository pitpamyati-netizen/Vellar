"""Состояние округи: как она звучит по тому, сколько по ней ходили (ADR 0055).

Локация в Vellar — не декорация: узлы держат волны, волны считает выработка, а
выработка двигает поколение округи (``domain/rules/nodes.py``, ADR 0035). Всё это
уже посчитано и лежит в ``LocationState``. Здесь — одно слово поверх: нетронута,
хожена, выработана или встревожена. Слово читает игрок на экране локации, а по
нему правятся цели сводки, шанс прозвищ врагов и цены ближайшего города
(ADR 0055).

Чистая функция от состояния — ни времени, ни ввода-вывода: и момент, и волны уже
внутри ``LocationState``.
"""

from __future__ import annotations

from enum import StrEnum

from mmorpg.domain.entities.location import LocationState
from mmorpg.domain.rules.nodes import REGROWTH_WAVES, location_epoch


class LocationMood(StrEnum):
    """Насколько густо по округе ходили. Порядок — от свежей к вычищенной."""

    UNTOUCHED = "untouched"  # нетронута: волн почти не снято
    WORKED = "worked"  # хожена: округу работают, но поколение ещё то же
    DEPLETED = "depleted"  # выработана: сняли столько, что округа переложилась
    RESTLESS = "restless"  # встревожена: в локации осел блуждающий ход


#: Сколько снятых единиц волн уже считается «по округе ходят». Половина порога
#: поколения (``REGROWTH_WAVES``) нарочно: «хожена» — это состояние до перекладки,
#: а не вместо неё.
WORKED_AT = REGROWTH_WAVES // 2


def worked_units(state: LocationState) -> int:
    """Сколько всего снято с узлов локации: прошедшие волны плюс взятое из текущих."""
    return sum(node.wave + node.taken for node in state.nodes.values())


def mood_of(state: LocationState) -> LocationMood:
    """Как звучит округа сейчас.

    Блуждающий ход перебивает всё — это самый громкий след. Дальше: переложившаяся
    округа «выработана», крепко хоженная — «хожена», всё прочее — «нетронута».
    """
    if state.roamer is not None:
        return LocationMood.RESTLESS
    if location_epoch(state) >= 1:
        return LocationMood.DEPLETED
    if worked_units(state) >= WORKED_AT:
        return LocationMood.WORKED
    return LocationMood.UNTOUCHED
