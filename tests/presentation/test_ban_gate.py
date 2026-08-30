"""Дверь, закрытая для заблокированного.

Проверяется настоящая прослойка на настоящем сообщении: правило «заблокированный
не играет» стоит одно на всю игру, и держится оно тем, что до хендлера дело не
доходит вовсе.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import Chat, Message, TelegramObject, User

from mmorpg.domain.entities.moderation import Ban
from mmorpg.domain.ports.repositories import User as Account
from mmorpg.infrastructure.persistence.memory import InMemoryUserRepository
from mmorpg.presentation.telegram.middlewares.moderation import BanMiddleware

pytestmark = pytest.mark.asyncio

ACCOUNT = 800_001
DAY = 24 * 60 * 60


class Answers:
    """Сообщение, которое умеет отвечать и помнит, чем ответило."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def __call__(self, text: str, **kwargs: Any) -> None:
        self.said.append(text)


class Deletes:
    """Заглушка ``message.delete``: помнит, звали ли её."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **kwargs: Any) -> None:
        self.calls += 1


def message_from(account: int, *, private: bool = True) -> tuple[Message, Answers]:
    said = Answers()
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=account, type="private" if private else "supergroup"),
        from_user=User(id=account, is_bot=False, first_name="Игрок"),
        text="Главное меню",
    )
    object.__setattr__(message, "answer", said)
    object.__setattr__(message, "delete", Deletes())
    return message, said


class Doorman:
    """Хендлер за дверью: помнит, пустили ли до него."""

    def __init__(self) -> None:
        self.reached = 0

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> str:
        self.reached += 1
        return "прошёл"


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


async def test_an_ordinary_player_passes_and_the_account_comes_with_them(
    users: InMemoryUserRepository,
) -> None:
    """Аккаунт кладётся в данные: хендлеру не надо читать его второй раз."""
    await users.upsert(Account(telegram_id=ACCOUNT, username="игрок"))
    message, said = message_from(ACCOUNT)
    handler, data = Doorman(), {"users": users}

    assert await BanMiddleware()(handler, message, data) == "прошёл"
    assert handler.reached == 1
    assert data["user"] is not None and data["user"].telegram_id == ACCOUNT
    assert said.said == []


async def test_a_banned_player_is_told_why_and_goes_no_further(
    users: InMemoryUserRepository,
) -> None:
    await users.upsert(Account(telegram_id=ACCOUNT))
    await users.set_ban(ACCOUNT, Ban(until=int(time.time()) + 2 * DAY, reason="обман в сделке"))
    message, said = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) is None
    assert handler.reached == 0
    assert len(said.said) == 1
    assert "обман в сделке" in said.said[0]
    assert "2 суток" in said.said[0]


async def test_a_ban_that_ran_out_stops_nobody(users: InMemoryUserRepository) -> None:
    """Истёкший срок никто не снимает: он просто перестаёт действовать."""
    await users.upsert(Account(telegram_id=ACCOUNT))
    await users.set_ban(ACCOUNT, Ban(until=int(time.time()) - 1, reason="старое"))
    message, said = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) == "прошёл"
    assert said.said == []


async def test_in_the_group_the_banned_are_turned_away_in_silence(
    users: InMemoryUserRepository,
) -> None:
    """Группа — не место для разговора о наказаниях."""
    await users.upsert(Account(telegram_id=ACCOUNT))
    await users.set_ban(ACCOUNT, Ban(until=-1))
    message, said = message_from(ACCOUNT, private=False)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) is None
    assert handler.reached == 0
    assert said.said == []


async def test_an_account_nobody_stored_anything_about_passes(
    users: InMemoryUserRepository,
) -> None:
    message, _ = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) == "прошёл"
    assert handler.reached == 1


async def test_a_muted_player_has_the_group_message_wiped_and_goes_no_further(
    users: InMemoryUserRepository,
) -> None:
    await users.upsert(Account(telegram_id=ACCOUNT))
    await users.set_mute(ACCOUNT, Ban(until=int(time.time()) + DAY, reason="флуд"))
    message, said = message_from(ACCOUNT, private=False)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) is None
    assert handler.reached == 0
    assert message.delete.calls == 1  # type: ignore[attr-defined]
    assert said.said == []  # в группе о наказаниях молчат


async def test_a_muted_player_plays_normally_in_private(users: InMemoryUserRepository) -> None:
    await users.upsert(Account(telegram_id=ACCOUNT))
    await users.set_mute(ACCOUNT, Ban(until=int(time.time()) + DAY))
    message, _ = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, {"users": users}) == "прошёл"
    assert handler.reached == 1
    assert message.delete.calls == 0  # type: ignore[attr-defined]


async def _maintenance_data(users: InMemoryUserRepository, *, admin: bool) -> dict[str, Any]:
    from mmorpg.application.services.keeper_panel import MAINTENANCE_KEY
    from mmorpg.config import Settings
    from mmorpg.infrastructure.cache.memory import InMemoryStateCache

    cache = InMemoryStateCache()
    await cache.set(MAINTENANCE_KEY, "Чиним арену.", 3600)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, bot_token="0:test", admin_ids=str(ACCOUNT) if admin else ""
    )
    return {"users": users, "settings": settings, "state_cache": cache}


async def test_maintenance_mode_turns_ordinary_players_away(
    users: InMemoryUserRepository,
) -> None:
    data = await _maintenance_data(users, admin=False)
    message, said = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, data) is None
    assert handler.reached == 0
    assert "обслуживании" in said.said[0] and "Чиним арену" in said.said[0]


async def test_maintenance_mode_lets_the_keeper_through(users: InMemoryUserRepository) -> None:
    data = await _maintenance_data(users, admin=True)
    message, _ = message_from(ACCOUNT)
    handler = Doorman()

    assert await BanMiddleware()(handler, message, data) == "прошёл"
    assert handler.reached == 1
