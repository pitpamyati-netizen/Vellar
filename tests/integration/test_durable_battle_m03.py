"""Общий бой, расчёт и экран переживают сбой и исчезновение Redis."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update, User

from mmorpg.application.battle_snapshots import encode
from mmorpg.application.services.battle import BattleStore, begin
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.config import Settings
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.domain.entities.location import Enemy, EnemyKind, Roamer
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.gameplay import (
    PostgresGameplayState,
    PostgresStorage,
    StaleBattleError,
)
from mmorpg.infrastructure.persistence.postgres import (
    PostgresGuildRepository,
    PostgresPartyRepository,
)
from mmorpg.infrastructure.persistence.world import PostgresLocationState
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.flows.state import LocationSession, PlayState
from mmorpg.presentation.telegram.handlers import combat
from mmorpg.presentation.telegram.middlewares.commands import CommandMiddleware
from mmorpg.presentation.telegram.middlewares.operations import EconomicSendMiddleware
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play
from tests.integration.test_economic_operations import economy  # noqa: F401
from tests.presentation.test_command_journal import RecordingSession, dependencies

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
SETTINGS = Settings(_env_file=None)


@pytest_asyncio.fixture(loop_scope="session")
async def battle_env(economy, content):  # noqa: F811
    e = economy
    e.cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, RedisStateCache(e.redis)))
    e.world = PostgresLocationState(e.pool)
    e.storage = PostgresStorage(e.pool)
    e.store = BattleStore(e.cache)
    e.guilds = GuildStore(PostgresGuildRepository(e.pool), e.cache)
    e.parties = PartyStore(PostgresPartyRepository(e.pool), e.cache)
    e.recording = RecordingSession()
    e.bot = Bot("765432:ABCDEF-test", session=e.recording)
    e.bot.session.middleware(EconomicSendMiddleware())
    e.reaper = MessageReaper()
    e.journal = CommandMiddleware(
        dependencies(
            e.characters,
            cache=e.cache,
            world=e.world,
            guilds=e.guilds,
            parties=e.parties,
            inventory=e.inventory,
        ),
        e.reaper,
    )
    e.flow = PlayState(screen=ScreenId.LOCATION, session=LocationSession("farhold", 1, 1, 0))
    e.session, roster = begin(
        content,
        battle_id=f"m03-{uuid4()}",
        attackers=[(hero, True) for hero in e.heroes[:2]],
        enemies=[
            Enemy(
                archetype_id="grey_wolf",
                name="Волк",
                kind=EnemyKind.BEAST,
                level=1,
                max_health=9999,
                damage=1,
                armor=0,
                initiative=0,
                loot=(),
                gold=10,
            )
        ],
        owner=e.heroes[0].id,
        city_id="farhold",
        slot=1,
        node=1,
        seed=b"m03-test",
    )
    e.session = replace(
        e.session, roster_snapshot=json.dumps(encode(roster)), play_snapshot=e.flow.serialise()
    )
    e.session = await e.store.pin(e.session, content)
    e.session = await e.store.save(e.session)
    await e.world.engage(
        "farhold",
        1,
        1,
        wave=0,
        place=0,
        battle_id=e.session.id,
        name=e.heroes[0].name,
        character_id=e.heroes[0].id,
        now=100,
        ttl=3600,
    )
    for hero in e.heroes[:2]:
        state = FSMContext(
            storage=e.storage,
            key=StorageKey(bot_id=e.bot.id, chat_id=hero.user_id, user_id=hero.user_id),
        )
        await state.set_state(Play.combat)
        await state.update_data(
            {
                "battle": e.session.id,
                "battle_version": e.session.version,
                "play": e.flow.serialise(),
            }
        )
    e.sequence = 0
    e.content = content
    try:
        yield e
    finally:
        await e.reaper.aclose()
        await e.bot.session.close()
        await e.pool.execute(
            "DELETE FROM gameplay_state WHERE key=$1 OR key LIKE 'loc:farhold:1%' OR key LIKE $2",
            f"battle:{e.session.id}",
            "session:%",
        )
        for hero in e.heroes:
            await e.pool.execute("DELETE FROM gameplay_state WHERE key=$1", f"battle-of:{hero.id}")


def message(e, text, actor=0, *, number=None):
    e.sequence += 1
    hero = e.heroes[actor]
    return Message(
        message_id=number or (e.heroes[0].id * 1000 + e.sequence),
        date=datetime.fromtimestamp(1, UTC),
        chat=Chat(id=hero.user_id, type="private"),
        from_user=User(id=hero.user_id, is_bot=False, first_name=hero.name),
        text=text,
    ).as_(e.bot)


def state_for(e, actor=0):
    hero = e.heroes[actor]
    return FSMContext(
        storage=e.storage,
        key=StorageKey(bot_id=e.bot.id, chat_id=hero.user_id, user_id=hero.user_id),
    )


async def press(e, text, actor=0, *, number=None, hook=None, join=False):
    msg = message(e, text, actor, number=number)
    event = Update(update_id=msg.message_id, message=msg).as_(e.bot)

    async def handle(event, data):
        if join:
            await combat._join_fight(
                event.message,
                state_for(e, actor),
                content=e.content,
                settings=SETTINGS,
                character=e.heroes[actor],
                flow=replace(e.flow, fight="join:0"),
                characters=e.characters,
                store=e.store,
                locations=e.world,
                location_state=await e.world.state("farhold", 1, now=100),
                storage=e.storage,
                emoji=False,
                now=100,
            )
        elif hook:
            session = await e.store.load(e.session.id)
            won = replace(
                session, state=replace(session.state, outcome=BattleOutcome.DECIDED, winner=0)
            )
            await combat._finish(
                event.message,
                state_for(e, actor),
                e.content,
                SETTINGS,
                won,
                {},
                e.heroes[actor],
                e.flow,
                e.characters,
                e.inventory,
                e.world,
                e.cache,
                e.guilds,
                fault_hook=hook,
            )
        else:
            await combat.fight(
                event.message,
                state_for(e, actor),
                e.content,
                SETTINGS,
                e.characters,
                e.inventory,
                e.world,
                e.cache,
                e.parties,
                e.guilds,
            )

    await e.journal(handle, event, {"bot": e.bot})
    return msg.message_id


async def test_restart_cache_loss_and_long_pause_keep_same_battle_and_screen(battle_env):
    e = battle_env
    await press(e, "/пауза")
    before = await e.store.load(e.session.id)
    await e.redis.flushdb()
    e.cache = PostgresGameplayState(e.pool, PostgresEffectState(e.pool, RedisStateCache(e.redis)))
    e.store = BattleStore(e.cache)
    e.storage = PostgresStorage(e.pool)
    assert (await e.store.load(e.session.id)) == before
    assert await state_for(e).get_state() == Play.combat.state
    assert await e.store.busy(e.heroes[0].id) == e.session.id
    assert (await e.world.engaged_at("farhold", 1, 1, wave=0, now=90000, ttl=3600))[
        0
    ].battle_id == e.session.id
    e.journal = CommandMiddleware(
        dependencies(
            e.characters,
            cache=e.cache,
            world=e.world,
            guilds=e.guilds,
            parties=e.parties,
            inventory=e.inventory,
        ),
        e.reaper,
    )
    await press(e, "/продолжить")
    assert "Бой. Круг" in e.recording.sent[-1].text
    assert (await e.store.load(e.session.id)).version == before.version


async def test_old_button_and_same_message_do_not_take_a_second_turn(battle_env):
    e = battle_env
    active = e.session.state.active.character_id
    actor = next(i for i, hero in enumerate(e.heroes) if hero.id == active)
    number = await press(e, f"Защититься — ход {e.session.version}", actor)
    after = await e.store.load(e.session.id)
    assert after.version == e.session.version + 1
    await press(e, f"Защититься — ход {e.session.version}", actor)
    assert (await e.store.load(e.session.id)) == after
    assert "прежнее действие не выполнено" in e.recording.sent[-1].text
    await press(e, f"Защититься — ход {e.session.version}", actor, number=number)
    assert (await e.store.load(e.session.id)) == after


async def test_stale_save_and_second_battle_cannot_replace_participants(battle_env, content):
    e = battle_env
    updated = replace(e.session, state=replace(e.session.state, round=2))
    await e.store.save(updated)
    with pytest.raises(StaleBattleError):
        await e.store.save(e.session)
    other, _ = begin(
        content, battle_id=f"other-{uuid4()}", attackers=[(e.heroes[0], True)], seed=b"other"
    )
    with pytest.raises(ValueError, match="another battle"):
        await e.store.save(other)
    assert await e.store.load(other.id) is None


@pytest.mark.parametrize(
    "point", ["after_character", "after_world", "after_result", "after_screens", "after_replies"]
)
async def test_failure_during_settlement_rolls_back_every_part_then_pays_once(battle_env, point):
    e = battle_env
    original = await e.store.load(e.session.id)
    original_data = await state_for(e).get_data()

    async def fail(name):
        if name == point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=point):
        await press(e, "закончить", hook=fail)
    assert await e.store.load(e.session.id) == original
    assert await state_for(e).get_data() == original_data
    assert not e.recording.sent
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]] == [500, 500]

    async def accept(name):
        pass

    await press(e, "закончить", hook=accept)
    done = await e.store.load(e.session.id)
    balances = [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]]
    assert done.settled and done.results
    assert all(one > 500 for one in balances)
    await e.redis.flushdb()
    await press(e, "закончить", hook=accept)
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]] == balances
    for actor in range(2):
        await press(e, "/обновить", actor)
        assert e.recording.sent[-1].text.startswith("Победа.")
    assert await e.store.busy(e.heroes[0].id) is None


async def test_roamer_claim_and_release_are_bound_to_exact_encounter(battle_env):
    e = battle_env
    await e.world.spawn_roamer(
        "farhold", 1, Roamer(node=1, group=True, difficulty="recon", level=1, stamp=99), ttl=3600
    )
    assert await e.world.claim_roamer(
        "farhold", 1, e.heroes[0].id, ttl=1800, encounter="old", stamp=99
    )
    assert not await e.world.claim_roamer(
        "farhold", 1, e.heroes[1].id, ttl=1800, encounter="other", stamp=99
    )
    await e.redis.flushdb()
    restarted = PostgresLocationState(e.pool)
    assert (await restarted.roamer("farhold", 1, now=90000)).holder == e.heroes[0].id
    await restarted.release_roamer("farhold", 1, encounter="old")
    assert await restarted.claim_roamer(
        "farhold", 1, e.heroes[1].id, ttl=1800, encounter="new", stamp=99
    )
    await restarted.clear_roamer("farhold", 1, encounter="old")
    await restarted.release_roamer("farhold", 1, encounter="old")
    assert (await restarted.roamer("farhold", 1, now=90000)).holder == e.heroes[1].id
    assert not await restarted.claim_roamer(
        "farhold", 1, e.heroes[1].id, ttl=1800, encounter="stale", stamp=100
    )
    await e.pool.execute(
        "UPDATE gameplay_state SET value=$1 WHERE key='loc:farhold:1:roamer:held'",
        str(e.heroes[1].id),
    )
    await restarted.clear_roamer("farhold", 1, encounter=f"{e.heroes[0].id}:10:99")
    assert (await restarted.roamer("farhold", 1, now=90000)).holder == e.heroes[1].id
    await restarted.release_roamer("farhold", 1, encounter=f"{e.heroes[1].id}:10:99")
    assert (await restarted.roamer("farhold", 1, now=0)).holder == 0


async def test_old_generation_release_cannot_clear_new_pack_claim(battle_env):
    from mmorpg.domain.rules.nodes import location_epoch
    from mmorpg.presentation.telegram.handlers.play import _location_state

    e = battle_env
    await e.world.reset("farhold", 1)
    assert location_epoch(await e.world.state("farhold", 1, now=4000)) == 1
    assert location_epoch(await _location_state(e.content, e.flow, e.world, 4000)) == 1
    await e.redis.flushdb()
    assert location_epoch(await PostgresLocationState(e.pool).state("farhold", 1, now=90000)) == 1
    assert (
        await e.world.engage(
            "farhold",
            1,
            1,
            wave=0,
            place=0,
            epoch=1,
            battle_id="new",
            name="Новый",
            character_id=e.heroes[2].id,
            now=4000,
            ttl=3600,
        )
        is None
    )
    await e.world.disengage("farhold", 1, 1, wave=0, place=0, epoch=0, battle_id=e.session.id)
    new = await e.world.engaged_at("farhold", 1, 1, wave=0, epoch=1, now=90000, ttl=3600)
    assert new[0].battle_id == "new"
    await e.world.disengage("farhold", 1, 1, wave=0, place=0, epoch=1, battle_id="wrong")
    assert await e.world.engaged_at("farhold", 1, 1, wave=0, epoch=1, now=90000, ttl=3600)


async def test_simultaneous_join_turn_and_yield_preserve_all_changes(battle_env):
    e = battle_env
    active = e.session.state.active.character_id
    actor = next(i for i, hero in enumerate(e.heroes) if hero.id == active)
    other = 1 - actor
    await asyncio.gather(
        press(e, "Защититься", actor),
        press(e, "вмешаться", 2, join=True),
        press(e, "Сдаться", other),
    )
    saved = await e.store.load(e.session.id)
    assert len(saved.participants()) == 3
    assert saved.combatant_of(e.heroes[other].id).left
    assert saved.combatant_of(e.heroes[2].id).live
    assert saved.version >= e.session.version + 3
    await e.redis.flushdb()
    assert await e.store.load(e.session.id) == saved


@pytest.mark.parametrize("reset_screen", [False, True])
async def test_actual_dispatcher_routes_returning_player_from_persistent_screen(
    battle_env, reset_screen
):
    from mmorpg.presentation.telegram.handlers import play

    e = battle_env
    await e.redis.flushdb()
    if reset_screen:
        await state_for(e).set_state(Play.main_menu)
        await state_for(e).set_data({})
    dispatcher = Dispatcher(storage=PostgresStorage(e.pool))
    dispatcher.update.outer_middleware.unregister(dispatcher.fsm)
    dispatcher.update.outer_middleware(e.journal)
    dispatcher.update.outer_middleware(dispatcher.fsm)
    dispatcher.include_router(combat.build_router())
    dispatcher.include_router(play.build_router())
    msg = message(e, "/продолжить")
    event = Update(update_id=msg.message_id, message=msg).as_(e.bot)
    await dispatcher.feed_update(
        e.bot,
        event,
        content=e.content,
        settings=SETTINGS,
        characters=e.characters,
        inventory=e.inventory,
        locations=e.world,
        state_cache=e.cache,
        parties=e.parties,
        guilds=e.guilds,
        users=None,
        keeper_log=None,
        overlays=None,
        registry=None,
        trades=None,
    )
    assert "Бой. Круг" in e.recording.sent[-1].text
    assert (await state_for(e).get_data())["play"] == e.flow.serialise()


async def test_useless_bag_item_does_not_decrease_inventory_or_version(battle_env):
    e = battle_env
    item = next(
        one
        for one in e.content.items
        if one.effect and one.effect.kind == "restore_resource_percent"
    )
    active = e.session.state.active.character_id
    actor = next(i for i, hero in enumerate(e.heroes) if hero.id == active)
    await e.inventory.add(e.heroes[actor].id, item.id)
    await press(e, "Сумка", actor)
    await press(e, f"{item.name} — использовать", actor)
    assert await e.inventory.count(e.heroes[actor].id, item.id) == 1
    assert (await e.store.load(e.session.id)).version == e.session.version
    assert "Вещь и ход сохранены" in e.recording.sent[-1].text


async def test_lost_commit_confirmation_restores_one_battle_result(battle_env, monkeypatch):
    from mmorpg.infrastructure.persistence.reconnect import ReconnectingPool

    e = battle_env
    original = ReconnectingPool.acquire
    injected = False

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        @asynccontextmanager
        async def transaction(self):
            nonlocal injected
            async with self.connection.transaction():
                yield
            if not injected:
                injected = True
                raise ConnectionResetError("lost battle commit reply")

    @asynccontextmanager
    async def acquire(pool):
        async with original(pool) as connection:
            yield Connection(connection)

    async def accept(name):
        pass

    with monkeypatch.context() as patch:
        patch.setattr(ReconnectingPool, "acquire", acquire)
        number = await press(e, "закончить", hook=accept)
    done = await e.store.load(e.session.id)
    balances = [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]]
    assert injected and done.settled
    await e.redis.flushdb()
    await press(e, "закончить", number=number, hook=accept)
    assert await e.store.load(e.session.id) == done
    assert [(await e.characters.get(hero.id)).gold for hero in e.heroes[:2]] == balances


async def test_imported_live_legacy_battle_and_screen_survive_cache_loss(battle_env):
    from aiogram.fsm.storage.redis import RedisStorage

    from mmorpg.application.services.battle import serialise
    from mmorpg.infrastructure.cache.operations import TransactionalRedis
    from mmorpg.infrastructure.cache.redis_cache import RedisLocationStateCache

    e = battle_env
    old_id = f"legacy-{uuid4()}"
    legacy = replace(e.session, id=old_id, version=0, content_version="", roster_snapshot="")
    await e.redis.set(f"battle:{old_id}", serialise(legacy), ex=3600)
    await e.redis.set(f"battle-of:{e.heroes[2].id}", old_id, ex=3600)
    key = StorageKey(
        bot_id=e.bot.id,
        chat_id=e.heroes[2].user_id,
        user_id=e.heroes[2].user_id,
        thread_id=e.heroes[0].id,
    )
    old_storage = RedisStorage(TransactionalRedis(e.redis))
    await old_storage.set_state(key, Play.combat)
    await old_storage.set_data(key, {"battle": old_id, "play": e.flow.serialise()})
    old_world = RedisLocationStateCache(e.redis)
    legacy_city = f"legacy-{uuid4().hex}"
    await old_world.take(legacy_city, 1, 2, wave=0, size=3, now=1, ttl=86400)
    await old_world.spawn_roamer(
        legacy_city, 1, Roamer(node=1, group=False, difficulty="recon", level=1, stamp=1), ttl=60
    )
    assert await e.cache.import_legacy(e.redis) >= 5
    await e.redis.flushdb()
    assert (await e.store.load(old_id)).state == legacy.state
    assert await PostgresStorage(e.pool).get_state(key) == Play.combat.state
    assert (await PostgresStorage(e.pool).get_data(key))["battle"] == old_id
    assert (await e.world.state(legacy_city, 1, now=10)).node(2).taken == 1
    assert await e.world.roamer(legacy_city, 1, now=9999999999) is None
    await old_world.take(legacy_city, 1, 2, wave=0, size=3, now=1, ttl=86400)
    await e.world.reset(legacy_city, 1)
    await e.cache.import_legacy(e.redis)
    assert (await e.world.state(legacy_city, 1, now=10)).node(2).taken == 0
    await e.redis.set(f"battle-of:{e.heroes[2].id}", old_id, ex=3600)
    await e.cache.delete(f"battle-of:{e.heroes[2].id}")
    assert not await e.cache.get(f"battle-of:{e.heroes[2].id}")
    await e.pool.execute(
        "DELETE FROM gameplay_state WHERE key=$1 OR key LIKE $2 OR key LIKE $3",
        f"battle:{old_id}",
        f"loc:{legacy_city}:1%",
        f"fsm:{e.heroes[2].user_id}:{e.heroes[2].user_id}:{key.thread_id}:%",
    )


async def test_keeper_cannot_unlock_live_durable_battle(battle_env):
    e = battle_env
    assert not await e.store.free(e.heroes[0].id)
    assert await e.store.busy(e.heroes[0].id) == e.session.id


async def test_yield_can_leave_immediately_and_old_result_does_not_touch_new_battle(battle_env):
    e = battle_env
    active = e.session.state.active.character_id
    actor = next(i for i, hero in enumerate(e.heroes) if hero.id != active and i < 2)
    await press(e, "Сдаться", actor)
    left = await e.store.load(e.session.id)
    assert e.heroes[actor].id in left.departed
    assert await e.store.busy(e.heroes[actor].id) is None
    await press(e, "Назад", actor)
    assert await state_for(e, actor).get_state() != Play.combat.state
    current = await e.characters.get(e.heroes[actor].id)
    other, _ = begin(
        e.content, battle_id=f"new-{uuid4()}", attackers=[(current, True)], seed=b"new"
    )
    await e.store.save(other)

    async def accept(name):
        pass

    await press(e, "закончить", 1 - actor, hook=accept)
    assert (await e.characters.get(current.id)).gold == current.gold
    assert (await e.characters.get(current.id)).health == current.health
    assert await e.store.busy(current.id) == other.id
    await e.pool.execute("DELETE FROM gameplay_state WHERE key=$1", f"battle:{other.id}")
