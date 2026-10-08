"""Полный маршрут новой и возвращающейся игры через настоящий диспетчер."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.application.journey import Activity, Visit
from mmorpg.application.operations import Operation
from mmorpg.domain.entities.character import Character
from mmorpg.infrastructure.persistence.journey import MemoryJourneyRepository
from mmorpg.main import build_application
from mmorpg.presentation.telegram import reading
from mmorpg.presentation.telegram.flows.play import begin, render
from mmorpg.presentation.telegram.messaging import send_screen
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.journey import history_screen, returning_screen
from tests.presentation.test_command_journal import RecordingSession, update


def test_screen_body_never_drops_the_end():
    screen = Screen(
        ScreenId.CREATE_CONFIRM,
        ("Создание.", "А" * 3000, "Б" * 2000),
        rows=((reading.label("Подтвердить"),),),
    )
    assert screen.body().endswith("Б" * 2000)
    pieces = reading.parts(screen)
    assert "".join(pieces).count("А") == 3000
    assert "".join(pieces).count("Б") == 2000
    for number in range(1, len(pieces) + 1):
        page = reading.page(screen, number)
        assert len(page.body()) <= 2000
        assert (page.find("Подтвердить") is not None) == (number == len(pieces))


@pytest.mark.parametrize(
    "text",
    ["Я" * 10000, "\U0001f600" * 5000, "a\n" * 5000],
    ids=["long-line", "unicode", "many-lines"],
)
def test_extreme_lines_and_unicode_are_readable(text):
    assert reading.requested_number("9" * 4000) == 1_000_000
    assert reading.requested_number("0" * 4000) == 1
    screen = Screen(ScreenId.COMBAT, (text,))
    parts = reading.parts(screen)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    for number in range(1, len(parts) + 1):
        assert len(reading.page(screen, number).body().encode("utf-16-le")) // 2 <= 4096


async def test_journey_is_numeric_and_cohorts_exclude_old_players():
    repo = MemoryJourneyRepository()
    await repo.observe(Visit(1, 42, "name", 10), None, "start")
    await repo.observe(
        Visit(1, 42, "main_menu", 2000, 1, 63), Activity(2000, 8, tutorial_steps=1), "gain"
    )
    await repo.observe(Visit(1, 99, "main_menu", 2000, 2, 63), None, "old")
    stats = await repo.stats(1, inactive_before=3000)
    assert (stats.started, stats.created, stats.completed, stats.returned) == (1, 1, 1, 1)
    assert dict(stats.inactive) == {"main_menu": 1}
    assert dict(stats.reached)["tutorial:hand_in"] == 1
    assert await repo.history(1, 99) == ()
    assert await repo.history(2, 42) == ()


async def test_history_orders_actions_within_one_second():
    repo = MemoryJourneyRepository()
    await repo.observe(Visit(1, 42, "main_menu", 10, 1), Activity(10, gold=8), "first")
    await repo.observe(Visit(1, 42, "main_menu", 10, 1), Activity(10, gold=-3), "second")
    assert [a.gold for a in await repo.history(1, 42)] == [-3, 8]


async def test_journey_rolls_back_and_replays_with_the_command():
    from pydantic import TypeAdapter

    repo = MemoryJourneyRepository()
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        await repo.observe(Visit(1, 42, "name", 1), Activity(1, 8), "one")
        if calls == 1:
            raise ValueError("controlled failure")
        return 1

    def operation():
        return Operation("one", "test", participants=(repo,))

    with pytest.raises(ValueError):
        await repo.operations.run(
            action,
            operation=operation(),
            fingerprint="same",
            codec=TypeAdapter(int),
            participants=(repo,),
        )
    assert await repo.get(1, 42) is None
    for _ in range(2):
        await repo.operations.run(
            action,
            operation=operation(),
            fingerprint="same",
            codec=TypeAdapter(int),
            participants=(repo,),
        )
    assert calls == 2
    assert len(await repo.history(1, 42)) == 1


def test_returning_and_history_name_next_action(content):
    hero = Character(1, 42, "Аргус", "human", "warrior")
    screen = returning_screen(content, hero, begin(hero), latest=Activity(1, gold=8))
    assert "Следующее дело обучения" in screen.body()
    assert "/обучение" in screen.body()
    assert "command:" not in screen.body()
    assert "золото при себе: плюс 8" in screen.body()
    assert "Продолжить дело" in history_screen(()).button_texts()[0]


async def test_dispatcher_start_keeps_saved_location_and_reading_works(content):
    from mmorpg.config import Settings
    from mmorpg.presentation.telegram.flows.state import LocationSession
    from mmorpg.presentation.telegram.states.screens import Play

    settings = Settings(app_env="local", bot_token="123456:ABCDEF-test", _env_file=None)
    app = await build_application(settings)
    await app.bot.session.close()
    session = RecordingSession()
    app.bot.session = session
    deps = app.dispatcher.workflow_data  # dependency middleware owns repositories
    assert isinstance(deps, dict)
    # Адаптеры приложения доступны через наружную обёртку зависимостей.
    dependency = next(
        m for m in app.dispatcher.update.outer_middleware if hasattr(m, "_dependencies")
    )
    dependencies = dependency._dependencies
    character = await dependencies.characters.create(Character(0, 42, "Аргус", "human", "warrior"))
    state = app.dispatcher.fsm.get_context(bot=app.bot, chat_id=42, user_id=42)
    flow = replace(
        begin(character),
        screen=ScreenId.LOCATION,
        session=LocationSession(city_id="farhold", slot=1, node=0),
    )
    await state.set_state(Play.location)
    await state.update_data(play=flow.serialise())
    try:
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=101, text="/start"))
        assert "Возвращение" in session.sent[-1].text
        assert (await state.get_data())["play"] == flow.serialise()
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=102, text="/дело"))
        assert "Тропы" in session.sent[-1].text or "Узел" in session.sent[-1].text
        # Длинный экран идёт тем же путём, с сохранённой клавиатурой.
        token = reading.reader_cache.set(dependencies.state_cache)
        try:
            await send_screen(
                update(app.bot).message,
                Screen(
                    ScreenId.COMBAT,
                    ("Бой.", "А" * 5000),
                    rows=((reading.label("Позднее действие"),),),
                ),
            )
        finally:
            reading.reader_cache.reset(token)
        assert "Часть 1 из" in session.sent[-1].text
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=103, text="/текст 999"))
        assert "Часть 4 из 4" in session.sent[-1].text
        assert any(
            b.text == "Позднее действие"
            for row in session.sent[-1].reply_markup.keyboard
            for b in row
        )
        from mmorpg.presentation.telegram.messaging import send_text

        token = reading.reader_cache.set(dependencies.state_cache)
        try:
            await send_text(
                update(app.bot).message,
                "Р" * 5000 + "Конец награды.",
                Screen(
                    ScreenId.COMBAT,
                    ("А" * 5000, "Конец боя."),
                    rows=((reading.label("Позднее действие"),),),
                ),
            )
        finally:
            reading.reader_cache.reset(token)
        saved = await dependencies.state_cache.get(reading.key(app.bot.id, 42))
        assert saved and "Конец награды." in saved and "Конец боя." in saved
        # Новый короткий экран снимает старое продолжение.
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=104, text="/меню"))
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=105, text=reading.NEXT))
        assert "уже сменился" in session.sent[-1].text
    finally:
        await app.stack.aclose()


def test_class_details_have_actual_counts(content):
    from mmorpg.presentation.telegram.screens.creation import class_details_screen

    for klass in content.classes:
        owned = [s for s in content.skills if s.owner == f"class:{klass.id}"]
        active = sum(s.is_active for s in owned)
        assert (
            f"боевых {active}, пассивных {len(owned) - active}"
            in class_details_screen(content, klass.id).body()
        )


async def test_returning_to_saved_battle_preserves_turn_and_result(content):
    import json

    from mmorpg.application.battle_snapshots import encode
    from mmorpg.application.services.battle import BattleStore
    from mmorpg.application.services.battle import begin as battle_begin
    from mmorpg.config import Settings
    from mmorpg.domain.entities.location import Enemy, EnemyKind
    from mmorpg.presentation.telegram.states.screens import Play

    app = await build_application(
        Settings(app_env="local", bot_token="123456:ABCDEF-test", _env_file=None)
    )
    await app.bot.session.close()
    session = RecordingSession()
    app.bot.session = session
    deps = next(
        m._dependencies
        for m in app.dispatcher.update.outer_middleware
        if hasattr(m, "_dependencies")
    )
    hero = await deps.characters.create(Character(0, 42, "Аргус", "human", "warrior"))
    state = app.dispatcher.fsm.get_context(bot=app.bot, chat_id=42, user_id=42)
    flow = begin(hero)
    battle, roster = battle_begin(
        content,
        battle_id="m04-return-control",
        attackers=[(hero, True)],
        enemies=[Enemy("wolf", "Волк", EnemyKind.BEAST, 1, 9999, 1, 0, 0, (), 10)],
        seed=b"m04-return",
        owner=hero.id,
    )
    store = BattleStore(deps.state_cache)
    battle = replace(
        battle, roster_snapshot=json.dumps(encode(roster)), play_snapshot=flow.serialise()
    )
    battle = await store.save(await store.pin(battle, content))
    await state.set_state(Play.combat)
    await state.update_data(battle=battle.id, play=flow.serialise(), battle_version=battle.version)
    try:
        for number, command in enumerate(("/start", "/история", "/дело"), 500):
            await app.dispatcher.feed_update(app.bot, update(app.bot, number=number, text=command))
            assert await store.load(battle.id) == battle
            assert await deps.characters.get(hero.id) == hero
        assert "Бой" in session.sent[-1].text
        assert (await state.get_data())["battle"] == battle.id
    finally:
        await app.stack.aclose()


def test_tutorial_command_moves_to_real_step(content):
    from mmorpg.presentation.telegram.flows.play import advance

    hero = Character(1, 42, "Аргус", "human", "warrior")
    flow = advance(content, hero, begin(hero), "/обучение", world_seed="m04")
    assert flow.screen == ScreenId.STATS
    assert "опыта" in flow.notice or "Шаг обучения сделан" in flow.notice
    assert render(content, hero, flow, world_seed="m04").id == ScreenId.STATS


async def test_first_session_from_creation_through_all_six_real_tasks(content):
    from collections import deque

    from mmorpg.config import Settings
    from mmorpg.domain.entities.location import NodeKind
    from mmorpg.domain.rules import quests, tutorial
    from mmorpg.presentation.telegram.flows.play import build_location
    from mmorpg.presentation.telegram.flows.state import PlayState
    from mmorpg.presentation.telegram.screens.play import NODE_ACTIONS, node_button
    from mmorpg.presentation.telegram.screens.quests import quest_button

    settings = Settings(
        app_env="local",
        bot_token="123456:ABCDEF-test",
        _env_file=None,
        world_seed="m04-first-session",
    )
    app = await build_application(settings)
    await app.bot.session.close()
    session = RecordingSession()
    app.bot.session = session
    deps = next(
        m._dependencies
        for m in app.dispatcher.update.outer_middleware
        if hasattr(m, "_dependencies")
    )
    state = app.dispatcher.fsm.get_context(bot=app.bot, chat_id=42, user_id=42)
    number = 200

    async def press(text):
        nonlocal number
        number += 1
        await app.dispatcher.feed_update(app.bot, update(app.bot, number=number, text=text))
        return session.sent[-1]

    async def character():
        return await deps.characters.get_active(42)

    async def flow():
        return PlayState.deserialise((await state.get_data())["play"])

    async def move_to(target):
        current = await flow()
        generated = build_location(content, settings.world_seed, current.session)
        queue = deque([(current.session.node, ())])
        seen = set()
        while queue:
            node, path = queue.popleft()
            if node == target:
                for index in path:
                    await press(node_button(generated.nodes[index]).text)
                return
            if node in seen:
                continue
            seen.add(node)
            queue.extend(
                (link, (*path, link)) for link in generated.nodes[node].links if link not in seen
            )
        raise AssertionError("A novice cannot reach the next task")

    async def enter_location(slot):
        await press("/меню")
        await press("Мир")
        await press("Локации")
        city = content.city((await character()).city_id)
        prefix = f"{slot}. {city.location(slot).name}"
        button = next(
            b.text
            for row in session.sent[-1].reply_markup.keyboard
            for b in row
            if b.text.startswith(prefix)
        )
        await press(button)

    try:
        for text in (
            "/start",
            "Аргус",
            "Дварф",
            "Продолжить",
            "Воин — стойкий боец ближнего боя",
            "Продолжить",
            "Берсерк",
            "Дуэлянт",
            "Продолжить",
            *(["Сила плюс один"] * 10),
            "Продолжить",
            "Подтвердить",
        ):
            await press(text)
        hero = await character()
        assert hero is not None, session.sent[-1].text
        assert "/обучение" in session.sent[-1].text
        first = await press("/обучение")
        hero = await character()
        assert tutorial.is_done(hero, tutorial.TutorialTask.STATS)
        assert hero.gold == tutorial.STEP_REWARD.gold
        assert "Награда за обучение" in first.text
        await press("/осмотреться")
        assert (await character()).gold == hero.gold
        await press("/обучение")
        await press(
            next(
                b.text
                for row in session.sent[-1].reply_markup.keyboard
                for b in row
                if b.text.startswith("Боевой слот 1")
            )
        )
        await press(
            next(
                b.text
                for row in session.sent[-1].reply_markup.keyboard
                for b in row
                if " — ранг " in b.text
            )
        )
        assert tutorial.is_done(await character(), tutorial.TutorialTask.SKILL_SLOT)
        await press("/обучение")
        quest = quests.available(content, await character(), hero.city_id)[0]
        await press(quest_button(quest).text)
        await press("Согласиться")
        assert tutorial.is_done(await character(), tutorial.TutorialTask.QUEST)
        await enter_location(quest.location_slot or 1)
        generated = build_location(content, settings.world_seed, (await flow()).session)
        # Первое задание собирает действия узлов; идём по настоящим связям карты.
        assert quest.objective.value == "search"
        for node in generated.nodes:
            if node.kind in {NodeKind.GATHER, NodeKind.CACHE, NodeKind.EVENT, NodeKind.SHRINE}:
                await move_to(node.index)
                await press(NODE_ACTIONS[node.kind])
                if (await character()).quests.progress(quest.id) >= quest.target_count:
                    break
        assert (await character()).quests.progress(quest.id) >= quest.target_count
        fights = sorted(
            (n for n in generated.nodes if n.kind == NodeKind.BATTLE), key=lambda n: n.level
        )
        await move_to(fights[0].index)
        fight_button = next(
            b.text
            for row in session.sent[-1].reply_markup.keyboard
            for b in row
            if b.text.startswith("Вступить в бой")
        )
        await press(fight_button)
        for _ in range(40):
            await press("/бой атака")
            if tutorial.is_done(await character(), tutorial.TutorialTask.FIGHT):
                break
        assert tutorial.is_done(await character(), tutorial.TutorialTask.FIGHT)
        await press("/обучение")
        import re

        # Добыча случайна: покупаем доступный товар с настоящего прилавка.
        available_gold = (await character()).gold
        purchases = [
            b.text
            for row in session.sent[-1].reply_markup.keyboard
            for b in row
            if (price := re.search(r" — (\d+) золота, купить$", b.text))
            and int(price[1]) <= available_gold
        ]
        assert purchases, (session.sent[-1].text, session.sent[-1].reply_markup)
        await press(purchases[0])
        await press("Купить")
        assert tutorial.is_done(await character(), tutorial.TutorialTask.TRADE)
        await press("/обучение")
        await press("Сдать задание")
        done = await character()
        assert tutorial.finished(done)
        assert quest.id in done.quests.done
        assert all(done.equipment.item_in(slot) for slot in tutorial.GEAR_SLOTS)
        assert await deps.inventory.count(done.id, "small_healing_potion") >= 3
        gold = done.gold
        await press("Сдать задание")
        assert (await character()).gold == gold
        await press("/история")
        assert "История действий" in session.sent[-1].text
        assert "command:" not in session.sent[-1].text
        report = await deps.journey.stats(app.bot.id, inactive_before=0)
        assert (report.started, report.created, report.completed) == (1, 1, 1)
    finally:
        await app.stack.aclose()
