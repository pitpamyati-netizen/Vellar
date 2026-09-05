"""Чем бьют: род урона.

Род урона - один список, и в нём две половины. **Физическая** отвечает на
вопрос, чем ударили: колющим, рубящим, дробящим или разрывающим, - и род оружия
называет это сам (``items.toml``, ``damage_type``). **Магическая** отвечает на
вопрос, чем ударили вместо железа: огнём, холодом, ядом, чарами и так далее.
Физических родов четыре, магических одиннадцать, и третьей половины не бывает -
ни «стихийного» рода, ни «хаотического» (ADR 0022).

У каждого рода своё сопротивление, и сверх него считается сопротивление
половине: ``resist_physical_percent`` смягчает все четыре физических рода,
``resist_magic_percent`` - все магические. Складываются они, а не выбираются:
латы держат удар вообще, а стёганка под ними - именно колющий.
"""

from __future__ import annotations

from enum import StrEnum


class DamageType(StrEnum):
    """Род урона. Первые четыре - физические, остальные - магические."""

    PIERCING = "piercing"
    SLASHING = "slashing"
    BLUDGEONING = "bludgeoning"
    RENDING = "rending"

    ARCANE = "arcane"
    FIRE = "fire"
    COLD = "cold"
    POISON = "poison"
    ACID = "acid"
    AIR = "air"
    HOLY = "holy"
    LIGHT = "light"
    MENTAL = "mental"
    NATURE = "nature"
    NEGATIVE = "negative"

    @property
    def is_physical(self) -> bool:
        return self in PHYSICAL_TYPES

    @property
    def is_magic(self) -> bool:
        return not self.is_physical

    @property
    def resist_key(self) -> str:
        """Ключ сопротивления именно этому роду."""
        return f"resist_{self.value}_percent"

    @property
    def half_resist_key(self) -> str:
        """Ключ сопротивления половине, к которой род относится."""
        return "resist_physical_percent" if self.is_physical else "resist_magic_percent"


PHYSICAL_TYPES: frozenset[DamageType] = frozenset(
    {
        DamageType.PIERCING,
        DamageType.SLASHING,
        DamageType.BLUDGEONING,
        DamageType.RENDING,
    }
)

MAGIC_TYPES: frozenset[DamageType] = frozenset(DamageType) - PHYSICAL_TYPES

#: Русские названия. Домен держит род машинным, слова говорит экран, но словарь
#: один: два разных перевода одного рода игрок услышал бы как два разных урона.
DAMAGE_TYPE_NAMES: dict[DamageType, str] = {
    DamageType.PIERCING: "колющий",
    DamageType.SLASHING: "рубящий",
    DamageType.BLUDGEONING: "дробящий",
    DamageType.RENDING: "разрывающий",
    DamageType.ARCANE: "чародейский",
    DamageType.FIRE: "огненный",
    DamageType.COLD: "ледяной",
    DamageType.POISON: "ядовитый",
    DamageType.ACID: "кислотный",
    DamageType.AIR: "воздушный",
    DamageType.HOLY: "священный",
    DamageType.LIGHT: "лучезарный",
    DamageType.MENTAL: "дурманящий",
    DamageType.NATURE: "природный",
    DamageType.NEGATIVE: "оскверняющий",
}

#: Все ключи сопротивлений: по одному на род плюс два на половины.
RESIST_KEYS: frozenset[str] = frozenset(
    {one.resist_key for one in DamageType} | {"resist_physical_percent", "resist_magic_percent"}
)

#: Чем бьёт тот, у кого в руках ничего нет.
UNARMED = DamageType.BLUDGEONING


def damage_type_name(one: DamageType) -> str:
    return DAMAGE_TYPE_NAMES[one]
