"""Кости: чем в Vellar считается урон.

Урон - число в границах: «2d6» это два броска шестигранной кости, от 2 до 12.
Игроку это говорят словами - «урон от 2 до 12», - потому что «2d6» экранный
диктор читает как «два дэ шесть» (`docs/accessibility.md`).

Границы важнее среднего: «1d16» и «2d8» бьют в среднем одинаково, а играются
по-разному. Но размах - характер рода оружия, а не лотерея, и объявлен числом
(``spread``): во сколько раз верхняя граница выше нижней. Меч бьёт ровно (1,2),
булава вразброс (1,5), а ``MAX_SPREAD`` - потолок всей игры. Прибавка руками не
пишется: сколько её нужно, чтобы кости легли ровно в объявленные границы, знает
:meth:`Dice.within` (ADR 0017, 0020).

Кости чистые: источник случайности приходит аргументом, поэтому бой повторяется
по сиду до последнего очка урона.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

#: Насколько рост вещи достаётся числу костей, а не их граням. Границы держит
#: ``spread``, а число костей решает только форму распределения внутри них.
SPLIT_EXPONENT = 0.6

#: Потолок размаха: верхняя граница удара нигде не выше полутора нижних - ни у
#: одного рода оружия, ни на одной ступени, ни на одном уровне (ADR 0020).
MAX_SPREAD = 1.5

#: И пол: кости, у которых верх равен низу, — это уже не кости.
MIN_SPREAD = 1.05

#: `2d6`, `1d14`, `2d8+3`, `1d4-1`. Пробелы допускаются, регистр — любой.
_PATTERN = re.compile(r"^\s*(\d+)\s*[dд]\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Dice:
    """Сколько костей, сколько у них граней и что прибавляется к броску."""

    count: int
    faces: int
    bonus: int = 0

    @classmethod
    def parse(cls, text: str) -> Dice:
        """Прочитать `2d6+3`. Непонятное — отказ, а не молчаливый ноль."""
        match = _PATTERN.match(text)
        if match is None:
            msg = f"not a dice expression: {text!r}"
            raise ValueError(msg)
        count, faces, sign, bonus = match.groups()
        amount = int(bonus or 0)
        return cls(
            count=int(count),
            faces=int(faces),
            bonus=-amount if sign == "-" else amount,
        )

    def __post_init__(self) -> None:
        if self.count < 1 or self.faces < 1:
            msg = f"dice must have at least one die of at least one face: {self!s}"
            raise ValueError(msg)

    @property
    def low(self) -> int:
        return max(0, self.count + self.bonus)

    @property
    def high(self) -> int:
        return max(0, self.count * self.faces + self.bonus)

    @property
    def average(self) -> float:
        return self.count * (self.faces + 1) / 2 + self.bonus

    @property
    def spread(self) -> float:
        """Во сколько раз верхняя граница выше нижней. Это и есть размах."""
        return self.high / self.low if self.low > 0 else float(self.high)

    @classmethod
    def within(cls, *, average: float, spread: float, count: int = 1) -> Dice:
        """Кости с этим средним и этим размахом.

        У костей среднее - ровно середина между границами, поэтому границы считаются
        из среднего и размаха: ``low = 2A / (1 + s)``, ``high = 2As / (1 + s)``.
        Разброс отдаётся костям, остаток - прибавке, которую не бросают вовсе.

        ``count`` решает только форму распределения внутри границ, но не сами
        границы. Размах зажимается в ``MIN_SPREAD..MAX_SPREAD`` здесь, а не у
        вызывающего: потолок один на всю игру, и обойти его содержимым нельзя.
        """
        span = min(MAX_SPREAD, max(MIN_SPREAD, float(spread)))
        wanted = max(1.0, float(average))
        count = max(1, int(count))

        # У костей среднее — ровно середина между границами, поэтому «в s раз
        # выше» — это половина разброса не больше, чем ``A (s - 1) / (s + 1)``.
        half = max(0.0, wanted * (span - 1.0) / (span + 1.0))
        tight = max(2, int(2.0 * half / count) + 1)

        # Прибавка добирает среднее до нужного. Целые числа на первых ступенях
        # грубы, поэтому перебираются четыре ближайших набора: грань туда-сюда и
        # прибавка туда-сюда. Выигрывает тот, чьё среднее ближе к нужному, а при
        # равном среднем - чей размах ближе к объявленному. Набор, вышедший за
        # потолок игры, в переборе не участвует.
        best: Dice | None = None
        for faces in (tight, tight + 1):
            exact = wanted - count * (faces + 1) / 2
            floor_bonus = int(exact // 1)
            for bonus in (floor_bonus, floor_bonus + 1):
                if bonus < 1 - count:
                    continue
                candidate = cls(count=count, faces=faces, bonus=bonus)
                if candidate.low < 1 or candidate.spread > MAX_SPREAD:
                    continue
                if best is None or (
                    abs(candidate.average - wanted),
                    abs(candidate.spread - span),
                ) < (abs(best.average - wanted), abs(best.spread - span)):
                    best = candidate
        if best is not None:
            return best
        # Ни одна прибавка не влезла под потолок - значит, её просто мало:
        # прибавка поднимает обе границы, и размах от этого только сужается.
        bonus = max(1 - count, int(wanted - count * (tight + 1) / 2)) + 1
        while True:
            candidate = cls(count=count, faces=tight, bonus=bonus)
            if candidate.low >= 1 and candidate.spread <= MAX_SPREAD:
                return candidate
            bonus += 1

    def scaled(self, factor: float, *, spread: float = MAX_SPREAD) -> Dice:
        """Те же кости, но крупнее: среднее во столько раз больше, размах тот же.

        Растёт одно среднее, а границы каждый раз считаются заново из него и из
        размаха рода, поэтому «в полтора раза» значит полтора на всей полосе
        уровней. Множить порознь число костей, грани и прибавку нельзя: у кости
        среднее растёт быстрее нижней границы, и размах от этого плывёт.
        """
        multiplier = max(0.0, factor)
        count = max(1, round(self.count * multiplier**SPLIT_EXPONENT))
        return Dice.within(average=self.average * multiplier, spread=spread, count=count)

    def roll(self, source: random.Random) -> int:
        """Один бросок. Ниже нуля урона не бывает."""
        total = sum(source.randint(1, self.faces) for _ in range(self.count)) + self.bonus
        return max(0, total)

    def spoken(self) -> str:
        """То, что слышит игрок: границы словами, без «дэ» и без псевдографики."""
        return f"от {self.low} до {self.high}"

    def __str__(self) -> str:
        tail = f"{self.bonus:+d}" if self.bonus else ""
        return f"{self.count}d{self.faces}{tail}"
