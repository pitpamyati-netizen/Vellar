"""Accessibility settings, duplicate updates and the latency guard rails."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from mmorpg.config import Settings
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.ports import AccessibilitySettings
from mmorpg.infrastructure.cache import InMemoryIdempotencyStore
from mmorpg.monitoring import install_slow_callback_detector, measure
from mmorpg.presentation.telegram.flows.play import PlayState, advance, begin, render
from mmorpg.presentation.telegram.middlewares.idempotency import IdempotencyMiddleware
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.settings import settings_screen

WORLD_SEED = "vellar-test"
CYCLE = 100


@pytest.fixture
def hero(content: GameContent) -> Character:
    return Character(id=1, user_id=42, name="Аргус", race_id="dwarf", class_id="warrior", level=3)


def step(
    content: GameContent,
    hero: Character,
    state: PlayState,
    text: str,
    settings: AccessibilitySettings | None = None,
) -> PlayState:
    return advance(
        content, hero, state, text, cycle=CYCLE, world_seed=WORLD_SEED, settings=settings
    )


# --- settings screen --------------------------------------------------


def test_emoji_are_off_by_default() -> None:
    assert AccessibilitySettings().emoji is False


def test_settings_state_their_current_value_in_words() -> None:
    """A checkmark is invisible to a screen reader, so the value is a sentence."""
    off = settings_screen(AccessibilitySettings(emoji=False)).text()
    on = settings_screen(AccessibilitySettings(emoji=True)).text()
    assert "Эмодзи: выключены" in off
    assert "Эмодзи: включены" in on


def test_settings_are_reachable_from_the_main_menu(content: GameContent, hero: Character) -> None:
    opened = step(content, hero, begin(hero), "Настройки")
    assert opened.screen is ScreenId.SETTINGS


def test_toggling_emoji_defers_the_write_to_the_handler(
    content: GameContent, hero: Character
) -> None:
    opened = step(content, hero, begin(hero), "Настройки")
    toggled = step(content, hero, opened, "Переключить эмодзи", AccessibilitySettings())
    assert toggled.pending_settings is not None
    assert toggled.pending_settings.emoji is True
    assert "Эмодзи теперь включены" in toggled.notice


def test_toggling_verbose_descriptions(content: GameContent, hero: Character) -> None:
    opened = step(content, hero, begin(hero), "Настройки")
    toggled = step(content, hero, opened, "Переключить подробные описания", AccessibilitySettings())
    assert toggled.pending_settings is not None
    assert toggled.pending_settings.verbose is False


def test_repeat_button_changes_nothing(content: GameContent, hero: Character) -> None:
    opened = step(content, hero, begin(hero), "Настройки")
    repeated = step(content, hero, opened, "Повторить текущий экран")
    assert repeated.pending_settings is None
    assert repeated.screen is ScreenId.SETTINGS


def test_emoji_setting_changes_the_rendered_keyboard(content: GameContent, hero: Character) -> None:
    screen = render(content, hero, begin(hero), world_seed=WORLD_SEED)
    plain = screen.button_texts(emoji=False)
    fancy = screen.button_texts(emoji=True)
    assert plain != fancy
    # ...but the meaning is carried by the text either way.
    assert all(
        plain_text in fancy_text
        for plain_row, fancy_row in zip(plain, fancy, strict=True)
        for plain_text, fancy_text in zip(plain_row, fancy_row, strict=True)
    )


# --- idempotency ------------------------------------------------------


async def test_duplicate_updates_never_apply_twice() -> None:
    store = InMemoryIdempotencyStore()
    calls: list[int] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        calls.append(event.update_id)
        return "handled"

    middleware = IdempotencyMiddleware(store)
    from aiogram.types import Update

    update = Update(update_id=555)
    assert await middleware(handler, update, {}) == "handled"
    assert await middleware(handler, update, {}) is None
    assert calls == [555]


async def test_distinct_updates_are_all_handled() -> None:
    store = InMemoryIdempotencyStore()
    calls: list[int] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        calls.append(event.update_id)
        return "handled"

    from aiogram.types import Update

    middleware = IdempotencyMiddleware(store)
    for update_id in (1, 2, 3):
        await middleware(handler, Update(update_id=update_id), {})
    assert calls == [1, 2, 3]


# --- latency guard rails ----------------------------------------------


def test_slow_callback_detector_uses_the_configured_threshold() -> None:
    loop = asyncio.new_event_loop()
    try:
        settings = Settings(_env_file=None, slow_callback_seconds=0.05)  # type: ignore[call-arg]
        install_slow_callback_detector(loop, settings)
        assert loop.slow_callback_duration == 0.05
        assert loop.get_debug() is True
    finally:
        loop.close()


def test_measure_does_not_raise_on_fast_work() -> None:
    with measure("render", budget_seconds=1.0):
        sum(range(10))


def test_rendering_a_screen_stays_inside_the_budget(content: GameContent, hero: Character) -> None:
    """A typical update renders one screen; that must be nowhere near 100 ms."""
    import time

    state = begin(hero)
    started = time.perf_counter()
    for _ in range(50):
        render(content, hero, state, world_seed=WORLD_SEED)
    average = (time.perf_counter() - started) / 50
    assert average < 0.02, f"rendering took {average * 1000:.1f} ms on average"


def test_keyboards_are_cached_by_layout(content: GameContent, hero: Character) -> None:
    from mmorpg.presentation.telegram.keyboards.reply import clear_cache, keyboard_for

    clear_cache()
    screen = render(content, hero, begin(hero), world_seed=WORLD_SEED)
    first = keyboard_for(screen, emoji=False)
    second = keyboard_for(screen, emoji=False)
    assert first is second, "an unchanged screen must not rebuild its markup"


def test_content_is_loaded_once_and_shared(content: GameContent) -> None:
    """Static content lives in memory; lookups are dict hits, not file reads."""
    assert content.race("human") is content.race("human")
    assert content.skill("warrior_cleave") is content.skill("warrior_cleave")


def test_no_blocking_calls_in_handlers_or_flows() -> None:
    """time.sleep and synchronous file I/O would stall every other player."""
    from tests.conftest import SOURCE_ROOT

    watched = list((SOURCE_ROOT / "presentation").rglob("*.py"))
    watched += list((SOURCE_ROOT / "domain").rglob("*.py"))
    for path in watched:
        text = path.read_text(encoding="utf-8")
        assert "time.sleep(" not in text, path.name
        assert "requests." not in text, path.name
        assert "urlopen" not in text, path.name


def test_settings_survive_a_flow_round_trip(content: GameContent, hero: Character) -> None:
    """pending_settings is transient: it must not leak into stored FSM data."""
    opened = step(content, hero, begin(hero), "Настройки")
    toggled = step(content, hero, opened, "Переключить эмодзи", AccessibilitySettings())
    restored = PlayState.deserialise(toggled.serialise())
    assert restored.pending_settings is None
    assert restored.screen is ScreenId.SETTINGS


def test_settings_screen_keeps_the_service_row() -> None:
    screen = settings_screen(AccessibilitySettings())
    assert [item.text for item in screen.all_rows()[-1]] == [
        "Назад",
        "Осмотреться",
        "Главное меню",
    ]


def test_toggling_twice_returns_to_the_original(content: GameContent, hero: Character) -> None:
    settings = AccessibilitySettings()
    opened = step(content, hero, begin(hero), "Настройки")
    once = step(content, hero, opened, "Переключить эмодзи", settings)
    assert once.pending_settings is not None
    twice = step(content, hero, once, "Переключить эмодзи", once.pending_settings)
    assert twice.pending_settings == replace(settings, emoji=False)
