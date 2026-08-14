"""The mentor: an unspent point becomes a number on the character sheet.

A point is earned by levelling, so the lesson itself costs no gold - the mentor
sells the record of it, and nobody has found a way to charge for that yet.
Retraining a skill is a different bargain and is not offered at this counter yet
(Roadmap 1.2).

Spending is one point at a time and deliberately not reversible here: a reset
would be a service with its own price, not a correction the player makes for free.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.stats import StatCode


def can_train(character: Character) -> bool:
    return character.unspent_stat_points > 0


def train_stat(character: Character, code: StatCode) -> Character | None:
    """Put one unspent point into ``code``. ``None`` when there is none left."""
    if not can_train(character):
        return None
    return replace(
        character,
        allocated=character.allocated.with_change(code, 1),
        unspent_stat_points=character.unspent_stat_points - 1,
    )
