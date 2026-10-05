"""Ответы и состояние экранов следуют за сохранением ценностей."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage
from aiogram.types import Message

from mmorpg.application.operations import (
    OperationCommittedError,
    OperationOutcomeUnknownError,
    StaleCharacterError,
    atomic_action,
)
from mmorpg.domain.entities.character import Character
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.cache.memory_operations import AtomicMemoryStorage
from mmorpg.infrastructure.persistence.memory import InMemoryCharacterRepository
from mmorpg.presentation.telegram.middlewares.errors import ErrorMiddleware
from mmorpg.presentation.telegram.middlewares.operations import EconomicSendMiddleware

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("fail", [False, True])
async def test_memory_screen_and_hero_change_together(fail):
    characters = InMemoryCharacterRepository()
    hero = await characters.create(
        Character(
            id=0,
            user_id=42,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            gold=100,
        )
    )
    cache = InMemoryStateCache()
    storage = AtomicMemoryStorage()
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)
    await cache.set("reward", "unclaimed", 60)
    await storage.set_data(key, {"turn": "before"})

    @atomic_action
    async def move(characters) -> None:
        await characters.grant_gold(hero.id, 50)
        await cache.set("reward", "claimed", 60)
        await storage.set_data(key, {"turn": "after"})
        assert (await storage.get_data(key))["turn"] == "after"

        async def outside():
            assert await cache.get("reward") == "unclaimed"
            assert (await storage.get_data(key))["turn"] == "before"

        await asyncio.create_task(outside())
        if fail:
            raise RuntimeError("controlled failure")

    if fail:
        with pytest.raises(RuntimeError, match="controlled failure"):
            await move(characters)
    else:
        await move(characters)
    assert (await characters.get(hero.id)).gold == (100 if fail else 150)
    assert await cache.get("reward") == ("unclaimed" if fail else "claimed")
    assert (await storage.get_data(key))["turn"] == ("before" if fail else "after")


@pytest.mark.parametrize("fail", [False, True])
async def test_success_message_waits_for_commit_and_unavailable_chat_does_not_stop_others(fail):
    characters = InMemoryCharacterRepository()
    middleware = EconomicSendMiddleware()
    bot = Bot("123456:ABCDEF-test")
    sent = []

    async def send(bot, method):
        sent.append(method.chat_id)
        if method.chat_id == 42:
            raise TelegramForbiddenError(method=method, message="blocked")
        return True

    @atomic_action
    async def move(characters) -> None:
        for chat in (42, 43, "@test_channel"):
            await middleware(send, bot, SendMessage(chat_id=chat, text="Действие сохранено."))
        assert sent == []
        if fail:
            raise RuntimeError("controlled failure")

    try:
        if fail:
            with pytest.raises(RuntimeError, match="controlled failure"):
                await move(characters)
        else:
            await move(characters)
        assert sent == ([] if fail else [42, 43, "@test_channel"])
    finally:
        await bot.session.close()


@pytest.mark.parametrize(
    "error, phrase",
    [
        (OperationCommittedError("id"), "Действие сохранено"),
        (OperationOutcomeUnknownError("id"), "результат действия пока не подтверждён"),
        (StaleCharacterError("id"), "Эта запись отменена"),
        (RuntimeError("cancelled"), "действие не выполнено"),
    ],
)
async def test_failure_reply_distinguishes_saved_unconfirmed_and_cancelled_action(error, phrase):
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    async def fail(event, data):
        raise error

    await ErrorMiddleware()(fail, message, {})
    assert phrase in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs["parse_mode"] is None
