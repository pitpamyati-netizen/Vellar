"""M02.2: настоящие награды, выемка и счёт после очистки Redis и сбоя."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.config import Settings
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.guild_contract import ContractKind, contracts
from mmorpg.infrastructure.cache.redis_cache import RedisStateCache
from mmorpg.infrastructure.persistence.effects import EFFECT_SCOPES, PostgresEffectState
from mmorpg.infrastructure.persistence.postgres import PostgresGuildRepository
from mmorpg.presentation.telegram import digest_claim
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.handlers.play import _guild_store_step, _guild_vault_step
from tests.integration.test_economic_operations import NOW, SWORD, economy  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
ROTATION = 1800
SETTINGS = Settings(_env_file=None, shop_rotation_seconds=ROTATION)


@pytest_asyncio.fixture(loop_scope="session")
async def effects(economy):  # noqa: F811
    e = economy
    e.cache = PostgresEffectState(e.pool, RedisStateCache(e.redis))
    e.guilds = GuildStore(PostgresGuildRepository(e.pool), e.cache)
    e.guild = await e.guilds.create(f"Меры {uuid4().hex[:10]}", e.heroes[0].id)
    e.foe = await e.guilds.create(f"Камень {uuid4().hex[:10]}", e.heroes[2].id)
    try:
        yield e
    finally:
        await e.guilds.disband(e.guild)
        await e.guilds.disband(e.foe)


@atomic_action
async def pay_digest(characters, cache, content, hero_id, *, broken=False, now=NOW):
    hero = await characters.get(hero_id)
    deeds = digest_rules.digest(content, "test", hero.city_id, now // ROTATION, hero.level)
    paid = await digest_claim.claim(
        cache, content, hero, deeds[0], now=now, rotation_seconds=ROTATION
    )
    if paid is None:
        return False
    await characters.save(paid.character)
    if broken:
        raise RuntimeError("after reward")
    return True


async def test_digest_and_paid_mark_rollback_together_then_pay_once_after_redis_loss(
    effects, content
):
    e = effects
    key = f"digest:{e.heroes[0].id}:{NOW // ROTATION}"
    with pytest.raises(RuntimeError, match="after reward"):
        await pay_digest(e.characters, e.cache, content, e.heroes[0].id, broken=True)
    assert await e.cache.get(key) is None
    assert (await e.characters.get(e.heroes[0].id)).gold == 500
    assert await pay_digest(e.characters, e.cache, content, e.heroes[0].id)
    before = await e.characters.get(e.heroes[0].id)
    await e.redis.flushdb()
    restarted = PostgresEffectState(e.pool, RedisStateCache(e.redis))
    assert not await pay_digest(e.characters, restarted, content, e.heroes[0].id, now=NOW + 301)
    assert (await e.characters.get(e.heroes[0].id)).gold == before.gold
    assert await pay_digest(e.characters, restarted, content, e.heroes[0].id, now=NOW + ROTATION)


async def test_concurrent_new_actions_share_one_digest_reward(effects, content):
    e = effects
    results = await asyncio.gather(
        *(pay_digest(e.characters, e.cache, content, e.heroes[0].id) for _ in range(2))
    )
    assert sorted(results) == [False, True]


@pytest.mark.parametrize("scope", sorted(EFFECT_SCOPES))
async def test_legacy_marks_survive_startup_import_and_cleared_redis(effects, scope):
    e = effects
    key = f"{scope}:{uuid4()}:5"
    await e.redis.set(key, "7", ex=60)
    assert await e.cache.import_legacy() >= 1
    await e.redis.flushdb()
    assert await PostgresEffectState(e.pool, RedisStateCache(e.redis)).get(key) == "7"
    row = await e.pool.fetchrow("SELECT value, operation_id FROM durable_effects WHERE key=$1", key)
    assert row["value"] == "7" and row["operation_id"] is None


async def test_contract_count_and_payment_survive_restart_and_concurrent_completion(
    effects, content
):
    e = effects
    place = guild_rules.standing(content, e.guild)
    deal = next(
        one
        for one in contracts(
            content.guild_tiers,
            place,
            world_seed="test",
            guild_id=e.guild.id,
            rotation=NOW // ROTATION,
        )
        if one.kind is ContractKind.CULL
    )
    options = {
        "guild_id": e.guild.id,
        "place": place,
        "kind": deal.kind,
        "world_seed": "test",
        "now": NOW,
        "rotation_seconds": ROTATION,
    }
    assert await e.guilds.work_on_contract(content, amount=deal.target - 1, **options) is None
    await e.redis.flushdb()
    restarted = GuildStore(
        PostgresGuildRepository(e.pool), PostgresEffectState(e.pool, RedisStateCache(e.redis))
    )
    results = await asyncio.gather(
        restarted.work_on_contract(content, amount=1, **options),
        restarted.work_on_contract(content, amount=1, **options),
    )
    assert sum(one is not None for one in results) == 1
    stored = await restarted.by_id(e.guild.id)
    assert stored.vault_gold == deal.reward_gold
    assert stored.deeds == deal.reward_deeds
    assert (await restarted.contract_progress(e.guild.id, now=NOW, rotation_seconds=ROTATION))[
        deal.kind
    ] == deal.target + 1
    await e.redis.flushdb()
    assert await restarted.work_on_contract(content, amount=deal.target, **options) is None
    assert (await restarted.by_id(e.guild.id)).vault_gold == deal.reward_gold


async def test_withdrawal_limit_survives_redis_loss_and_blocks_a_new_command(effects, content):
    e = effects
    guild = e.guild.with_member(e.heroes[1].id, GuildRank.MEMBER)
    await e.guilds.save(guild)
    await e.guilds.deposit(guild, 10000)
    guild = await e.guilds.by_id(guild.id)
    hero = e.heroes[1]
    limit = guild_rules.withdraw_limit(guild.rank_of(hero.id), hero.level)
    notice, hero = await _guild_vault_step(
        content, hero, "withdraw", str(limit), e.characters, e.guilds, guild, SETTINGS, NOW
    )
    assert "Из казны взято" in notice
    await e.redis.flushdb()
    notice, _ = await _guild_vault_step(
        content,
        hero,
        "withdraw",
        "1",
        e.characters,
        e.guilds,
        await e.guilds.by_id(guild.id),
        SETTINGS,
        NOW + 301,
    )
    assert "Из казны взято" not in notice
    assert (await e.characters.get(hero.id)).gold == 500 + limit


async def test_item_withdrawal_count_survives_a_cleared_cache(effects, content):
    e = effects
    guild = e.guild.with_member(e.heroes[1].id, GuildRank.MEMBER)
    await e.guilds.save(guild)
    await e.guilds.stow(guild.id, SWORD, 100)
    limit = guild_rules.store_take_limit(GuildRank.MEMBER)
    assert limit is not None
    flow = PlayState(vault_action="take", vault_item=SWORD, vault_amount=limit)
    hero = e.heroes[1]
    assert "Из хранилища взято" in await _guild_store_step(
        content, hero, flow, e.inventory, e.guilds, SETTINGS, NOW
    )
    await e.redis.flushdb()
    assert (
        await e.guilds.taken_items(guild.id, hero.id, now=NOW + 301, rotation_seconds=ROTATION)
        == limit
    )
    refusal = await _guild_store_step(
        content, hero, replace(flow, vault_amount=1), e.inventory, e.guilds, SETTINGS, NOW + 301
    )
    assert "Из хранилища взято" not in refusal
    assert await e.inventory.count(hero.id, SWORD) == 1 + limit


async def test_same_war_pair_is_not_scored_twice_after_restart(effects):
    e = effects
    war = await e.guilds.open_war(
        challenger_id=e.guild.id,
        defender_id=e.foe.id,
        stake=10,
        started=NOW // ROTATION,
        ends=NOW // ROTATION + 5,
    )
    options = {
        "guild_id": e.guild.id,
        "winner_id": e.heroes[0].id,
        "loser_id": e.heroes[2].id,
        "now": NOW,
        "rotation_seconds": ROTATION,
    }
    assert await e.guilds.score_war(war, **options)
    await e.redis.flushdb()
    restarted = GuildStore(
        PostgresGuildRepository(e.pool), PostgresEffectState(e.pool, RedisStateCache(e.redis))
    )
    assert not await restarted.score_war(war, **options)
    assert (await restarted.war_of(e.guild.id)).challenger_score == 1


@atomic_action
async def hand_in(characters, inventory, content, hero_id, quest_id, *, broken=False):
    hero = await characters.get(hero_id)
    paid = quest_rules.hand_in(content, hero, content.quest(quest_id))
    if paid is None:
        return False
    await characters.save(paid.character)
    if paid.item_id:
        await inventory.add(hero.id, paid.item_id)
    if broken:
        raise RuntimeError("after quest")
    return True


async def test_quest_completion_and_reward_rollback_and_repeat_after_redis_loss(effects, content):
    e = effects
    quest = next(iter(content.quests))
    if isinstance(quest, str):
        quest = content.quest(quest)
    hero = e.heroes[0]
    log = hero.quests.take(quest.id).advanced(quest.id, quest.target_count)
    hero = await e.characters.save(replace(hero, level=max(hero.level, quest.level), quests=log))
    with pytest.raises(RuntimeError, match="after quest"):
        await hand_in(e.characters, e.inventory, content, hero.id, quest.id, broken=True)
    assert not (await e.characters.get(hero.id)).quests.is_done(quest.id)
    assert (await e.characters.get(hero.id)).gold == hero.gold
    assert await hand_in(e.characters, e.inventory, content, hero.id, quest.id)
    paid = await e.characters.get(hero.id)
    await e.redis.flushdb()
    assert not await hand_in(e.characters, e.inventory, content, hero.id, quest.id)
    assert (await e.characters.get(hero.id)).gold == paid.gold


async def test_contract_payout_failure_rolls_back_count_and_paid_mark(
    effects, content, monkeypatch
):
    e = effects
    place = guild_rules.standing(content, e.guild)
    deal = contracts(
        content.guild_tiers, place, world_seed="test", guild_id=e.guild.id, rotation=NOW // ROTATION
    )[0]
    original = e.guilds._roster.add_deeds

    async def fail_after_deeds(guild_id, amount):
        await original(guild_id, amount)
        raise RuntimeError("after guild reward")

    monkeypatch.setattr(e.guilds._roster, "add_deeds", fail_after_deeds)
    options = {
        "guild_id": e.guild.id,
        "place": place,
        "kind": deal.kind,
        "amount": deal.target,
        "world_seed": "test",
        "now": NOW,
        "rotation_seconds": ROTATION,
    }
    with pytest.raises(RuntimeError, match="after guild reward"):
        await e.guilds.work_on_contract(content, **options)
    guild = await e.guilds.by_id(e.guild.id)
    assert guild.vault_gold == guild.deeds == 0
    assert (await e.guilds.contract_progress(e.guild.id, now=NOW, rotation_seconds=ROTATION))[
        deal.kind
    ] == 0
    assert await e.cache.get(e.guilds._paid_key(e.guild.id, NOW // ROTATION, deal.kind)) is None
    monkeypatch.setattr(e.guilds._roster, "add_deeds", original)
    assert await e.guilds.work_on_contract(content, **options) is not None


async def test_failed_war_score_does_not_consume_pair(effects, monkeypatch):
    e = effects
    war = await e.guilds.open_war(
        challenger_id=e.guild.id,
        defender_id=e.foe.id,
        stake=10,
        started=NOW // ROTATION,
        ends=NOW // ROTATION + 5,
    )
    original = e.guilds._roster.score_war

    async def broken(war_id, guild_id):
        await original(war_id, guild_id)
        raise RuntimeError("after war score")

    monkeypatch.setattr(e.guilds._roster, "score_war", broken)
    options = {
        "guild_id": e.guild.id,
        "winner_id": e.heroes[0].id,
        "loser_id": e.heroes[2].id,
        "now": NOW,
        "rotation_seconds": ROTATION,
    }
    with pytest.raises(RuntimeError, match="after war score"):
        await e.guilds.score_war(war, **options)
    assert (await e.guilds.war_of(e.guild.id)).challenger_score == 0
    key = e.guilds._war_hit_key(war.id, e.heroes[0].id, e.heroes[2].id, NOW // ROTATION)
    assert await e.cache.get(key) is None
    monkeypatch.setattr(e.guilds._roster, "score_war", original)
    results = await asyncio.gather(*(e.guilds.score_war(war, **options) for _ in range(2)))
    assert sorted(results) == [False, True]
