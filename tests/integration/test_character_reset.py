"""Новая раса и класс в SQL: откат, повтор, конкуренция и незакрытые резервы."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import OperationKeyConflictError, atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.character_reset import GOALS, CharacterReset
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.market import CODEC, KEY
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.long_goal import GoalState
from mmorpg.domain.entities.market import CraftOrder, Lot, MarketState
from mmorpg.domain.entities.trade import Offer, OfferKind, Party, TradeStatus
from mmorpg.domain.ports.repositories import User
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresGuildRepository,
    PostgresInventoryRepository,
    PostgresPartyRepository,
    PostgresTradeRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.handlers.character_reset import KEY as RESET_KEY
from mmorpg.presentation.telegram.handlers.character_reset import begin_selection
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play
from tests.application.test_character_reset import assert_fresh, draft_for, rich_hero
from tests.integration.test_combat_entry import combat_app as combat_app
from tests.integration.test_combat_entry import feed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def restart_sql(pool, content):
    account = 8_000_000_000 + uuid4().int % 1_000_000_000
    name = f"Сброс{account}"
    await PostgresUserRepository(pool).upsert(User(telegram_id=account))
    characters = PostgresCharacterRepository(pool, content)
    inventory = PostgresInventoryRepository(pool, content)
    cache = PostgresGameplayState(pool, InMemoryStateCache())
    trades = PostgresTradeRepository(pool)
    parties = PartyStore(PostgresPartyRepository(pool), cache)
    guilds = GuildStore(PostgresGuildRepository(pool), cache)
    hero = await characters.create(rich_hero(account, name))
    await PostgresUserRepository(pool).upsert(User(telegram_id=account + 1))
    other = await characters.create(rich_hero(account + 1, f"Другой{account}"))
    await inventory.add(hero.id, "iron_scrap", 4)
    await inventory.add(hero.id, "sword@1#common")
    hero = await characters.get(hero.id)
    await cache.set(
        f"long-goal:hero:{hero.id}",
        GOALS.dump_json(
            GoalState(trial=True, crafted=3, studied=True, claimed=("craft",))
        ).decode(),
        10**12,
    )
    e = SimpleNamespace(
        characters=characters,
        inventory=inventory,
        cache=cache,
        trades=trades,
        parties=parties,
        guilds=guilds,
        hero=hero,
        other=other,
        draft=draft_for(content, name),
        service=CharacterReset(content, characters, inventory, cache, trades, parties, guilds),
    )
    await pool.execute("DELETE FROM gameplay_state WHERE key=$1", KEY)
    try:
        yield e
    finally:
        await pool.execute("DELETE FROM gameplay_state WHERE key=$1", KEY)
        await pool.execute(
            "DELETE FROM trades WHERE author_character_id=$1 OR target_character_id=$1", hero.id
        )
        await pool.execute("DELETE FROM users WHERE telegram_id=$1", account)
        await pool.execute("DELETE FROM users WHERE telegram_id=$1", account + 1)
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key=ANY($1::text[])",
            [KEY, f"long-goal:hero:{hero.id}", f"long-goal:offer:{hero.id}"],
        )


async def test_sql_new_race_class_assets_and_restart_replay(restart_sql, content):
    e = restart_sql
    key = "reset:" + uuid4().hex
    fresh, notice = await e.service.confirm(
        e.hero.user_id, e.hero.revision, e.draft, operation_id=key
    )
    assert fresh is not None, notice
    await assert_fresh(e, content, await e.characters.get(e.hero.id))
    changed = await e.characters.save(fresh.with_gold(13))
    # Новый адаптер и пустой временный кэш всё равно находят старый результат.
    new = CharacterReset(
        content,
        e.characters,
        e.inventory,
        PostgresGameplayState(e.characters._pool.raw, InMemoryStateCache()),
        e.trades,
        e.parties,
        e.guilds,
    )
    replay, _ = await new.confirm(e.hero.user_id, e.hero.revision, e.draft, operation_id=key)
    assert replay == fresh and await e.characters.get(e.hero.id) == changed
    with pytest.raises(OperationKeyConflictError):
        await new.confirm(
            e.hero.user_id, e.hero.revision, replace(e.draft, class_id="warrior"), operation_id=key
        )


async def test_sql_failure_rolls_back_character_inventory_goals_and_starters(restart_sql, pool):
    e = restart_sql
    items = await e.inventory.list_items(e.hero.id)
    count = await pool.fetchval("SELECT count(*) FROM item_instances")
    goals = await e.cache.get(f"long-goal:hero:{e.hero.id}")

    @atomic_action
    async def fail(service) -> None:
        assert (await service.confirm(e.hero.user_id, e.hero.revision, e.draft))[0]
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled failure"):
        await fail(e.service)
    assert await e.characters.get(e.hero.id) == e.hero
    assert await e.inventory.list_items(e.hero.id) == items
    assert await e.cache.get(f"long-goal:hero:{e.hero.id}") == goals
    assert await pool.fetchval("SELECT count(*) FROM item_instances") == count


async def test_sql_two_concurrent_confirmations_do_not_reset_twice(restart_sql):
    e = restart_sql
    results = await asyncio.gather(
        *(e.service.confirm(e.hero.user_id, e.hero.revision, e.draft) for _ in range(2))
    )
    assert sum(hero is not None for hero, _ in results) == 1
    assert (await e.characters.get(e.hero.id)).revision == e.hero.revision + 1


@pytest.mark.parametrize("owner", ["author", "target"])
async def test_sql_pending_offer_in_any_group_blocks_then_closed_allows(restart_sql, owner):
    e = restart_sql
    other = Party(e.other.user_id, e.other.id, e.other.name)
    actor = Party(e.hero.user_id, e.hero.id, e.hero.name)
    record = await e.trades.open(
        Offer(
            0,
            OfferKind.SELL,
            actor if owner == "author" else other,
            other if owner == "author" else actor,
            "iron_scrap",
            "Железо",
            20,
        ),
        scope="reset:" + uuid4().hex,
    )
    assert await e.trades.has_pending(e.hero.id)
    assert (await e.service.confirm(e.hero.user_id, e.hero.revision, e.draft))[0] is None
    await e.trades.close(
        record.number, scope=record.scope, status=TradeStatus.DECLINED, settled_at=1
    )
    assert not await e.trades.has_pending(e.hero.id)
    assert (await e.service.confirm(e.hero.user_id, e.hero.revision, e.draft))[0]


@pytest.mark.parametrize("kind", ["lot", "reservation", "order"])
async def test_sql_market_escrow_blocks_reset(restart_sql, kind):
    e = restart_sql
    state = MarketState()
    if kind == "order":
        state = replace(state, orders=(CraftOrder(1, e.hero.id, "smith_ingot@1", 20, 99999),))
    else:
        state = replace(
            state,
            lots=(
                Lot(
                    1,
                    e.hero.id if kind == "lot" else 999,
                    "iron_scrap",
                    1,
                    20,
                    99999,
                    status="open" if kind == "lot" else "reserved",
                    buyer_id=e.hero.id if kind == "reservation" else 0,
                ),
            ),
        )
    await e.cache.set(KEY, CODEC.dump_json(state).decode(), 10**12)
    assert (await e.service.confirm(e.hero.user_id, e.hero.revision, e.draft))[0] is None
    assert await e.characters.get(e.hero.id) == e.hero


async def test_reset_through_built_sql_application_and_duplicate_command(combat_app):
    app, deps, hero, state, recording = combat_app
    await state.set_state(Play.main_menu)
    await feed(app, recording, hero, "/сброс", 810)
    await feed(app, recording, hero, "/сброс выбрать", 811)
    data = await state.get_data()
    saved = data[RESET_KEY]
    choice = replace(
        begin_selection(hero),
        screen=ScreenId.CREATE_CONFIRM,
        draft=draft_for(app.content, hero.name),
    )
    saved["flow"] = choice.serialise()
    await state.update_data({RESET_KEY: saved})
    event, response = await feed(app, recording, hero, "/сброс подтвердить", 812)
    fresh = await deps.characters.get(hero.id)
    assert (fresh.level, fresh.race_id, fresh.class_id, fresh.gold) == (1, "dwarf", "mage", 0)
    assert "Обучение повторно не включается" in response.text
    await app.dispatcher.feed_update(app.bot, event)
    assert await deps.characters.get(hero.id) == fresh


async def test_current_battle_blocks_reset_through_actual_application(combat_app):
    app, deps, hero, state, recording = combat_app
    flow = PlayState(
        screen=ScreenId.DUNGEON_PICK, city_id="farhold", dungeon_pick="farhold_first_adit"
    )
    await state.set_state(Play.dungeon_pick)
    await state.update_data(play=flow.serialise())
    await feed(app, recording, hero, "Разведка", 820)
    store = BattleStore(deps.state_cache)
    battle = await store.busy(hero.id)
    assert battle
    snapshot = await store.load(battle)
    _, response = await feed(app, recording, hero, "/сброс", 821)
    assert "Сначала завершите текущий бой" in response.text
    assert await store.load(battle) == snapshot
    assert (await deps.characters.get(hero.id)).level == 10
    assert not (await state.get_data()).get(RESET_KEY)
