"""Подстановка зависимостей хендлерам.

Хендлер объявляет параметрами, что ему нужно, а эта мидлварь это выдаёт. Сами
объекты собираются один раз на старте, в корне композиции, поэтому здесь ничего
не создаётся на каждое обновление.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from mmorpg.application.services.content import ContentRegistry
from mmorpg.config import Settings
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    InventoryRepository,
    KeeperLogRepository,
    LocationStateCache,
    PrivacyRepository,
    StateCache,
    TradeRepository,
    UserRepository,
)
from mmorpg.presentation.telegram.broadcast import ChannelBroadcaster


@dataclass(frozen=True, slots=True)
class Dependencies:
    """Всё, что хендлер вправе попросить."""

    settings: Settings
    registry: ContentRegistry
    users: UserRepository
    characters: CharacterRepository
    inventory: InventoryRepository
    trades: TradeRepository
    privacy: PrivacyRepository
    keeper_log: KeeperLogRepository
    state_cache: StateCache
    locations: LocationStateCache
    overlays: ContentOverlayRepository
    broadcasts: ChannelBroadcaster

    def as_data(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            # Содержимое берётся у реестра, а не запоминается: правка смотрителя
            # должна быть видна со следующего нажатия, а не с перезапуска
            # (``application/services/content.py``).
            "content": self.registry.current,
            "registry": self.registry,
            "users": self.users,
            "characters": self.characters,
            "inventory": self.inventory,
            "trades": self.trades,
            "privacy": self.privacy,
            "keeper_log": self.keeper_log,
            "state_cache": self.state_cache,
            "locations": self.locations,
            "overlays": self.overlays,
            "broadcasts": self.broadcasts,
        }


class DependencyMiddleware(BaseMiddleware):
    def __init__(self, dependencies: Dependencies) -> None:
        self._dependencies = dependencies

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Словарь собирается на каждом обновлении: одно поле в нём живое.
        data.update(self._dependencies.as_data())
        return await handler(event, data)
