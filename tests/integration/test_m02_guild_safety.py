"""Роспуск, возвращённое золото, сроки и прежняя схема на отдельной SQL-базе."""

import asyncio
import os
import sys
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest
from scripts.test_stand import load_test_settings

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.guild_safety import disband_token, dissolve
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import guild as rules
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.guild_contract import ContractKind
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import PostgresEffectState
from mmorpg.infrastructure.persistence.postgres import PostgresGuildRepository
from mmorpg.presentation.telegram.flows import play
from mmorpg.presentation.telegram.flows.state import Goods, PlayState
from mmorpg.presentation.telegram.handlers.play import _apply, _guild_vault_step
from mmorpg.presentation.telegram.screens import items
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.integration.test_durable_effects import effects as _effects
from tests.integration.test_economic_operations import NOW, SWORD
from tests.integration.test_economic_operations import economy as _economy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
SETTINGS = Settings(_env_file=None)
effects = _effects
economy = _economy


async def token(e, guild=None):
    guild = await e.guilds.by_id((guild or e.guild).id)
    return disband_token(guild, await e.guilds.stock(guild.id))


async def dissolve_ours(e, *, expected=None):
    return await dissolve(
        e.guilds,
        e.characters,
        e.inventory,
        e.guild.id,
        actor_id=e.heroes[0].id,
        expected=expected or await token(e),
    )


@atomic_action
async def vault(
    characters, guilds, content, actor_id, command, *, now=NOW, operation_id=None, broken=False
) -> tuple[str, Character]:
    action, amount = command.split()
    actor = await characters.get(actor_id)
    result = await _guild_vault_step(
        content, actor, action, amount, characters, guilds, await guilds.of(actor.id), SETTINGS, now
    )
    if broken:
        raise RuntimeError("after contribution")
    return result


async def move(e, content, action, amount, actor=0, **kwargs):
    return await vault(
        e.characters, e.guilds, content, e.heroes[actor].id, f"{action} {amount}", **kwargs
    )


async def test_dissolution_transfers_all_property_preserves_history_and_repeats_safely(effects):
    e = effects
    await e.guilds.deposit(e.guild, 123)
    await e.guilds.stow(e.guild.id, SWORD, 4)
    guild = await e.guilds.by_id(e.guild.id)
    expected = await token(e)
    results = await asyncio.gather(*(dissolve_ours(e, expected=expected) for _ in range(2)))
    assert sum("переданы" in one for one in results) == 1
    assert (await e.characters.get(e.heroes[0].id)).gold == 623
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 5
    assert await e.guilds.by_id(e.guild.id) is None
    archive = await e.pool.fetchrow(
        "SELECT disbanded, archived_members, vault_gold FROM guilds WHERE id=$1", e.guild.id
    )
    assert (
        archive["disbanded"] and archive["archived_members"] != "[]" and archive["vault_gold"] == 0
    )
    with pytest.raises(ValueError, match="no longer active"):
        await e.guilds.save(guild)
    recreated = await e.guilds.create(guild.name, e.heroes[0].id)
    assert recreated.id != guild.id


@pytest.mark.parametrize("side", ["challenger", "defender"])
async def test_active_war_blocks_dissolution_on_both_sides_and_retains_stakes(
    effects, content, side
):
    e = effects
    for guild in (e.guild, e.foe):
        await e.guilds.deposit(guild, 300)
        assert await e.guilds.withdraw(guild, 300)
    war = await e.guilds.open_war(
        challenger_id=e.guild.id,
        defender_id=e.foe.id,
        stake=300,
        started=NOW,
        ends=NOW + 259200,
        clock_seconds=True,
    )
    guild, actor = (e.guild, e.heroes[0]) if side == "challenger" else (e.foe, e.heroes[2])
    result = await dissolve(
        e.guilds,
        e.characters,
        e.inventory,
        guild.id,
        actor_id=actor.id,
        expected=await token(e, guild),
    )
    assert "воюет" in result
    assert await e.guilds.war_of(guild.id) is not None
    assert (await e.characters.get(actor.id)).gold == 500
    settlements = await asyncio.gather(
        *(e.guilds.settle_war(content, war, NOW + 259200) for _ in range(2))
    )
    assert sum(one is not None for one in settlements) == 1
    assert (await e.guilds.by_id(e.guild.id)).vault_gold == 300
    assert (await e.guilds.by_id(e.foe.id)).vault_gold == 300
    assert "переданы" in await dissolve(
        e.guilds,
        e.characters,
        e.inventory,
        guild.id,
        actor_id=actor.id,
        expected=await token(e, guild),
    )
    assert await e.pool.fetchval("SELECT over FROM guild_wars WHERE id=$1", war.id)


