"""Копия одного согласованного снимка; проверка раньше удаления старых копий."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from mmorpg.config import load_settings


def database_dsn(dsn: str, database: str) -> str:
    """Менять только имя базы, сохраняя параметры соединения и пароль."""
    url = urlsplit(dsn)
    if url.scheme not in {"postgres", "postgresql"} or not url.hostname or not url.path:
        raise ValueError("Неверный адрес PostgreSQL")
    if url.fragment or not database or len(database.encode()) > 63:
        raise ValueError("Неверное имя проверочной базы")
    return urlunsplit(url._replace(path=f"/{database}"))


def pg_environment(dsn: str) -> dict[str, str]:
    url = urlsplit(dsn)
    database_dsn(dsn, "postgres")
    env = dict(os.environ)
    env.update(
        PGHOST=url.hostname or "",
        PGPORT=str(url.port or 5432),
        PGUSER=unquote(url.username or ""),
        PGPASSWORD=unquote(url.password or ""),
        PGDATABASE=unquote(url.path[1:]),
        PGCONNECT_TIMEOUT="10",
    )
    # libpq разбирает параметры через URI; пароль остаётся только в окружении.
    env["PGDATABASE"] = urlunsplit(url._replace(netloc=url.netloc.split("@")[-1]))
    return env


def tool_command(tool: str, dsn: str) -> list[str]:
    if shutil.which(tool):
        return [tool, "--dbname", pg_environment(dsn)["PGDATABASE"]]
    # Compose публикует SQL на loopback. Инструменты можно взять из его образа.
    url = urlsplit(dsn)
    if (
        shutil.which("docker")
        and url.hostname in {"localhost", "127.0.0.1"}
        and (url.port or 5432) == 5432
    ):
        return [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "PGPASSWORD",
            "postgres",
            tool,
            "-h",
            "127.0.0.1",
            "-U",
            unquote(url.username or "vellar"),
            "-d",
            unquote(url.path[1:]),
        ]
    raise RuntimeError(f"Не найден {tool}: установите инструменты PostgreSQL")


async def run_tool(tool: str, dsn: str, args: list[str], file: Path, *, restore: bool) -> None:
    with file.open("rb" if restore else "wb") as stream:
        process = await asyncio.create_subprocess_exec(
            *tool_command(tool, dsn),
            *args,
            env=pg_environment(dsn),
            stdin=stream if restore else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL if restore else stream,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(600):
                await process.communicate()
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            # Диагностика pg_dump может содержать адрес и частные данные.
            raise RuntimeError(f"{tool} завершился с ошибкой {process.returncode}")


async def manifest(connection: asyncpg.Connection) -> dict[str, tuple[int, str]]:
    """Хеш всех строк всех таблиц снимка, включая ценности, резервы и ответы."""
    tables = await connection.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    result = {}
    for row in tables:
        name = row["tablename"]
        quoted = '"' + name.replace('"', '""') + '"'
        digest = hashlib.sha256()
        count = 0
        # Порядок по полному представлению строки, без зависимости от PK.
        async for record in connection.cursor(
            f"SELECT row_to_json(t)::text AS data FROM {quoted} t ORDER BY row_to_json(t)::text"
        ):
            value = record["data"].encode("utf-8")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
            count += 1
        result[name] = (count, digest.hexdigest())
    return result


@asynccontextmanager
async def backup_lock(dsn: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(dsn, timeout=10, command_timeout=600)
    try:
        locked = await connection.fetchval("SELECT pg_try_advisory_lock(8675309004)")
        if not locked:
            raise RuntimeError("Другая резервная копия ещё выполняется")
        yield connection
    finally:
        await connection.close()


def rotate(directory: Path, keep: int, current: Path) -> None:
    if keep < 1:
        raise ValueError("Нужно хранить хотя бы одну проверенную копию")
    # Старые sql без подтверждения никогда автоматически не удаляются.
    verified = []
    for proof in directory.glob("vellar-*.verified.json"):
        dump = proof.with_name(proof.name.removesuffix(".verified.json") + ".dump")
        if dump.is_file():
            verified.append(dump)
    ordered = [current, *sorted((p for p in verified if p != current), reverse=True)]
    for stale in ordered[keep:]:
        stale.unlink()
        stale.with_suffix(".verified.json").unlink()


async def create_backup(dsn: str, directory: Path, *, keep: int = 20, verify: bool = True) -> Path:
    if keep < 1:
        raise ValueError("Нужно хранить хотя бы одну копию")
    database_dsn(dsn, "postgres")  # Проверка до создания файлов и соединения.
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    file = directory / f"vellar-{stamp}-{uuid4().hex}.dump"
    scratch = "vellar_restore_" + uuid4().hex + "_test"
    async with backup_lock(dsn) as source:
        async with source.transaction(isolation="repeatable_read", readonly=True):
            snapshot = await source.fetchval("SELECT pg_export_snapshot()")
            expected = await manifest(source)
            await run_tool(
                "pg_dump",
                dsn,
                ["--format=custom", "--no-owner", "--no-acl", "--snapshot=" + snapshot],
                file,
                restore=False,
            )
        if not file.stat().st_size:
            raise RuntimeError("Пустая резервная копия")
        if not verify:
            return file  # Непроверенная копия не даёт права на ротацию.
        admin = await asyncpg.connect(database_dsn(dsn, "postgres"), timeout=10)
        created = False
        try:
            await admin.execute(f'CREATE DATABASE "{scratch}"')
            created = True
            restored_dsn = database_dsn(dsn, scratch)
            await run_tool(
                "pg_restore",
                restored_dsn,
                ["--exit-on-error", "--no-owner", "--no-acl"],
                file,
                restore=True,
            )
            restored = await asyncpg.connect(restored_dsn, timeout=10, command_timeout=600)
            try:
                async with restored.transaction(isolation="repeatable_read", readonly=True):
                    actual = await manifest(restored)
            finally:
                await restored.close()
            if actual != expected:
                raise RuntimeError("Данные восстановленной копии отличаются от снимка")
        finally:
            if created:
                await admin.execute(f'DROP DATABASE "{scratch}" WITH (FORCE)')
            await admin.close()
        proof = file.with_suffix(".verified.json")
        proof.write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
        rotate(directory, keep, file)
    return file


def main() -> int:
    parser = argparse.ArgumentParser(description="Копия с проверкой восстановления до ротации")
    parser.add_argument("--keep", type=int, default=20)
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--directory", type=Path, default=Path("backups"))
    args = parser.parse_args()
    try:
        file = asyncio.run(
            create_backup(
                load_settings().postgres_dsn,
                args.directory,
                keep=args.keep,
                verify=not args.no_verify,
            )
        )
    except Exception as error:
        print(f"Копия не прошла проверку ({type(error).__name__}); старые копии сохранены.")
        return 1
    print(f"Копия {'с проверкой' if not args.no_verify else 'без проверки'}: {file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
