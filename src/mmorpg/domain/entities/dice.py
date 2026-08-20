"""Кости: чем в Vellar считается урон.

Урон в игре — не процент от чего-то, а число в границах: «2d6» это два броска
шестигранной кости, от 2 до 12. Так его пишут в содержимом, так его и слышит
игрок — только словами: **«урон от 2 до 12»**, потому что «2d6» экранный диктор
читает как «два дэ шесть», и это не речь (`docs/accessibility.md`).

Границы важнее среднего. Одно и то же среднее даёт разную игру: «1d16» и «2d8»
бьют в среднем одинаково, но первым можно промахнуться по смыслу — размах и есть
то, чем булава отличается от меча.

Кости чистые: бросок принимает источник случайности аргументом, поэтому бой
повторяется по сиду до последнего очка урона.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

#: Насколько прибавка достаётся числу костей, а не их граням. Ровно половина
#: (0,5) оставила бы хвост распределения слишком длинным: на сороковом уровне
#: обычный бой одного класса из восьми затягивался вдвое против остальных.
#: Шесть десятых — костей чуть больше, граней чуть меньше, и невезение перестаёт
#: быть отдельной игрой, не переставая быть невезением.
SPLIT_EXPONENT = 0.6

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

    def scaled(self, factor: float) -> Dice:
        """Те же кости, но крупнее: и костей больше, и граней у каждой.

        Растёт и то и другое — костей чуть больше, граней чуть меньше
        (``SPLIT_EXPONENT``), а среднее выходит ровно во столько раз больше, во
        сколько просили. Если растить
        одни грани, «1d16» на трёхсотом уровне становится «1d1786», и удар
        превращается в подбрасывание монеты: выпавшая единица и выпавшая тысяча
        одинаково вероятны. Если растить одни кости, разброс схлопывается к
        середине и оружие перестаёт отличаться друг от друга.

        Так остаётся и то и другое: булава и на трёхсотом уровне бьёт вразброс
        шире меча, но обе бьют в пределах, по которым можно принимать решения.
        """
        split = max(0.0, factor) ** SPLIT_EXPONENT
        count = max(1, round(self.count * split))
        # Грани считаются от нужного среднего, а не от старых граней: у кости
        # среднее это (грани + 1) / 2, и умножать одни грани значило бы каждый
        # раз недобирать по единице с кости. На трёхсотом уровне «2d6» так теряло
        # четырнадцать процентов урона - ровно там, где это заметнее всего.
        wanted = self.count * (self.faces + 1) / 2 * max(0.0, factor)
        faces = max(1, round(2 * wanted / count - 1))
        return Dice(count=count, faces=faces, bonus=round(self.bonus * factor))

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
