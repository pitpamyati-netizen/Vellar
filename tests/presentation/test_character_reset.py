"""Путь сброса через приложение: команды, отмена, старые кнопки и обучение."""

import pytest

from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import tutorial
from mmorpg.main import build_application
from mmorpg.presentation.telegram.flows.creation import CreationState, render
from mmorpg.presentation.telegram.handlers.character_reset import (
    KEY,
    Reset,
    begin_selection,
    selection_screen,
    warning,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play
from tests.presentation.test_command_journal import RecordingSession, update
from tests.test_main import _dependencies


@pytest.fixture
async def reset_app():
    app = await build_application(
        Settings(
            app_env="local",
            bot_token="123456:ABCDEF-test",
            _env_file=None,
        )
    )
    await app.bot.session.close()
    recording = RecordingSession()
    app.bot.session = recording
    deps = _dependencies(app)
    hero = await deps.characters.create(
        Character(
            0,
            42,
            "Аргус",
            "human",
            "warrior",
            level=30,
            gold=100,
        )
    )
    state = app.dispatcher.fsm.get_context(app.bot, 42, 42)
    await state.set_state(Play.main_menu)
    try:
        yield app, deps, hero, state, recording
    finally:
        await app.stack.aclose()
        await app.bot.session.close()


async def test_command_path_new_race_features_and_class_then_confirm(reset_app):
    app, deps, hero, state, recording = reset_app
    number = 100

    async def send(text):
        nonlocal number
        number += 1
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=number, text=text))

    await send("/сброс подтвердить")
    assert await state.get_state() == Reset.review.state
    assert (await deps.characters.get(hero.id)).level == 30
    await send("/сброс выбрать")

    # Все решения доступны через пронумерованные текстовые команды.
    async def choose(predicate):
        data = await state.get_data()
        flow = CreationState.deserialise(data[KEY]["flow"])
        buttons = [button.text for row in render(app.content, flow).rows for button in row]
        index = next(i for i, text in enumerate(buttons, 1) if predicate(text))
        await send(f"/сброс выбор {index}")

    await choose(lambda text: text == "Дварф")
    await choose(lambda text: text == "Продолжить")
    await choose(lambda text: text.startswith("Маг —"))
    await choose(lambda text: text == "Продолжить")
    await choose(lambda text: text == "Берсерк")
    await choose(lambda text: text == "Дуэлянт")
    await choose(lambda text: text == "Продолжить")
    for _ in range(app.content.rules.free_points_at_creation):
        await choose(lambda text: text == "Интеллект плюс один")
    await choose(lambda text: text == "Продолжить")
    await send("Подтвердить")
    assert (await deps.characters.get(hero.id)).level == 30
    assert await state.get_state() == Reset.selection.state
    await send("/сброс подтвердить")
    fresh = await deps.characters.get(hero.id)
    assert (fresh.level, fresh.race_id, fresh.class_id, fresh.gold) == (1, "dwarf", "mage", 0)
    assert tutorial.finished(fresh)
    assert not (await state.get_data()).get(KEY)
    assert await state.get_state() == Play.main_menu.state
    await send("/обучение")
    assert (await deps.characters.get(hero.id)).gold == 0
    assert all(method.parse_mode is None for method in recording.sent)


@pytest.mark.parametrize("cancel", ["/назад", "/меню", "Главное меню", "/start"])
async def test_cancel_keeps_original_character(reset_app, cancel):
    app, deps, hero, state, _ = reset_app
    for number, text in enumerate(("Сбросить персонажа", "/сброс выбрать", cancel), 201):
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=number, text=text))
    assert await deps.characters.get(hero.id) == hero
    assert await state.get_state() == Play.main_menu.state


def test_warning_and_new_choice_are_clear_and_have_back(content):
    hero = Character(1, 42, "Аргус", "human", "warrior")
    screen = warning(hero)
    assert "Имя сохранится" in screen.body() and "Обучение" in screen.body()
    flow = begin_selection(hero)
    assert flow.screen is ScreenId.CREATE_RACE
    assert flow.draft.race_id == flow.draft.class_id == ""
    assert flow.draft.trait_ids == () and flow.draft.allocated.total == 0
    screen = selection_screen(content, flow)
    assert "Назад" in {text for row in screen.button_texts() for text in row}
    assert "/сброс выбор 1" in screen.body()