async def test_dissolution_failure_restores_hero_stock_vault_and_confirmation(effects, monkeypatch):
    e = effects
    await e.guilds.deposit(e.guild, 77)
    await e.guilds.stow(e.guild.id, SWORD, 2)
    expected = await token(e)
    original = e.inventory.add

    async def fail_after_add(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("after stock transfer")

    monkeypatch.setattr(e.inventory, "add", fail_after_add)
    with pytest.raises(RuntimeError, match="after stock"):
        await dissolve_ours(e, expected=expected)
    assert (await e.guilds.by_id(e.guild.id)).vault_gold == 77
    assert await e.guilds.stock(e.guild.id) == ((SWORD, 2),)
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 1
    monkeypatch.setattr(e.inventory, "add", original)
    assert "переданы" in await dissolve_ours(e, expected=expected)


async def test_settlement_racing_with_dissolution_keeps_both_stakes(effects, content):
    e = effects
    war = await e.guilds.open_war(
        challenger_id=e.guild.id,
        defender_id=e.foe.id,
        stake=300,
        started=NOW,
        ends=NOW + 259200,
        clock_seconds=True,
    )
    expected = await token(e)
    await asyncio.gather(
        e.guilds.settle_war(content, war, NOW + 259200),
        dissolve_ours(e, expected=expected),
    )
    assert await e.pool.fetchval("SELECT over FROM guild_wars WHERE id=$1", war.id)
    assert (await e.guilds.by_id(e.foe.id)).vault_gold == 300
    guild = await e.guilds.by_id(e.guild.id)
    assert (await e.characters.get(e.heroes[0].id)).gold + (guild.vault_gold if guild else 0) == 800
    if guild:
        assert "переданы" in await dissolve_ours(e)
    assert (await e.characters.get(e.heroes[0].id)).gold == 800


@atomic_action
async def purchase(
    characters,
    inventory,
    users,
    content,
    actor_id,
    *,
    quoted=100,
    current=100,
    available=True,
    operation_id=None,
) -> tuple[str, Character]:
    actor = await characters.get(actor_id)
    item = content.item(SWORD)
    state = replace(
        PlayState().at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM), item_id=SWORD, shop_price=quoted
    )
    step = play.advance(
        content,
        actor,
        state,
        items.BUY.text,
        world_seed="m02",
        goods=Goods(gold=actor.gold, stock=(item,) if available else (), prices={SWORD: current}),
    )
    changed = await _apply(step.pending, actor, actor.user_id, characters, inventory, users)
    return step.notice, changed


