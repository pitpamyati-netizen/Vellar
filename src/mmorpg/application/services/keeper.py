"""Keeping the keeper flag honest.

``ADMIN_IDS`` is the source of truth and the character column is a mirror of it,
so the two are reconciled every time a character is loaded for their owner: an id
added to the setting gains the screen on the next press, an id taken out of it
loses the screen just as fast. Nothing in the game can set this flag - it is only
ever copied from the environment (``Claude.md``, rule 5: the handler asks, the
service decides).
"""

from __future__ import annotations

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import CharacterRepository


async def sync_keeper(
    character: Character,
    telegram_id: int,
    settings: Settings,
    characters: CharacterRepository,
) -> Character:
    """Return the character with the flag the environment says it should have.

    Writes only when the answer changed, so the common case - a player who is not
    a keeper, and never was - costs one comparison and no round trip.
    """
    wanted = settings.is_admin(telegram_id)
    if character.is_admin == wanted:
        return character
    updated = character.as_admin(wanted)
    await characters.save(updated)
    return updated
