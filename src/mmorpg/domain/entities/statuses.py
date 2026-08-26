"""Состояния: то, что висит на бойце и что-то с ним делает.

Раньше половина этого была признаком «правда или ложь», спрятанным в самом
бойце: ``stunned`` считал ходы, ``free_cast`` помнил одно нажатие, а горение и
кровотечение отличались друг от друга только названием умения, которое их
повесило. Ни спросить «что на мне висит», ни снять это по имени, ни повесить то
же самое двумя разными умениями было нельзя.

Состояние - это эффект с именем из этого списка. Имя решает всё остальное:
беда это или благо, что оно прибавляет, точит ли цель каждый ход и каким родом
урона, и пропускает ли носитель ход. Два умения, вешающие горение, вешают одно
и то же горение - оно обновляется, а не складывается вдвое (``EffectStack``).

Величина состояния (``magnitude``) - одно число, и что оно значит, сказано
здесь: для слабости это проценты урона, для горения - урон за ход, для барьера
- сколько он держит, для защиты - сколько брони она добавляет.

У состояния бывает и то, что от величины не зависит вовсе (``flat_modifiers``):
защита прибавляет брони столько, сколько стоит закрыться на этом уровне, а
уклонения - всегда одну и ту же треть. Обе прибавки объявлены здесь, потому что
обещает их состояние, а не тот, кто его повесил.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.damage import DamageType


class StatusKind(StrEnum):
    """Двадцать одно состояние игры. Больше их не бывает."""

    SILENCE = "silence"
    BURNING = "burning"
    POISON = "poison"
    FEAR = "fear"
    BLEEDING = "bleeding"
    INVULNERABILITY = "invulnerability"
    WEAKNESS = "weakness"
    EMPOWER = "empower"
    BERSERK = "berserk"
    HEAL_BLOCK = "heal_block"
    RESOURCE_BLOCK = "resource_block"
    RESOURCE_REGEN = "resource_regen"
    HEALTH_REGEN = "health_regen"
    CONFUSION = "confusion"
    HASTE = "haste"
    SLOW = "slow"
    CHARM = "charm"
    FREEZE = "freeze"
    STUN = "stun"
    BARRIER = "barrier"
    #: Закрылся: ход отдан обороне (``rules/combat.DEFEND_*``).
    GUARD = "guard"


@dataclass(frozen=True, slots=True)
class StatusSpec:
    """Что состояние такое и что оно делает.

    ``modifiers`` - прибавки, которые состояние даёт носителю: ключ из общего
    словаря и доля величины. Слабость с величиной 25 даёт ``damage_percent``
    минус двадцать пять.
    """

    kind: StatusKind
    name: str
    beneficial: bool
    modifiers: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    #: Прибавки, которые состояние даёт целиком, не оглядываясь на величину.
    flat_modifiers: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    #: Каким родом урона состояние точит носителя каждый ход. ``None`` - не точит.
    damage: DamageType | None = None
    #: Ход носителя не состоится вовсе.
    skips_turn: bool = False
    #: Снимается первым же полученным ударом.
    broken_by_damage: bool = False

    @property
    def is_dot(self) -> bool:
        return self.damage is not None


def _spec(
    kind: StatusKind,
    name: str,
    *,
    beneficial: bool = False,
    modifiers: Mapping[str, float] | None = None,
    flat_modifiers: Mapping[str, float] | None = None,
    damage: DamageType | None = None,
    skips_turn: bool = False,
    broken_by_damage: bool = False,
) -> StatusSpec:
    return StatusSpec(
        kind=kind,
        name=name,
        beneficial=beneficial,
        modifiers=MappingProxyType(dict(modifiers or {})),
        flat_modifiers=MappingProxyType(dict(flat_modifiers or {})),
        damage=damage,
        skips_turn=skips_turn,
        broken_by_damage=broken_by_damage,
    )


STATUSES: Mapping[StatusKind, StatusSpec] = MappingProxyType(
    {
        # --- беды, которые точат каждый ход ---
        StatusKind.BURNING: _spec(StatusKind.BURNING, "Горение", damage=DamageType.FIRE),
        StatusKind.POISON: _spec(StatusKind.POISON, "Яд", damage=DamageType.POISON),
        StatusKind.BLEEDING: _spec(StatusKind.BLEEDING, "Кровотечение", damage=DamageType.RENDING),
        # --- беды, отнимающие ход ---
        # Оглушение отнимает ход и больше ничего. Заморозка держит крепче, но
        # заморожённого легче разбить. Страх спадает от первого же удара - тем и
        # отличается от оглушения: испуганного можно привести в чувство.
        StatusKind.STUN: _spec(StatusKind.STUN, "Оглушение", skips_turn=True),
        StatusKind.FREEZE: _spec(
            StatusKind.FREEZE,
            "Заморозка",
            modifiers={"damage_taken_percent": 1.0},
            skips_turn=True,
        ),
        StatusKind.FEAR: _spec(StatusKind.FEAR, "Страх", skips_turn=True, broken_by_damage=True),
        # --- беды, отнимающие выбор ---
        StatusKind.CHARM: _spec(StatusKind.CHARM, "Очарование"),
        StatusKind.CONFUSION: _spec(StatusKind.CONFUSION, "Спутанность сознания"),
        StatusKind.SILENCE: _spec(StatusKind.SILENCE, "Молчание"),
        # --- беды, меняющие числа ---
        StatusKind.WEAKNESS: _spec(
            StatusKind.WEAKNESS, "Слабость", modifiers={"damage_percent": -1.0}
        ),
        StatusKind.SLOW: _spec(
            StatusKind.SLOW, "Замедление", modifiers={"initiative_percent": -1.0}
        ),
        StatusKind.HEAL_BLOCK: _spec(StatusKind.HEAL_BLOCK, "Запрет лечения"),
        StatusKind.RESOURCE_BLOCK: _spec(StatusKind.RESOURCE_BLOCK, "Запрет восстановления"),
        # --- блага ---
        StatusKind.EMPOWER: _spec(
            StatusKind.EMPOWER, "Усиление", beneficial=True, modifiers={"damage_percent": 1.0}
        ),
        StatusKind.BERSERK: _spec(
            StatusKind.BERSERK,
            "Берсерк",
            beneficial=True,
            modifiers={"damage_percent": 1.0, "damage_taken_percent": 1.0},
        ),
        StatusKind.HASTE: _spec(
            StatusKind.HASTE, "Ускорение", beneficial=True, modifiers={"initiative_percent": 1.0}
        ),
        StatusKind.INVULNERABILITY: _spec(
            StatusKind.INVULNERABILITY, "Неуязвимость", beneficial=True
        ),
        StatusKind.HEALTH_REGEN: _spec(
            StatusKind.HEALTH_REGEN, "Восстановление здоровья", beneficial=True
        ),
        StatusKind.RESOURCE_REGEN: _spec(
            StatusKind.RESOURCE_REGEN, "Восстановление сил", beneficial=True
        ),
        StatusKind.BARRIER: _spec(StatusKind.BARRIER, "Барьер", beneficial=True),
        # Защита - единственное состояние, которое вешает не умение, а кнопка:
        # закрыться умеет всякий, и стоит это целого хода (ADR 0025).
        StatusKind.GUARD: _spec(
            StatusKind.GUARD,
            "Защита",
            beneficial=True,
            modifiers={"armor_flat": 1.0},
            flat_modifiers={"dodge_percent": 30.0},
        ),
    }
)

#: Состояния, отнимающие ход целиком.
CONTROL_STATUSES: frozenset[StatusKind] = frozenset(
    kind for kind, spec in STATUSES.items() if spec.skips_turn
)

#: Состояния, точащие носителя каждый ход.
DOT_STATUSES: frozenset[StatusKind] = frozenset(
    kind for kind, spec in STATUSES.items() if spec.is_dot
)


def status_spec(kind: StatusKind) -> StatusSpec:
    return STATUSES[kind]


def status_name(kind: StatusKind) -> str:
    return STATUSES[kind].name


def status_id(kind: StatusKind) -> str:
    """Ключ, под которым состояние лежит в стопке эффектов.

    Один на состояние, а не на умение: два умения, вешающие горение, вешают одно
    и то же горение.
    """
    return f"status:{kind.value}"