async def test_sql_purchase_checks_quote_rolls_back_failure_and_repeats_once(
    economy, content, monkeypatch
):
    e = economy

    async def buy(**kwargs):
        return await purchase(e.characters, e.inventory, e.users, content, e.heroes[0].id, **kwargs)

    for changes in ({"available": False}, {"quoted": 50}, {"quoted": 150}, {"quoted": None}):
        await buy(**changes)
        assert (await e.characters.get(e.heroes[0].id)).gold == 500
        assert await e.inventory.count(e.heroes[0].id, SWORD) == 1
    operation_id = str(uuid4())
    original = e.inventory.add

    async def fail_after_add(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("after purchase")

    monkeypatch.setattr(e.inventory, "add", fail_after_add)
    with pytest.raises(RuntimeError, match="after purchase"):
        await buy(operation_id=operation_id)
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 1
    # После отмены повторяем ту же команду с тем же содержанием; сбой был внешним.
    monkeypatch.setattr(e.inventory, "add", original)
    await buy(operation_id=operation_id)
    assert (await e.characters.get(e.heroes[0].id)).gold == 400
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 2
    results = await asyncio.gather(*(buy(operation_id=str(uuid4())) for _ in range(2)))
    assert len(results) == 2
    assert (await e.characters.get(e.heroes[0].id)).gold == 200
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 4
    replay_id = str(uuid4())
    repeated = await asyncio.gather(*(buy(operation_id=replay_id) for _ in range(2)))
    assert repeated[0] == repeated[1]
    assert (await e.characters.get(e.heroes[0].id)).gold == 100
    assert await e.inventory.count(e.heroes[0].id, SWORD) == 5


async def test_changed_stock_or_founder_invalidates_prior_confirmation(effects):
    e = effects
    expected = await token(e)
    await e.guilds.stow(e.guild.id, SWORD, 1)
    assert "изменились" in await dissolve_ours(e, expected=expected)
    guild = (await e.guilds.by_id(e.guild.id)).with_member(e.heroes[1].id, GuildRank.ELDER)
    await e.guilds.save(guild.succeeded_by(e.heroes[1].id))
    assert "основатель" in await dissolve_ours(e, expected=await token(e))
    assert await e.guilds.stock(e.guild.id) == ((SWORD, 1),)


async def test_deposit_withdraw_cycle_cannot_repeat_growth_after_restart_or_new_period(
    effects, content
):
    e = effects
    await e.characters.save(replace(e.heroes[0], level=20))
    await move(e, content, "deposit", 66)
    initial = await e.guilds.by_id(e.guild.id)
    for count in range(4):
        await move(e, content, "withdraw", 66)
        await e.redis.flushdb()
        e.guilds = GuildStore(
            PostgresGuildRepository(e.pool), PostgresEffectState(e.pool, RedisStateCache(e.redis))
        )
        await move(e, content, "deposit", 66, now=NOW + count * SETTINGS.guild_contract_seconds)
        current = await e.guilds.by_id(e.guild.id)
        assert current.deeds == initial.deeds
        assert current.contributed_by(e.heroes[0].id) == initial.contributed_by(e.heroes[0].id)
    assert (await e.characters.get(e.heroes[0].id)).gold == 434
    assert (await e.guilds.by_id(e.guild.id)).vault_gold == 66


async def test_live_legacy_redis_periods_are_bridged_before_new_commands(effects):
    e = effects
    gid, actor = e.guild.id, e.heroes[0].id
    legacy = {
        f"guild-taken:{gid}:{actor}:49": "100",
        f"guild-taken:{gid}:{actor}:50": "17",
        f"guild-items:{gid}:{actor}:50": "2",
        f"guild-contract:{gid}:50:cull": "99",
        f"guild-contract-paid:{gid}:50:cull": "1",
        f"guild-war-hit:{gid}:{actor}:99:50": "1",
    }
    for key, value in legacy.items():
        await e.redis.set(key, value, ex=60)
    await e.cache.import_legacy()
    await e.redis.flushdb()
    cache = PostgresEffectState(e.pool, RedisStateCache(e.redis))
    resumed = GuildStore(PostgresGuildRepository(e.pool), cache)
    assert await resumed.taken(gid, actor, now=NOW, rotation_seconds=86400) == 17
    assert await resumed.taken_items(gid, actor, now=NOW, rotation_seconds=86400) == 2
    assert (await resumed.contract_progress(gid, now=NOW, rotation_seconds=604800))[
        ContractKind.CULL
    ] == 99
    assert not await resumed.claim_contract(
        gid, ContractKind.CULL, now=NOW, rotation_seconds=604800
    )
    assert await cache.get(f"guild-war-hit-v2:{gid}:{actor}:99:0") == "1"
    assert await cache.get(f"guild-period:{gid}:contract:seed") == "50"


async def test_another_member_and_a_new_founder_cannot_launder_withdrawn_gold(effects, content):
    e = effects
    await e.characters.save(replace(e.heroes[0], level=20))
    await e.characters.save(replace(e.heroes[1], level=20))
    await e.guilds.save(e.guild.with_member(e.heroes[1].id, GuildRank.MEMBER))
    await move(e, content, "deposit", 66)
    before = await e.guilds.by_id(e.guild.id)
    await move(e, content, "withdraw", 66, actor=1)
    await e.guilds.save((await e.guilds.by_id(e.guild.id)).succeeded_by(e.heroes[1].id))
    await move(e, content, "deposit", 66, actor=1)
    assert (await e.guilds.by_id(e.guild.id)).deeds == before.deeds
    await move(e, content, "deposit", 66, actor=1)
    assert (await e.guilds.by_id(e.guild.id)).deeds == before.deeds + rules.deeds_for_deposit(
        66, 20
    )


async def test_contribution_and_counter_rollback_together_then_command_replays(effects, content):
    e = effects
    before = await e.characters.get(e.heroes[0].id)
    operation = str(uuid4())
    with pytest.raises(RuntimeError, match="after contribution"):
        await move(e, content, "deposit", 66, operation_id=operation, broken=True)
    assert (await e.characters.get(before.id)).gold == before.gold
    assert (await e.guilds.by_id(e.guild.id)).vault_gold == 0
    assert await e.pool.fetchval("SELECT recycled_gold FROM guilds WHERE id=$1", e.guild.id) == 0
    first = await move(e, content, "deposit", 66, operation_id=operation)
    await e.redis.flushdb()
    second = await move(e, content, "deposit", 66, operation_id=operation, now=NOW + 1000)
    assert first == second
    assert (await e.guilds.by_id(e.guild.id)).vault_gold == 66


async def test_active_contract_terms_and_limits_survive_configuration_and_level_changes(
    effects, content
):
    e = effects
    place = rules.standing(content, e.guild)
    first = await e.guilds.current_contracts(
        content, place, guild_id=e.guild.id, world_seed="test", now=NOW, seconds=604800
    )
    await e.guilds.note_taken(e.guild.id, e.heroes[0].id, 30, now=NOW, rotation_seconds=86400)
    await e.guilds.note_taken_items(e.guild.id, e.heroes[0].id, 4, now=NOW, rotation_seconds=86400)
    await e.redis.flushdb()
    await e.guilds.add_deeds(e.guild.id, 10000)
    newer = rules.standing(content, await e.guilds.by_id(e.guild.id))
    second = await e.guilds.current_contracts(
        content, newer, guild_id=e.guild.id, world_seed="changed", now=NOW + 1800, seconds=60
    )
    assert first == second
    assert (
        await e.guilds.taken(e.guild.id, e.heroes[0].id, now=NOW + 1800, rotation_seconds=60) == 30
    )
    assert (
        await e.guilds.taken_items(e.guild.id, e.heroes[0].id, now=NOW + 1800, rotation_seconds=60)
        == 4
    )
    assert (await e.guilds.period(e.guild.id, "limits", now=NOW + 86400, seconds=60))[
        1
    ] == NOW + 86460
    assert (
        await e.guilds.taken(e.guild.id, e.heroes[0].id, now=NOW + 86400, rotation_seconds=60) == 0
    )


@pytest.mark.parametrize("old_revision", ["0030", "0033"])
async def test_old_schema_preserves_guild_property_stakes_and_paid_contract(old_revision, content):
    settings = load_test_settings()
    server_dsn = settings.postgres_dsn.rsplit("/", 1)[0]
    name = f"vellar_m02_{uuid4().hex}_test"
    admin = await asyncpg.connect(server_dsn + "/postgres")
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = server_dsn + "/" + name

    async def upgrade(revision):
        env = {**os.environ, "POSTGRES_DSN": dsn}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            revision,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode(errors="replace")

    try:
        await upgrade(old_revision)
        db = await asyncpg.connect(dsn)
        await db.execute("""
            INSERT INTO users (telegram_id) VALUES (1), (2);
            INSERT INTO characters (user_id, name, race_id, class_id, gold) VALUES
            (1,'ControlA','human','warrior',731),(2,'ControlB','human','warrior',219);
            INSERT INTO guilds (name,founder_id,vault_gold,deeds) VALUES
            ('ControlA',1,123,7),('ControlB',2,234,9);
            INSERT INTO guild_members (guild_id,character_id,rank,contributed) VALUES
            (1,1,4,7),(2,2,4,9);
            INSERT INTO guild_items VALUES (1,'sword@1#common',3);
            INSERT INTO guild_wars (challenger_id,defender_id,stake,started,ends) VALUES
            (1,2,300,50,53);
        """)
        if old_revision == "0033":
            await db.execute(
                "INSERT INTO durable_effects (key,value) VALUES "
                "('guild-contract:1:50:cull','99'),('guild-contract-paid:1:50:cull','1'),('guild-taken:1:1:50','17'),('guild-items:1:1:50','2')"
            )
        await db.close()
        await upgrade("head")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        assert pool is not None
        try:
            store = GuildStore(
                PostgresGuildRepository(pool), PostgresEffectState(pool, InMemoryStateCache())
            )
            assert await store.stock(1) == ((SWORD, 3),)
            assert not await store.disband(await store.by_id(1))
            assert await pool.fetchval("SELECT gold FROM characters WHERE id=1") == 731
            war = await store.timed_war(1, now=1000000, legacy_seconds=900, duration=259200)
            assert war.started == 45000 and war.ends >= 1259200
            assert (
                await store.timed_war(1, now=1001800, legacy_seconds=1800, duration=10)
            ).ends == war.ends
            if old_revision == "0033":
                assert not await store.claim_contract(
                    1, ContractKind.CULL, now=1000000, rotation_seconds=604800
                )
                assert await store.taken(1, 1, now=1000000, rotation_seconds=86400) == 17
                assert await store.taken_items(1, 1, now=1000000, rotation_seconds=86400) == 2
        finally:
            await pool.close()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
