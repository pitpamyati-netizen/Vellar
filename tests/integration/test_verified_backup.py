"""Новая копия сверяется с её снимком, ошибки не удаляют прежнюю годную."""

import asyncio
from pathlib import Path

import asyncpg
import pytest
from scripts import backup
from scripts.test_stand import load_test_settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_good_backup_failure_and_unverified_copy_preserve_previous(
    pool, tmp_path, monkeypatch
):
    dsn = load_test_settings().postgres_dsn
    previous = await backup.create_backup(dsn, tmp_path, keep=1)
    proof = previous.with_suffix(".verified.json")
    assert proof.exists()
    original = backup.run_tool

    async def corrupt(tool, address, args, file, *, restore):
        if not restore:
            file.write_bytes(b"bad dump")
        else:
            await original(tool, address, args, file, restore=restore)

    monkeypatch.setattr(backup, "run_tool", corrupt)
    with pytest.raises(RuntimeError):
        await backup.create_backup(dsn, tmp_path, keep=1)
    assert previous.exists() and proof.exists()
    monkeypatch.setattr(backup, "run_tool", original)
    unverified = await backup.create_backup(dsn, tmp_path, keep=1, verify=False)
    assert previous.exists() and unverified.exists()
    assert not unverified.with_suffix(".verified.json").exists()


async def test_concurrent_backup_is_refused_without_removing_good_copy(pool, tmp_path):
    dsn = load_test_settings().postgres_dsn
    previous = await backup.create_backup(dsn, tmp_path, keep=1)
    async with backup.backup_lock(dsn):
        with pytest.raises(RuntimeError, match="Другая"):
            await backup.create_backup(dsn, tmp_path, keep=1)
    assert previous.exists()


async def test_new_account_during_dump_is_not_a_false_failure(pool, tmp_path, monkeypatch):
    dsn = load_test_settings().postgres_dsn
    original = backup.run_tool
    account = -988699
    await pool.execute("DELETE FROM users WHERE telegram_id=$1", account)

    async def create_while_dumping(tool, address, args, file, *, restore):
        if not restore:
            await pool.execute("INSERT INTO users(telegram_id) VALUES($1)", account)
            await pool.execute(
                "INSERT INTO characters(user_id,name,race_id,class_id,gold)"
                " VALUES($1,'Снимок','human','warrior',321)",
                account,
            )
        await original(tool, address, args, file, restore=restore)

    monkeypatch.setattr(backup, "run_tool", create_while_dumping)
    try:
        file = await backup.create_backup(dsn, tmp_path, keep=1)
        assert file.with_suffix(".verified.json").exists()
        assert await pool.fetchval("SELECT 1 FROM users WHERE telegram_id=$1", account)
    finally:
        await pool.execute("DELETE FROM users WHERE telegram_id=$1", account)


async def test_equal_row_counts_with_changed_value_fail_verification(pool, tmp_path, monkeypatch):
    dsn = load_test_settings().postgres_dsn
    previous = await backup.create_backup(dsn, tmp_path, keep=1)
    original = backup.run_tool

    async def damage_value(tool, address, args, file, *, restore):
        await original(tool, address, args, file, restore=restore)
        if restore:
            connection = await asyncpg.connect(address)
            try:
                # Даже при пустой таблице игроков постоянная версия схемы существует.
                await connection.execute("UPDATE alembic_version SET version_num='bad'")
            finally:
                await connection.close()

    monkeypatch.setattr(backup, "run_tool", damage_value)
    with pytest.raises(RuntimeError, match="отличаются"):
        await backup.create_backup(dsn, tmp_path, keep=1)
    assert previous.exists()


@pytest.mark.parametrize("keep", [0, -5])
async def test_invalid_retention_is_rejected_before_connecting(tmp_path, keep):
    with pytest.raises(ValueError):
        await backup.create_backup("postgresql://unused@127.0.0.1:1/unused", tmp_path, keep=keep)
    assert await asyncio.to_thread(lambda: list(Path(tmp_path).iterdir())) == []
