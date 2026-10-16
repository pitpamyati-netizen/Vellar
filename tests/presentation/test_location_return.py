"""Возврат из боя показывает настоящий узел и оставляет рабочие кнопки."""

from __future__ import annotations

from dataclasses import replace

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from mmorpg.config import Settings
from mmorpg.domain.entities import Character
from mmorpg.domain.entities.location import LocationState, Presence, Roamer
from mmorpg.domain.rules import nodes as node_rules
from mmorpg.infrastructure.cache.memory import InMemoryLocationStateCache
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    LocationSession,
    PlayState,
    advance,
    build_location,
    node_standing,
    render,
)
from mmorpg.presentation.telegram.flows.state import Descent
from mmorpg.presentation.telegram.handlers import combat
from mmorpg.presentation.telegram.screens import play as ui
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play
from tests.presentation.test_command_journal import RecordingSession, update

SEED = "vellar-prime"
NOW = 10_000
HERO = Character(id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior", level=8)


@pytest.mark.parametrize("epoch", [2, 7])
def test_render_without_shared_state_preserves_the_saved_map(content, epoch):
    flow = PlayState(
        screen=ScreenId.LOCATION,
        session=LocationSession("farhold", 1, node=2, epoch=epoch),
    )
    saved_map = render(
        content, HERO, flow, world_seed=SEED, location_state=LocationState(epoch_offset=epoch)
    )
    assert render(content, HERO, flow, world_seed=SEED) == saved_map


@pytest.fixture
async def returning(monkeypatch):
    recording = RecordingSession()
    bot = Bot("123456:ABCDEF-test", session=recording)
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=bot.id, chat_id=42, user_id=42))
    locations = InMemoryLocationStateCache(clock=lambda: NOW)
    original_state = locations.state

    async def pinned_state(city_id, slot, *, now):
        return replace(await original_state(city_id, slot, now=now), epoch_offset=2)

    monkeypatch.setattr(locations, "state", pinned_state)
    monkeypatch.setattr(combat.time, "time", lambda: NOW)
    settings = Settings(_env_file=None, world_seed=SEED)
    try:
        yield bot, state, recording, locations, settings
    finally:
        await storage.close()
        await bot.session.close()


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("roaming", [False, True])
async def test_return_reads_the_actual_map_packs_roamer_and_neighbours(
    content, returning, empty, roaming
):
    bot, state, recording, locations, settings = returning
    session = LocationSession("farhold", 1, node=2, epoch=2, wave=0)
    location = build_location(content, SEED, session)
    assert location.node(2).name == "Страж прохода"
    assert location.node(2).links == (0, 5, 6)
    world = await locations.state("farhold", 1, now=NOW)
    size = node_standing(content, SEED, session, world, NOW)[2].size
    for place in range(size if empty else 1):
        await locations.take("farhold", 1, 2, wave=0, size=size, now=NOW, ttl=3600, place=place)
    roamer = Roamer(node=2, group=False, difficulty="recon", level=1, stamp=4)
    await locations.spawn_roamer("farhold", 1, roamer, ttl=3600)
    await locations.arrive(
        "farhold", 1, Presence(character_id=2, name="Спутник", level=8, node=2), now=NOW, ttl=3600
    )
    if not empty:
        await locations.engage(
            "farhold",
            1,
            2,
            wave=0,
            place=1,
            epoch=2,
            battle_id="neighbour",
            name="Спутник",
            character_id=2,
            now=NOW,
            ttl=3600,
        )
    flow = PlayState(session=session).at(ScreenId.LOCATION).at(ScreenId.COMBAT)
    if roaming:
        descent = Descent(city_id="farhold", slot=1, roamer=True, stamp=4, level=1)
        await locations.claim_roamer(
            "farhold", 1, HERO.id, ttl=3600, encounter=descent.encounter(HERO.id), stamp=4
        )
        flow = replace(flow, descent=descent)

    await combat._leave_to_play(
        update(bot, text="Назад").message, state, content, settings, flow, HERO, locations
    )
    saved = PlayState.deserialise((await state.get_data())["play"])
    sent = recording.sent[-1]
    assert await state.get_state() == Play.location.state
    assert saved.session.node == 2 and saved.session.epoch == 2
    assert saved.session.roamer_stamp == 4
    assert not saved.descent.active and not saved.fight
    assert "узел 2: Страж прохода." in sent.text
    assert "Спутник" in sent.text
    assert "блуждающее подземелье" in sent.text.lower()
    buttons = [button.text for row in sent.reply_markup.keyboard for button in row]
    if empty:
        assert "Здесь пусто. Новое появится примерно через 3 минуты." in sent.text
        assert not saved.session.foes
        empty_action = ui.node_action(location.node(2).kind, index=2, location=location.name).text
        assert empty_action in buttons
        refused = advance(
            content,
            HERO,
            saved,
            empty_action,
            world_seed=SEED,
            clock=Clock(now=NOW),
            location_state=await locations.state("farhold", 1, now=NOW),
        )
        assert not refused.fight and refused.pending.empty
        assert "уже всё разобрали" in refused.notice
    else:
        assert saved.session.foes and 0 not in dict(saved.session.foes)
        assert "сражается Спутник" in sent.text
        assert any(text.startswith("Вмешаться") for text in buttons)
    # Каждая предложенная тропа работает сразу, без ложного отказа старой кнопки.
    shared = (await locations.state("farhold", 1, now=NOW)).with_roamer(
        await locations.roamer("farhold", 1, now=NOW)
    )
    for index in location.node(2).links:
        text = ui.node_button(location.node(index), 2, location.name).text
        assert text in buttons
        moved = advance(
            content,
            HERO,
            saved,
            text,
            world_seed=SEED,
            clock=Clock(now=NOW),
            location_state=shared,
        )
        assert moved.session.node == index and not moved.notice


