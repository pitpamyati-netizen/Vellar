"""Правки смотрителя: то, что положено поверх содержимого из TOML.

Файлы в ``content/`` остаются источником мира. Смотритель ничего в них не пишет:
он кладёт сверху записи, и игра читает содержимое как «TOML плюс правки». Отсюда
два свойства, ради которых это и сделано: правку видно сразу, без перезапуска, и
её всегда можно снять, потому что исходная строка никуда не девалась.

Одна запись — одна сущность. Поля хранятся строками: запись переживает и код, и
содержимое, а строка — единственный вид, который одинаково читается через год.
Разбирает строки ``domain/rules/overlay.py``, и он же отказывается применять
запись, которая перестала иметь смысл.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class OverlayKind(StrEnum):
    """Что именно правит запись.

    Список закрыт: каждая разновидность знает свои поля (``rules/overlay.py``) и
    своё место в игре, поэтому «ещё одна сущность» — это работа, а не строка.
    """

    NPC = "npc"
    QUEST = "quest"
    LOCATION = "location"
    ENEMY = "enemy"
    CITY = "city"


#: С чего начинаются идентификаторы, выданные смотрителем. Отдельный префикс
#: нужен, чтобы правка никогда не спорила за имя с ключом из TOML.
KEEPER_PREFIX = "keeper_"


@dataclass(frozen=True, slots=True)
class OverlayRecord:
    """Одна правка: чему, что и когда.

    ``removed`` — надгробие: сущность из TOML не стирается, а перестаёт
    показываться, и запись можно снять, вернув всё как было.
    """

    kind: OverlayKind
    entity_id: str
    fields: Mapping[str, str] = field(default_factory=dict)
    removed: bool = False
    author_id: int = 0
    updated_at: int = 0

    @property
    def is_keepers(self) -> bool:
        """Сущность, которой в TOML не было: её правка — она сама."""
        return self.entity_id.startswith(KEEPER_PREFIX)

    def value(self, key: str, default: str = "") -> str:
        return self.fields.get(key, default)

    def number(self, key: str, default: int = 0) -> int:
        """Число поля. Нечисло — это ``default``: проверку делает валидатор."""
        raw = self.fields.get(key, "").strip()
        try:
            return int(raw)
        except ValueError:
            return default

    def rate(self, key: str, default: float = 1.0) -> float:
        """Доля вроде «1,2». Запятая принимается: её и набирают."""
        raw = self.fields.get(key, "").strip().replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            return default

    def flag(self, key: str) -> bool:
        return self.fields.get(key, "").strip().casefold() in {"да", "yes", "true", "1"}

    def listed(self, key: str) -> tuple[str, ...]:
        """Поле-перечисление: «луга, лес» — два значения, пустое — ни одного."""
        raw = self.fields.get(key, "")
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    def with_field(self, key: str, value: str) -> OverlayRecord:
        return replace(self, fields=MappingProxyType({**self.fields, key: value}))

    def without_field(self, key: str) -> OverlayRecord:
        remaining = {name: held for name, held in self.fields.items() if name != key}
        return replace(self, fields=MappingProxyType(remaining))
