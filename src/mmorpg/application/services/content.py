"""Содержимое, которое можно перечитать, не останавливая игру.

Раньше ``GameContent`` собирался один раз в композиционном корне и раздавался
хендлерам как значение. Так и осталось — с одной поправкой: значение теперь
берётся отсюда на каждом обновлении, а не запоминается при старте. Реестр держит
две сборки: ту, что прочитана из ``content/``, и ту, что игра показывает сейчас,
то есть с правками смотрителя поверх (``domain/rules/overlay.py``).

Исходная сборка не меняется никогда. Поэтому снять правку — это не «вернуть как
было по памяти», а собрать мир заново без одной записи, и результат один и тот же
независимо от того, сколько правок было до неё.
"""

from __future__ import annotations

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.overlay import OverlayRecord
from mmorpg.domain.ports.repositories import ContentOverlayRepository
from mmorpg.domain.rules import overlay as overlay_rules


class ContentRegistry:
    """Что игра показывает сейчас. Один объект на приложение."""

    __slots__ = ("_base", "_current", "_records")

    def __init__(self, base: GameContent) -> None:
        self._base = base
        self._current = base
        self._records: tuple[OverlayRecord, ...] = ()

    @property
    def base(self) -> GameContent:
        """Мир, как он записан в ``content/``. Только для сверки."""
        return self._base

    @property
    def current(self) -> GameContent:
        """Мир, каким его видит игрок прямо сейчас."""
        return self._current

    @property
    def records(self) -> tuple[OverlayRecord, ...]:
        return self._records

    def problems(self) -> tuple[tuple[OverlayRecord, tuple[str, ...]], ...]:
        """Правки, которые не работают, и почему.

        Смотритель должен узнать об этом от панели, а не от игрока, который зашёл
        в город и не нашёл там обещанного жителя.
        """
        found = (
            (record, overlay_rules.problems(self._current, record)) for record in self._records
        )
        return tuple((record, why) for record, why in found if why)

    async def reload(self, overlays: ContentOverlayRepository) -> int:
        """Перечитать правки и пересобрать мир. Возвращает, сколько правок легло."""
        self._records = await overlays.all()
        self._current = overlay_rules.apply(self._base, self._records)
        return len(self._records)