async def test_return_after_a_refill_refreshes_the_choice_before_the_next_attack(
    content, returning
):
    bot, state, recording, locations, settings = returning
    session = LocationSession("farhold", 1, node=2, epoch=2, wave=0, foes=((0, "old"),))
    world = await locations.state("farhold", 1, now=NOW)
    size = node_standing(content, SEED, session, world, NOW)[2].size
    for place in range(size):
        await locations.take(
            "farhold",
            1,
            2,
            wave=0,
            size=size,
            now=NOW - node_rules.RESPAWN_SECONDS,
            ttl=3600,
            place=place,
        )
    flow = PlayState(session=session).at(ScreenId.LOCATION).at(ScreenId.COMBAT)
    await combat._leave_to_play(
        update(bot, text="Назад").message, state, content, settings, flow, HERO, locations
    )
    saved = PlayState.deserialise((await state.get_data())["play"])
    assert saved.session.wave == 1
    buttons = recording.sent[-1].reply_markup.keyboard
    choice = next(
        button.text for row in buttons for button in row if button.text.startswith("Вступить в бой")
    )
    result = advance(
        content,
        HERO,
        saved,
        choice,
        world_seed=SEED,
        clock=Clock(now=NOW),
        location_state=await locations.state("farhold", 1, now=NOW),
    )
    assert result.fight == "node:0" and result.screen is ScreenId.COMBAT


async def test_explicit_reset_during_battle_returns_to_the_current_entrance(content, returning):
    bot, state, recording, locations, settings = returning
    flow = (
        PlayState(session=LocationSession("farhold", 1, node=2, epoch=1))
        .at(ScreenId.LOCATION)
        .at(ScreenId.COMBAT)
    )
    await combat._leave_to_play(
        update(bot, text="Назад").message, state, content, settings, flow, HERO, locations
    )
    saved = PlayState.deserialise((await state.get_data())["play"])
    assert saved.session.node == 0 and saved.session.epoch == 2
    assert "Вы вернулись ко входу" in recording.sent[-1].text
    assert "узел 0: Вход." in recording.sent[-1].text


async def test_return_to_city_without_a_visit_does_not_read_a_location(
    content, returning, monkeypatch
):
    bot, state, _recording, locations, settings = returning

    async def forbidden(*args, **kwargs):
        raise AssertionError("No location belongs to this battle")

    monkeypatch.setattr(locations, "state", forbidden)
    flow = PlayState().at(ScreenId.CITY).at(ScreenId.COMBAT)
    await combat._leave_to_play(
        update(bot, text="Назад").message, state, content, settings, flow, HERO, locations
    )
    saved = PlayState.deserialise((await state.get_data())["play"])
    assert saved.screen is ScreenId.CITY and not saved.session.active
