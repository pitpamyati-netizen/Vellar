"""Временные эффекты и стопка, в которой они лежат.

Эффекты лежат по ключу. Повторное наложение **обновляет** эффект, а не кладёт
второй такой же, поэтому усиление, нажатое трижды, не даёт тройной прибавки. Это
проверяет ``tests/domain/test_effects.py``.

У эффекта бывает имя состояния (``status``) - одно из объявленных в
``entities/statuses.py``. Тогда ключ у него общий на всё состояние: горение от
жезла и горение от стрелы - это одно горение, а не два. Что состояние делает,
сказано там же; здесь оно только лежит и считает свои ходы.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from mmorpg.domain.entities.statuses import (
    CONTROL_STATUSES,
    StatusKind,
    status_id,
    status_spec,
)


@dataclass(frozen=True, slots=True)
class ActiveEffect:
    """Набор прибавок со сроком, измеренным в ходах."""

    id: str
    name: str
    modifiers: Mapping[str, float]
    turns_left: int
    source: str = ""
    # Помогает ли эффект носителю. Ставится тем, кто эффект создаёт: по знаку
    # прибавки этого не понять (положительный ``damage_taken_percent`` - беда).
    beneficial: bool = True
    #: Какое это состояние. ``None`` - обычная прибавка, не состояние.
    status: StatusKind | None = None
    #: Величина состояния. Что она значит, решает само состояние: для слабости
    #: это проценты урона, для горения - урон за ход, для барьера - сколько он
    #: держит.
    magnitude: float = 0.0
    #: Держится весь бой и ходами не меряется. Так живёт место в отряде: щит
    #: остаётся щитом до конца боя, и снять это ни ходом, ни очищением нельзя
    #: (``entities/party.py``).
    permanent: bool = False

    def ticked(self) -> ActiveEffect:
        if self.permanent:
            return self
        return replace(self, turns_left=self.turns_left - 1)

    @property
    def expired(self) -> bool:
        return not self.permanent and self.turns_left <= 0


def status_effect(
    kind: StatusKind,
    *,
    turns: int,
    magnitude: float = 0.0,
    source: str = "",
) -> ActiveEffect:
    """Состояние как эффект: имя, знак и прибавки берутся из его описания."""
    spec = status_spec(kind)
    return ActiveEffect(
        id=status_id(kind),
        name=spec.name,
        modifiers=MappingProxyType(
            {key: magnitude * scale for key, scale in spec.modifiers.items()}
            | dict(spec.flat_modifiers)
        ),
        turns_left=max(1, turns),
        source=source,
        beneficial=spec.beneficial,
        status=kind,
        magnitude=magnitude,
    )


@dataclass(frozen=True, slots=True)
class EffectStack:
    """Неизменяемый набор действующих эффектов по ключу."""

    effects: Mapping[str, ActiveEffect] = field(default_factory=dict)

    def __iter__(self) -> Iterator[ActiveEffect]:
        return iter(self.effects.values())

    def __len__(self) -> int:
        return len(self.effects)

    def __contains__(self, effect_id: str) -> bool:
        return effect_id in self.effects

    def apply(self, effect: ActiveEffect) -> EffectStack:
        """Добавить или обновить эффект.

        Повторное наложение оставляет больший из двух сроков и большую из двух
        величин, а прибавки не складывает: состояние, наложенное дважды, - это
        одно состояние, только посильнее и подольше.
        """
        current = self.effects.get(effect.id)
        if current is None:
            merged = effect
        else:
            stronger = effect if effect.magnitude >= current.magnitude else current
            merged = replace(stronger, turns_left=max(effect.turns_left, current.turns_left))
        return EffectStack(MappingProxyType({**self.effects, effect.id: merged}))

    def remove(self, effect_id: str) -> EffectStack:
        remaining = {key: value for key, value in self.effects.items() if key != effect_id}
        return EffectStack(MappingProxyType(remaining))

    def cleanse(self, count: int, *, beneficial: bool = False) -> EffectStack:
        """Снять до ``count`` эффектов, начиная со старших.

        По умолчанию снимаются беды - это и делает всякое умение очищения.
        """
        remaining = dict(self.effects)
        for effect_id, effect in self.effects.items():
            if count <= 0:
                break
            if effect.permanent:
                # Место в отряде очищением не снимается: это не то, что на бойца
                # повесили, а то, кем он в этом бою стоит.
                continue
            if effect.beneficial is beneficial:
                del remaining[effect_id]
                count -= 1
        return EffectStack(MappingProxyType(remaining))

    def tick(self) -> EffectStack:
        """Прожить один ход и выбросить то, что кончилось."""
        advanced = {
            effect_id: ticked
            for effect_id, effect in self.effects.items()
            if not (ticked := effect.ticked()).expired
        }
        return EffectStack(MappingProxyType(advanced))

    def modifiers(self) -> dict[str, float]:
        """Сумма прибавок всех действующих эффектов."""
        total: dict[str, float] = {}
        for effect in self.effects.values():
            for key, value in effect.modifiers.items():
                total[key] = total.get(key, 0.0) + value
        return total

    def penalties(self) -> tuple[ActiveEffect, ...]:
        return tuple(effect for effect in self.effects.values() if not effect.beneficial)

    # --- состояния ----------------------------------------------------

    def statuses(self) -> tuple[ActiveEffect, ...]:
        """Всё, что висит на бойце состоянием, а не безымянной прибавкой."""
        return tuple(effect for effect in self.effects.values() if effect.status is not None)

    def has(self, kind: StatusKind) -> bool:
        return status_id(kind) in self.effects

    def status(self, kind: StatusKind) -> ActiveEffect | None:
        return self.effects.get(status_id(kind))

    def magnitude_of(self, kind: StatusKind) -> float:
        effect = self.status(kind)
        return effect.magnitude if effect is not None else 0.0

    def turns_of(self, kind: StatusKind) -> int:
        effect = self.status(kind)
        return effect.turns_left if effect is not None else 0

    def without(self, kind: StatusKind) -> EffectStack:
        return self.remove(status_id(kind))

    def control(self) -> ActiveEffect | None:
        """Состояние, отнимающее у носителя ход. ``None`` - ход за ним."""
        for effect in self.effects.values():
            if effect.status is not None and effect.status in CONTROL_STATUSES:
                return effect
        return None

    def without_control(self) -> EffectStack:
        """Снять всё, что отнимает ход: то, чем платит «вас нельзя оглушить»."""
        remaining = {
            key: value
            for key, value in self.effects.items()
            if value.status is None or value.status not in CONTROL_STATUSES
        }
        return EffectStack(MappingProxyType(remaining))
