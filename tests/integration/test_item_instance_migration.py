"""Прежние одинаковые вещи, инструменты и резервы переносятся без потери."""

import asyncio
import json
import os
import sys
from uuid import uuid4

import asyncpg
import pytest
from scripts.backup import database_dsn
from scripts.test_stand import load_test_settings

from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresInventoryRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def migrate(dsn, revision):
    env = dict(os.environ, POSTGRES_DSN=dsn)
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
    output, error = await process.communicate()
    assert process.returncode == 0, (output + error).decode(errors="replace")


@pytest.mark.parametrize("old_revision", ["0030", "0034"])
async def test_old_inventory_equipment_vault_and_multi_item_reserve(old_revision, content):
    dsn = load_test_settings().postgres_dsn
    database = "vellar_instances_" + uuid4().hex + "_test"
    admin = await asyncpg.connect(database_dsn(dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    address = database_dsn(dsn, database)
    try:
        await migrate(address, old_revision)
        db = await asyncpg.connect(address)
        try:
            await db.execute("INSERT INTO users(telegram_id) VALUES(1),(2)")
            a = await db.fetchval(
                "INSERT INTO characters(user_id,name,race_id,class_id,equipment,wear,gold)"
                " VALUES(1,'Первый','human','warrior',$1::jsonb,$2::jsonb,555) RETURNING id",
                json.dumps({"weapon": "sword@1#common", "tool": "pickaxe@1#common"}),
                json.dumps({"sword@1#common": 35, "pickaxe@1#common": 8}),
            )
            b = await db.fetchval(
                "INSERT INTO characters(user_id,name,race_id,class_id)"
                " VALUES(2,'Второй','human','warrior') RETURNING id"
            )
            await db.execute("INSERT INTO inventory VALUES($1,'sword@1#common',2)", a)
            material = next(item.id for item in content.items if item.kind.value == "material")
            await db.execute("INSERT INTO inventory VALUES($1,$2,7)", a, material)
            guild = await db.fetchval(
                "INSERT INTO guilds(name,founder_id) VALUES('Склад',$1) RETURNING id", a
            )
            await db.execute("INSERT INTO guild_items VALUES($1,'sword@1#common',2)", guild)
            await db.execute(
                "INSERT INTO trades(scope,number,kind,price,item_id,item_name,quantity,"
                "author_user_id,author_character_id,author_name,target_user_id,target_character_id,"
                "target_name,created_at) VALUES('old',1,'sell',100,'sword@1#common','Меч',2,"
                "1,$1,'Первый',2,$2,'Второй',1000)",
                a,
                b,
            )
        finally:
            await db.close()
        await migrate(address, "head")
        pool = await asyncpg.create_pool(address, min_size=1, max_size=2)
        try:
            characters = PostgresCharacterRepository(pool)
            inventory = PostgresInventoryRepository(pool, content)
            hero = await characters.get(a)
            assert hero.gold == 555
            assert hero.wear.spent(hero.equipment.item_in("weapon")) == 35
            assert hero.wear.spent(hero.equipment.item_in("tool")) == 8
            held = await inventory.list_items(a)
            swords = [row for row in held if row.item_id.startswith("sword@")]
            assert len(swords) == 2 and all(row.quantity == 1 for row in swords)
            assert all(hero.wear.spent(row.item_id) == 35 for row in swords)
            assert await inventory.count(a, material) == 7
            stock = await pool.fetch(
                "SELECT item_id,quantity FROM guild_items WHERE guild_id=$1", guild
            )
            assert len(stock) == 2 and all(row["quantity"] == 1 for row in stock)
            assert await pool.fetchval("SELECT count(*) FROM item_instances WHERE used=35") == 7
            reserved = await pool.fetchval("SELECT item_id FROM trades WHERE scope='old'")
            # Закрытие старого предложения выдаёт те же два экземпляра, с их износом.
            async with pool.acquire() as connection, connection.transaction():
                await connection.execute("UPDATE trades SET status='declined' WHERE scope='old'")
                raw = PostgresInventoryRepository(connection, content)
                await raw.add(a, reserved, 2)
            reloaded = await characters.get(a)
            held = await inventory.list_items(a)
            assert len([row for row in held if row.item_id.startswith("sword@")]) == 4
            assert all(
                reloaded.wear.spent(row.item_id) == 35
                for row in held
                if row.item_id.startswith("sword@")
            )
            await inventory.add(a, "sword@1#common")
            assert len(await inventory.list_items(a)) == 6
        finally:
            await pool.close()
    finally:
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()
