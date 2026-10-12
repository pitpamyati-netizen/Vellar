"""Отдых записывается вместе с командой; незавершённый бой исключает его."""

from dataclasses import dataclass

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository, StateCache
from mmorpg.domain.rules.health_recovery import recover


@dataclass
class HealthRecovery:
    content: GameContent
    characters: CharacterRepository
    _cache: StateCache

    @atomic_action
    async def apply(self, character_id: int, *, now: int) -> Character | None:
        character = await self.characters.get(character_id)
        if character is None or await BattleStore(self._cache).busy(character_id):
            return character
        rested = recover(self.content, character, now=now)
        return await self.characters.save(rested) if rested != character else character
