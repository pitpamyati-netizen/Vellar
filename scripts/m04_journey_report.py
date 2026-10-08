"""Только агрегаты этапов новичка: никаких идентификаторов и переписки в выводе."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict

from mmorpg.config import Settings
from mmorpg.infrastructure.persistence.journey import PostgresJourneyRepository
from mmorpg.infrastructure.persistence.pool import create_postgres_pool


async def report(bot_id: int, inactive_hours: int) -> None:
    settings = Settings()
    if not settings.uses_postgres:
        raise ValueError("A permanent report requires PostgreSQL")
    pool = await create_postgres_pool(settings)
    try:
        result = await PostgresJourneyRepository(pool).stats(
            bot_id, inactive_before=int(time.time()) - inactive_hours * 3600
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-id", type=int, required=True)
    parser.add_argument("--inactive-hours", type=int, default=24)
    args = parser.parse_args()
    if args.inactive_hours < 1:
        parser.error("--inactive-hours must be positive")
    asyncio.run(report(args.bot_id, args.inactive_hours))


if __name__ == "__main__":
    main()
