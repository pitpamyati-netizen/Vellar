"""Основные характеристики персонажа.

Семь основных характеристик кормят собой каждое производное число. Модуль -
чистые данные: он знает, как характеристики складываются, а не откуда они
берутся.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum


class StatCode(StrEnum):
    """Семь основных характеристик."""

    STR = "STR"  # сила: физический урон, переносимый вес
    AGI = "AGI"  # ловкость: точность, уклонение, инициатива
    END = "END"  # выносливость: здоровье, броня, сопротивление
    INT = "INT"  # интеллект: магический урон, мана
    WIS = "WIS"  # мудрость: лечение, сопротивление магии, восстановление ресурса
    CHA = "CHA"  # харизма: цены, плата по заданиям, влияние
    LCK = "LCK"  # удача: шанс крита, редкость добычи, случайные эффекты


@dataclass(frozen=True, slots=True)
class StatBlock:
    """Неизменный набор характеристик. Сложение идёт покомпонентно."""

    STR: int = 0
    AGI: int = 0
    END: int = 0
    INT: int = 0
    WIS: int = 0
    CHA: int = 0
    LCK: int = 0

    def __add__(self, other: StatBlock) -> StatBlock:
        return StatBlock(
            STR=self.STR + other.STR,
            AGI=self.AGI + other.AGI,
            END=self.END + other.END,
            INT=self.INT + other.INT,
            WIS=self.WIS + other.WIS,
            CHA=self.CHA + other.CHA,
            LCK=self.LCK + other.LCK,
        )

    def __getitem__(self, code: StatCode) -> int:
        value: int = getattr(self, code.value)
        return value

    def __iter__(self) -> Iterator[tuple[StatCode, int]]:
        for field in fields(self):
            yield StatCode(field.name), getattr(self, field.name)

    @property
    def total(self) -> int:
        """Чистая сумма всех значений, вместе со штрафами."""
        return sum(value for _, value in self)

    @property
    def positive_total(self) -> int:
        return sum(value for _, value in self if value > 0)

    @property
    def penalty_total(self) -> int:
        """Сумма отрицательных значений по модулю."""
        return -sum(value for _, value in self if value < 0)

    def with_change(self, code: StatCode, delta: int) -> StatBlock:
        """Вернуть копию, в которой одна характеристика сдвинута на ``delta``."""
        values = {name.value: value for name, value in self}
        values[code.value] += delta
        return StatBlock(**values)

    def as_mapping(self) -> dict[StatCode, int]:
        return dict(self)

    @classmethod
    def uniform(cls, value: int) -> StatBlock:
        """Набор с одним и тем же значением в каждой характеристике."""
        return cls(STR=value, AGI=value, END=value, INT=value, WIS=value, CHA=value, LCK=value)

    @classmethod
    def from_mapping(cls, values: Mapping[str, int]) -> StatBlock:
        """Собрать из словаря ``{"STR": 2, ...}``. Незнакомый ключ - KeyError."""
        unknown = set(values) - {code.value for code in StatCode}
        if unknown:
            msg = f"unknown stat codes: {sorted(unknown)}"
            raise KeyError(msg)
        return cls(**{key: int(value) for key, value in values.items()})
