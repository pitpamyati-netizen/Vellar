"""Экземпляры на PostgreSQL: перенос, резерв, повтор, сбой и конкуренция."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.group_trade import GroupResult, GroupTrade, roll_back
from mmorpg.domain.entities.character import Character, Equipment, ItemWear
from mmorpg.domain.entities.item_instance import instance_ids
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules import repair, salvage
from mmorpg.domain.rules.group_commands import parse_group_command
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresGuildRepository,
    PostgresInventoryRepository,
    PostgresPrivacyRepository,
    PostgresTradeRepository,
    PostgresUserRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def physical(pool, redis, content):
    accounts = [-988601, -988602, -988603]
    await pool.execute("DELETE FROM users WHERE telegram_id=ANY($1::bigint[])", accounts)
    pool.economic_cache = redis
    users = PostgresUserRepository(pool)
    characters = PostgresCharacterRepository(pool, content)
    inventory = PostgresInventoryRepository(pool, content)
    trades = PostgresTradeRepository(pool)
    heroes = []
    for account in accounts:
        await users.upsert(User(telegram_id=account))
        heroes.append(
            await characters.create(
                Character(0, account, f"Контроль{abs(account)}", "human", "warrior", gold=10000)
            )
        )
    await inventory.add(heroes[0].id, "sword@1#rare", 2)
    refs = [row.item_id for row in await inventory.list_items(heroes[0].id)]
    heroes[0] = await characters.save(replace(heroes[0], wear=ItemWear({refs[0]: 40, refs[1]: 2})))
    scope = f"physical:{uuid4()}"
    trade = GroupTrade(
        content, characters, inventory, trades, PostgresPrivacyRepository(pool), scope=scope
    )
    env = SimpleNamespace(
        pool=pool,
        characters=characters,
        inventory=inventory,
        trades=trades,
        heroes=heroes,
        refs=refs,
        users=users,
        trade=trade,
        accounts=accounts,
        guilds=PostgresGuildRepository(pool),
    )
    try:
        yield env
    finally:
        await characters.operations.recover()
        await pool.execute("DELETE FROM trades WHERE scope=$1", scope)
        await pool.execute("DELETE FROM users WHERE telegram_id=ANY($1::bigint[])", accounts)


@atomic_action
async def move(
    characters, inventory, giver: int, taker: int, ref: str, operation_id: str, fail: bool = False
) -> bool:
    if not await inventory.remove(giver, ref):
        return False
    if fail:
        raise RuntimeError("controlled stop")
    await inventory.add(taker, ref)
    return True


async def test_new_copy_does_not_inherit_wear_and_saved_recipient_keeps_it(physical):
    e = physical
    a, b = e.refs
    assert await move(
        e.characters, e.inventory, e.heroes[0].id, e.heroes[1].id, a, "move:" + str(uuid4())
    )
    recipient = await e.characters.get(e.heroes[1].id)
    assert recipient.wear.spent(a) == 40
    await e.characters.save(e.heroes[1].with_gold(1))
    assert (await e.characters.get(recipient.id)).wear.spent(a) == 40
    await e.inventory.add(recipient.id, "sword@1#rare")
    newer = next(
        row.item_id for row in await e.inventory.list_items(recipient.id) if row.item_id != a
    )
    assert (await e.characters.get(recipient.id)).wear.spent(newer) == 0
    assert (await e.characters.get(e.heroes[0].id)).wear.spent(b) == 2

    recipient = await e.characters.get(recipient.id)
    await e.characters.save(replace(recipient, wear=ItemWear({a: 50})))
    assert await move(
        e.characters, e.inventory, recipient.id, e.heroes[0].id, a, "return:" + str(uuid4())
    )
    await e.characters.save(e.heroes[0].with_gold(1))
    assert (await e.characters.get(e.heroes[0].id)).wear.spent(a) == 50


async def test_restart_replay_and_failure_keep_one_instance(physical):
    e = physical
    op = "physical:" + str(uuid4())
    with pytest.raises(RuntimeError):
        await move(e.characters, e.inventory, e.heroes[0].id, e.heroes[1].id, e.refs[0], op, True)
    assert await e.inventory.count(e.heroes[0].id, e.refs[0]) == 1
    assert await e.inventory.count(e.heroes[1].id, e.refs[0]) == 0
    op = "physical:" + str(uuid4())
    assert await move(e.characters, e.inventory, e.heroes[0].id, e.heroes[1].id, e.refs[0], op)
    new_characters = PostgresCharacterRepository(e.pool)
    new_inventory = PostgresInventoryRepository(e.pool)
    assert await move(new_characters, new_inventory, e.heroes[0].id, e.heroes[1].id, e.refs[0], op)
    assert await new_inventory.count(e.heroes[1].id, e.refs[0]) == 1
    assert (await new_characters.get(e.heroes[1].id)).wear.spent(e.refs[0]) == 40


async def test_two_receivers_cannot_duplicate_one_item(physical):
    e = physical
    results = await asyncio.gather(
        *[
            move(
                e.characters,
                e.inventory,
                e.heroes[0].id,
                hero.id,
                e.refs[0],
                "race:" + str(uuid4()),
            )
            for hero in e.heroes[1:]
        ]
    )
    assert sorted(results) == [False, True]
    assert sum([await e.inventory.count(hero.id, e.refs[0]) for hero in e.heroes]) == 1


@atomic_action
async def vault_trip(characters, inventory, guilds, hero: Character, ref: str) -> int:
    guild = await guilds.create("Экземпляры" + str(hero.id), hero.id)
    assert await inventory.remove(hero.id, ref)
    await guilds.stow(guild.id, ref, 1)
    assert await guilds.unstow(guild.id, ref, 1)
    await inventory.add(hero.id, ref)
    return guild.id


async def test_vault_round_trip_preserves_wear(physical):
    e = physical
    guild = await vault_trip(e.characters, e.inventory, e.guilds, e.heroes[0], e.refs[0])
    assert (await e.characters.get(e.heroes[0].id)).wear.spent(e.refs[0]) == 40
    await e.pool.execute("DELETE FROM guilds WHERE id=$1", guild)


@atomic_action
async def equip_and_repair(characters, inventory, hero: Character, ref: str, content) -> Character:
    assert await inventory.remove(hero.id, ref)
    hero = replace(hero, equipment=Equipment({"weapon": ref}))
    hero = await characters.save(hero)
    hero = repair.repaired(hero, [content.item(ref)])
    return await characters.save(hero)


async def test_repair_fixes_one_equipped_item(physical, content):
    e = physical
    hero = await equip_and_repair(e.characters, e.inventory, e.heroes[0], e.refs[0], content)
    reloaded = await e.characters.get(hero.id)
    assert reloaded.equipment.item_in("weapon") == e.refs[0]
    assert reloaded.wear.spent(e.refs[0]) == 0
    assert reloaded.wear.spent(e.refs[1]) == 2


@atomic_action
async def reforge(characters, inventory, hero: Character, ref: str, content) -> str:
    from random import Random

    new = salvage.reforged(content, ref, source=Random(1))
    assert await inventory.remove(hero.id, ref)
    await inventory.add(hero.id, new)
    used = dict(hero.wear.used)
    used[new] = used.pop(ref)
    await characters.save(replace(hero, wear=ItemWear(used)))
    return new


async def test_reforge_keeps_identity_and_damage(physical, content):
    e = physical
    new = await reforge(e.characters, e.inventory, e.heroes[0], e.refs[0], content)
    assert instance_ids(new) == instance_ids(e.refs[0])
    assert (await e.characters.get(e.heroes[0].id)).wear.spent(new) == 40


async def test_real_offer_reserve_accept_and_rollback_keep_damage(physical, content):
    e = physical
    name = content.item(e.refs[0]).name
    parsed = parse_group_command(f"продать 100 {name}")
    assert parsed is not None
    result = await e.trade.run(parsed, author_id=e.accounts[0], target_id=e.accounts[1], now=1000)
    assert result.result == GroupResult.OFFER_MADE
    number = result.offer.number
    accepted = await e.trade.run(
        parse_group_command(f"принять {number}"), author_id=e.accounts[1], target_id=None, now=1001
    )
    assert accepted.result == GroupResult.OFFER_ACCEPTED
    assert (await e.characters.get(e.heroes[1].id)).wear.spent(e.refs[0]) == 40
    record = (await e.trades.journal(e.heroes[0].id))[0]
    await roll_back(record.id, trades=e.trades, characters=e.characters, inventory=e.inventory)
    assert (await e.characters.get(e.heroes[0].id)).wear.spent(e.refs[0]) == 40


async def test_sql_rejects_the_same_instance_in_two_locations(physical):
    e = physical
    with pytest.raises(Exception, match=r"multiple owners|duplicate|уникаль"):
        await e.pool.execute(
            "UPDATE characters SET equipment=$2::jsonb WHERE id=$1",
            e.heroes[1].id,
            '{"weapon":"' + e.refs[0] + '"}',
        )
    assert (await e.characters.get(e.heroes[1].id)).equipment.item_in("weapon") is None
