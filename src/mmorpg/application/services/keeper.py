"""Keeping the keeper flag honest.

The right comes from two places and the character column mirrors both. One is
``ADMIN_IDS``: an id standing there is a keeper always, cannot be stripped of it
from inside the game, and is the only one who hands the right to somebody else.
The other is that handing out: it is stored on the account (``users.keeper``),
because a right a player could walk around by rolling a second character would
not be one.

The two are reconciled every time a character is loaded for their owner, so a
right given or taken away lands on the next press. Nothing in the game writes the
character column directly - it is only ever copied from these two sources
(``Claude.md``, rule 5: the handler asks, the service decides).
"""

from __future__ import annotations

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.ports.repositories import CharacterRepository, UserRepository


async def sync_keeper(
    character: Character,
    telegram_id: int,
    settings: Settings,
    characters: CharacterRepository,
    *,
    granted: bool = False,
) -> Character:
    """Return the character with the flag the two sources say it should have.

    ``granted`` is what the account was handed from inside the game; the setting
    is checked here. Writes only when the answer changed, so the common case - a
    player who is not a keeper, and never was - costs one comparison and no round
    trip.
    """
    wanted = granted or settings.is_admin(telegram_id)
    if character.is_admin == wanted:
        return character
    updated = character.as_admin(wanted)
    await characters.save(updated)
    return updated


async def is_keeper(users: UserRepository, telegram_id: int, settings: Settings) -> bool:
    """Whether this account holds the right at all, from either source."""
    if settings.is_admin(telegram_id):
        return True
    user = await users.get(telegram_id)
    return user is not None and user.keeper


async def set_keeper(
    users: UserRepository,
    characters: CharacterRepository,
    telegram_id: int,
    *,
    keeper: bool,
    settings: Settings,
) -> bool:
    """Hand the right to an account, or take it back. False when it cannot change.

    An account named by ``ADMIN_IDS`` is refused: its right lives outside the game
    and pretending to take it away here would only put the mirror out of step with
    what the next load reads back.

    Every character of the account gets the mirror written, not just the active
    one: the right belongs to the person, and they may be playing any of them.
    """
    if settings.is_admin(telegram_id):
        return False
    await users.set_keeper(telegram_id, keeper)
    for character in await characters.list_for_user(telegram_id):
        if character.is_admin != keeper:
            await characters.save(character.as_admin(keeper))
    return True
