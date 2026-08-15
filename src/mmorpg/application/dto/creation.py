"""The character draft: what the player has chosen so far.

The draft is immutable and carries every step's choice, which is what makes
"back" cheap - stepping back does not undo anything, it only changes which screen
is shown (spec section 12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from mmorpg.domain.entities.character import Character
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
    """Names are spoken aloud, so they stay short and free of decoration."""
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
    """Everything chosen during creation. Nothing is lost by going back."""

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
        """Pick or unpick a trait, refusing to exceed the limit."""
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
        """Turn the draft into a level 1 character with its starting loadout.

        The first class active and the racial active are slotted *and known* -
        given, not bought: a level 1 character has no skill points at all, and the
        first fight must not be fought with an empty panel. Nothing else is
        slotted, and a new skill is never auto-equipped later (docs/skills.md).
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
            loadout=SkillLoadout(
                actives=tuple(actives),
                racial=content.race(self.race_id).active_code,
            ),
            city_id=content.city_by_order(1).id,
        )
