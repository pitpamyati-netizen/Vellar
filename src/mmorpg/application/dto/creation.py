"""Черновик персонажа: что игрок выбрал к этой минуте.

Черновик неизменен и несёт выбор каждого шага, и это то, что делает «назад»
дешёвым: шаг назад ничего не отменяет, он лишь меняет, какой экран показан
(спецификация, раздел 12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from mmorpg.domain.entities.character import Character, Equipment
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.stats import StatBlock, StatCode

NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 20
_NAME_PATTERN = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z0-9 \-']*$")


@dataclass(frozen=True, slots=True)
class NameCheck:
    ok: bool
    problem: str = ""


def validate_name(name: str) -> NameCheck:
    """Имена произносят вслух, поэтому они короткие и без украшений."""
    stripped = name.strip()
    if len(stripped) < NAME_MIN_LENGTH:
        return NameCheck(False, f"Имя должно быть не короче {NAME_MIN_LENGTH} символов.")
    if len(stripped) > NAME_MAX_LENGTH:
        return NameCheck(False, f"Имя должно быть не длиннее {NAME_MAX_LENGTH} символов.")
    if not _NAME_PATTERN.match(stripped):
        return NameCheck(
            False,
            "Имя может содержать буквы, цифры, пробел, дефис и апостроф, "
            "и должно начинаться с буквы.",
        )
    return NameCheck(True)


@dataclass(frozen=True, slots=True)
class CharacterDraft:
    """Всё, выбранное при создании. Шаг назад ничего не теряет."""

    name: str = ""
    race_id: str = ""
    class_id: str = ""
    trait_ids: tuple[str, ...] = ()
    allocated: StatBlock = field(default_factory=StatBlock)

    def with_name(self, name: str) -> CharacterDraft:
        return replace(self, name=name.strip())

    def with_race(self, race_id: str) -> CharacterDraft:
        return replace(self, race_id=race_id)

    def with_class(self, class_id: str) -> CharacterDraft:
        return replace(self, class_id=class_id)

    def toggle_trait(self, trait_id: str, limit: int) -> CharacterDraft:
        """Выбрать черту или снять выбор, отказавшись превысить предел."""
        if trait_id in self.trait_ids:
            return replace(
                self, trait_ids=tuple(item for item in self.trait_ids if item != trait_id)
            )
        if len(self.trait_ids) >= limit:
            return self
        return replace(self, trait_ids=(*self.trait_ids, trait_id))

    def spend_point(self, stat: StatCode, budget: int) -> CharacterDraft:
        if self.spent_points >= budget:
            return self
        return replace(self, allocated=self.allocated.with_change(stat, 1))

    def reset_points(self) -> CharacterDraft:
        return replace(self, allocated=StatBlock())

    @property
    def spent_points(self) -> int:
        return self.allocated.total

    def points_left(self, budget: int) -> int:
        return max(0, budget - self.spent_points)

    def is_complete(self, content: GameContent) -> bool:
        rules = content.rules
        return bool(
            validate_name(self.name).ok
            and self.race_id
            and self.class_id
            and len(self.trait_ids) == rules.traits_at_creation
            and self.spent_points == rules.free_points_at_creation
        )

    def to_character(self, content: GameContent, user_id: int) -> Character:
        """Превратить черновик в персонажа первого уровня с его стартовым набором.

        Первое активное умение класса и активное умение расы кладутся в панель *и
        считаются изученными* - выданы, а не куплены: у персонажа первого уровня нет ни
        одного очка умений, а первый бой не должен драться с пустой панелью. Больше в
        панель не кладётся ничего, и новое умение никогда не встаёт в слот само
        (docs/skills.md).
        """
        from mmorpg.domain.entities.character import SkillLoadout
        from mmorpg.domain.entities.content import SkillKind

        first_active = content.class_skills_up_to(self.class_id, 1, SkillKind.ACTIVE)
        actives: list[str | None] = [None] * content.rules.active_slots
        if first_active:
            actives[0] = first_active[0].code

        return Character(
            id=0,
            user_id=user_id,
            name=self.name.strip(),
            race_id=self.race_id,
            class_id=self.class_id,
            allocated=self.allocated,
            trait_ids=self.trait_ids,
            equipment=self._starting_gear(content),
            loadout=SkillLoadout(
                actives=tuple(actives),
                racial=content.race(self.race_id).active_code,
            ),
            city_id=content.city_by_order(1).id,
        )

    def _starting_gear(self, content: GameContent) -> Equipment:
        """Оружие в руку и доспех на плечи — самой первой ступени.

        Весь урон в игре растёт из костей оружия (ADR 0015), и первый бой голыми
        руками был бы не «скромным началом», а наказанием за то, что персонаж
        новый: кулак бьёт втрое слабее самого простого меча. Даётся своё для
        класса: первое оружие из его списка и самый крепкий доспех, какой он
        носит, — обе вещи первой ступени и обычной редкости, то есть без единой
        прибавки. Это начало дороги, а не подарок.
        """
        from mmorpg.domain.procgen.items import gear_id

        klass = content.character_class(self.class_id)
        if not content.gear_tiers:
            return Equipment()
        first = content.gear_tiers[0].level

        by_kind = {
            (archetype.slot, archetype.weapon_type or archetype.armor_type): archetype
            for archetype in content.gear_archetypes
        }
        equipment = Equipment()
        wanted = (
            ("weapon", klass.weapon_types[0] if klass.weapon_types else ""),
            ("body", klass.armor_types[-1] if klass.armor_types else ""),
        )
        for slot, kind in wanted:
            archetype = by_kind.get((slot, kind))
            if archetype is None:
                continue
            item_id = gear_id(archetype.id, first, "common")
            if content.has_item(item_id):
                equipment = equipment.equip(slot, item_id)
        return equipment
