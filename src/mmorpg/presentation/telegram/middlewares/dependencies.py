"""Dependency injection for handlers.

Handlers declare what they need as parameters; this middleware supplies it. The
objects themselves are built once at startup in the composition root, so nothing
here allocates per update.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from mmorpg.config import Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    InventoryRepository,
    LocationDeltaCache,
    PrivacyRepository,
    StateCache,
    TradeRepository,
    UserRepository,
)
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster


@dataclass(frozen=True, slots=True)
class Dependencies:
    """Everything a handler can ask for."""

    settings: Settings
    content: GameContent
    users: UserRepository
    characters: CharacterRepository
    inventory: InventoryRepository
    trades: TradeRepository
    privacy: PrivacyRepository
    state_cache: StateCache
    location_deltas: LocationDeltaCache
    broadcasts: ChannelBroadcaster

    def as_data(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            "content": self.content,
            "users": self.users,
            "characters": self.characters,
            "inventory": self.inventory,
            "trades": self.trades,
            "privacy": self.privacy,
            "state_cache": self.state_cache,
            "location_deltas": self.location_deltas,
            "broadcasts": self.broadcasts,
        }


class DependencyMiddleware(BaseMiddleware):
    def __init__(self, dependencies: Dependencies) -> None:
        self._data = dependencies.as_data()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.update(self._data)
        return await handler(event, data)
