"""Ремёсла: сбор сырья в дороге и работа из него руками.

Ремесло - это работа, а не то, кто ты такой: приключенец выбирает класс один
раз, а ремёсла может выучить все (``Narrative.md``, раздел 2). Описания
приходят из ``content/crafts.toml``; на персонаже лежит только уже сделанная
работа, потому что ранг зарабатывают, а не выдают.

Ничего производного не хранится. Ранг пересчитывается из опыта каждый раз,
когда его показывают, ровно как уровень персонажа.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.stats import StatCode


class CraftKind(StrEnum):
    """Сбор приносит сырьё, работа руками превращает его в вещи."""

    GATHERING = "gathering"
    MAKING = "making"


@dataclass(frozen=True, slots=True)
class CraftYield:
    """Одно сырьё, которое собирающее ремесло приносит начиная с уровня ``level``.

    ``biomes`` — это где оно лежит: биомы локаций вокруг города
    (``content/world.toml``). Сбор когда-то не смотрел на карту вовсе: одна кнопка
    давала одно и то же в каждом городе дороги, и пятнадцать городов держали два
    одинаковых сырья. Пустой список по-прежнему значит «везде», потому что у
    ремесла должно быть то, чем игрок может заняться всегда.
    """

    item_id: str
    level: int
    biomes: tuple[str, ...] = ()

    def found_in(self, biomes: frozenset[str]) -> bool:
        """Лежит ли это сырьё в земле вокруг этих мест."""
        return not self.biomes or bool(biomes.intersection(self.biomes))


@dataclass(frozen=True, slots=True)
class Craft:
    id: str
    name: str
    kind: CraftKind
    stat: StatCode
    description: str
    yields: tuple[CraftYield, ...] = ()

    @property
    def gathers(self) -> bool:
        return self.kind is CraftKind.GATHERING


@dataclass(frozen=True, slots=True)
class RecipeInput:
    item_id: str
    count: int


@dataclass(frozen=True, slots=True)
class Recipe:
    """Одна вещь, которую ремесло умеет сделать."""

    id: str
    craft_id: str
    rank: int
    inputs: tuple[RecipeInput, ...]
    output_id: str
    output_count: int
    experience: int


@dataclass(frozen=True, slots=True)
class QualityTier:
    """Насколько хорошо вышла партия.

    Сумка держит идентификаторы вещей и их число, а не образцы, поэтому качество
    платит тем, что выходит из работы: ``extra`` вещей сверху и ``refund_percent``
    сырья, оставшегося неистраченным.
    """

    id: str
    name: str
    extra: int
    refund_percent: int


@dataclass(frozen=True, slots=True)
class CraftRules:
    """Опорные числа ремесла, общие для содержимого и правил."""

    max_rank: int
    experience_per_rank: int
    rank_names: tuple[str, ...]
    gather_base: int
    gather_per_rank: int
    gather_experience: int
    qualities: tuple[QualityTier, ...]
    good_chance_base: float
    good_chance_per_rank: float
    fine_chance_base: float
    fine_chance_per_rank: float

    def quality(self, quality_id: str) -> QualityTier:
        for tier in self.qualities:
            if tier.id == quality_id:
                return tier
        msg = f"unknown quality {quality_id}"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class CraftProgress:
    """Что один персонаж сделал в одном ремесле.

    ``gathered_at`` - unix-время последнего сбора. Откат личный и короткий: дорога
    пополняется для этого персонажа по его собственным часам, а не по общей страже,
    которую всем приходилось пережидать вместе.
    """

    experience: int = 0
    gathered_at: int = 0


@dataclass(frozen=True, slots=True)
class CraftLog:
    """Каждое ремесло, которого персонаж коснулся. Нетронутых ремёсел просто нет."""

    entries: Mapping[str, CraftProgress] = field(default_factory=dict)

    def progress(self, craft_id: str) -> CraftProgress:
        return self.entries.get(craft_id, CraftProgress())

    def with_experience(self, craft_id: str, gained: int) -> CraftLog:
        current = self.progress(craft_id)
        updated = replace(current, experience=current.experience + max(0, gained))
        return CraftLog(MappingProxyType({**self.entries, craft_id: updated}))

    def with_gathered_at(self, craft_id: str, moment: int) -> CraftLog:
        current = self.progress(craft_id)
        updated = replace(current, gathered_at=moment)
        return CraftLog(MappingProxyType({**self.entries, craft_id: updated}))
