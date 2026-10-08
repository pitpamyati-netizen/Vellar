"""Последний прежний период сохраняется при импорте ещё живого Redis."""

from typing import Any


async def bridge_legacy_periods(connection: Any) -> None:
    rows = await connection.fetch("SELECT key, value FROM durable_effects WHERE key LIKE 'guild-%'")
    latest: dict[tuple[str, str], int] = {}
    parsed: list[tuple[list[str], int, str, tuple[str, str]]] = []
    for row in rows:
        parts = row["key"].split(":")
        scope = parts[0]
        if scope in {"guild-taken", "guild-items"} and len(parts) == 4:
            index = 3
            family = scope
        elif scope in {"guild-contract", "guild-contract-paid"} and len(parts) == 4:
            index = 2
            family = "guild-contract"
            if parts[3] not in {"cull", "delve", "tithe"}:
                continue
        elif scope == "guild-war-hit" and len(parts) == 5:
            index = 4
            family = scope
        else:
            continue
        if not all(part.isascii() and part.isdecimal() for part in parts[1 : index + 1]):
            continue
        period = int(parts[index])
        group = (family, parts[1])
        latest[group] = max(period, latest.get(group, 0))
        parsed.append((parts, index, row["value"], group))
    for parts, index, value, group in parsed:
        if int(parts[index]) != latest[group]:
            continue
        parts[0] += "-v2"
        parts[index] = "0"
        await connection.execute(
            "INSERT INTO durable_effects (key, value) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            ":".join(parts),
            value,
        )
    for (family, guild_id), period in latest.items():
        if family == "guild-contract":
            await connection.execute(
                "INSERT INTO durable_effects (key, value) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                f"guild-period:{guild_id}:contract:seed",
                str(period),
            )
