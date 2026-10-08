"""Контроль набора, согласия, приглашения и блока в восстановленной копии."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from aiogram.fsm.storage.base import StorageKey
from scripts.test_stand import load_test_settings

from mmorpg.application.services.party import PartyStore
from mmorpg.application.services.recruitment import Recruitment
from mmorpg.domain.entities import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.recruitment import PACES, goals
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.content.loader import load_content
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState, PostgresStorage
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresPartyRepository,
    PostgresUserRepository,
)
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.states.screens import Play

ACCOUNTS = (-99990500, -99990501, -99990502, -99990503)


async def run(database: str, seed: bool) -> None:
    settings = load_test_settings()
    if not database.endswith("_test") or not database.replace("_", "").isalnum():
        raise ValueError("Only an isolated test database is allowed")
    dsn = urlunsplit(urlsplit(settings.postgres_dsn)._replace(path="/" + database))
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    try:
        content = load_content(Path("content"))
        characters = PostgresCharacterRepository(pool)
        cache = PostgresGameplayState(pool, InMemoryStateCache())
        parties = PartyStore(PostgresPartyRepository(pool), cache)
        service = Recruitment(parties, characters).with_content(content)
        storage = PostgresStorage(pool)
        key = StorageKey(bot_id=1, chat_id=ACCOUNTS[1], user_id=ACCOUNTS[1])
        if seed:
            users = PostgresUserRepository(pool)
            people = []
            for index, account in enumerate(ACCOUNTS):
                await users.upsert(User(telegram_id=account))
                people.append(
                    await characters.create(
                        Character(
                            id=0,
                            user_id=account,
                            name=f"Набор{index}",
                            race_id="human",
                            class_id="warrior",
                            city_id=content.cities[0].id,
                            level=6,
                            gold=93,
                        )
                    )
                )
            listing, _ = await service.publish(
                people[0], goals(content, people[0])[0].key, PACES[0], now=100
            )
            assert listing
            await service.join(listing.id, people[1], now=101)
            await service.ready(listing.id, people[1], enabled=True)
            await parties.call(leader_id=people[0].id, invitee_id=people[2].id, now=100)
            await service.block(people[0], people[3].id, enabled=True)
            await storage.set_state(key, Play.recruitment_card)
            await storage.set_data(
                key,
                {
                    "play": PlayState(
                        screen=ScreenId.RECRUITMENT_CARD, recruitment_id=listing.id
                    ).serialise()
                },
            )
        else:
            people = [await characters.get_active(account) for account in ACCOUNTS]
            assert all(people)
            owner, guest, invitee, blocked = people
            assert owner and guest and invitee and blocked
            listing = await service.for_party(owner.id)
            assert listing and listing.ready == (guest.id,)
            assert (await parties.of(guest.id)).members == (owner.id, guest.id)
            assert await parties.invitations.blocked(owner.id, blocked.id)
            assert "истёк" in await parties.invitations.explanation(invitee.id, now=100 + 86400)
            data = await storage.get_data(key)
            flow = PlayState.deserialise(data["play"])
            assert flow.recruitment_id == listing.id and flow.screen is ScreenId.RECRUITMENT_CARD
            assert all(one.gold == 93 for one in people if one)
    finally:
        await pool.close()
    print("M05 roster, consent, expired invitation, block and screen survived backup restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.seed))
