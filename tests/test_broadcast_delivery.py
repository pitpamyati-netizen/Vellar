"""Командная публикация использует общую очередь, сохраняет отказ и номер."""

from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from scripts import broadcast as script

from mmorpg.application.delivery import DeliveryPriority, DeliveryStatus
from mmorpg.config import AppEnv, Settings
from mmorpg.infrastructure.persistence.memory_delivery import MemoryDeliveryQueue
from tests.presentation.test_delivery import QueueSession


@pytest.fixture
async def publication(monkeypatch):
    session = QueueSession()
    bot = Bot("765432:ABCDEF-test", session=session)
    queue = MemoryDeliveryQueue()
    calls = []

    async def close():
        calls.append("close")

    async def create_pool(settings):
        calls.append("open")
        return SimpleNamespace(close=close)

    settings = Settings(
        app_env="solo", bot_token="765432:ABCDEF-test", channel_id="@channel", _env_file=None
    )
    monkeypatch.setattr(script, "load_settings", lambda: settings)
    monkeypatch.setattr(script, "create_postgres_pool", create_pool)
    monkeypatch.setattr(script, "Bot", lambda **kwargs: bot)
    monkeypatch.setattr(script, "PostgresDeliveryQueue", lambda pool: queue)
    try:
        yield SimpleNamespace(queue=queue, session=session, calls=calls, settings=settings)
    finally:
        await session.close()


def args(*extra):
    return script.parse_args(["--kind", "service", "--headline", "Открылась новая дорога.", *extra])


async def test_dry_run_never_opens_storage_or_contacts_telegram(publication):
    e = publication
    assert await script._send(args("--dry-run")) == 0
    assert not e.calls
    assert not e.session.received
    assert not e.queue._rows


async def test_repeat_cli_uses_saved_delivery_and_shared_budget(publication):
    e = publication
    assert await script._send(args()) == 0
    assert await script._send(args()) == 0
    assert len(e.queue._rows) == 1
    assert len(e.session.sent) == 1
    row = next(iter(e.queue._rows.values()))
    assert row.priority == DeliveryPriority.ANNOUNCEMENT
    assert row.status == DeliveryStatus.SENT
    assert len(e.queue._budgets[765432].attempts) == 1
    assert e.calls == ["open", "close", "open", "close"]


async def test_deliberate_new_delivery_id_can_publish_same_text_again(publication):
    e = publication
    assert await script._send(args("--delivery-id", "first")) == 0
    assert await script._send(args("--delivery-id", "second")) == 0
    assert len(e.queue._rows) == 2
    assert len(e.session.sent) == 2


async def test_429_is_saved_and_reported_as_pending(publication, capsys):
    e = publication
    e.session.errors = [TelegramRetryAfter(SendMessage(chat_id=42, text="x"), "flood", 300)]
    assert await script._send(args()) == 0
    assert "сохранено в очереди" in capsys.readouterr().err
    assert not e.session.sent
    row = next(iter(e.queue._rows.values()))
    assert row.status == DeliveryStatus.QUEUED
    assert row.last_error == "TelegramRetryAfter"


async def test_local_mode_refuses_external_publish_without_shared_storage(publication, monkeypatch):
    e = publication
    settings = e.settings.model_copy(update={"app_env": AppEnv.LOCAL})
    monkeypatch.setattr(script, "load_settings", lambda: settings)
    assert await script._send(args()) == 1
    assert not e.calls
    assert not e.session.received
