"""SQL: последняя свободная позиция, отказ, повтор и восстановление без Redis."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.guild import GuildStore
from mmorpg.application.services.party import PartyStore
from mmorpg.application.services.recruitment import Recruitment
from mmorpg.domain.entities import Character
from mmorpg.domain.ports.repositories import User
from mmorpg.domain.rules.guild import GuildRank
from mmorpg.domain.rules.recruitment import PACES, goals
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.infrastructure.persistence.postgres import (
    PostgresCharacterRepository,
    PostgresGuildRepository,
    PostgresPartyRepository,
    PostgresUserRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def recruits(pool, content):
    accounts = list(range(-985_510, -985_502))
    await pool.execute("DELETE FROM users WHERE telegram_id=ANY($1::bigint[])", accounts)
    await pool.execute(
        "DELETE FROM gameplay_state WHERE key LIKE 'recruitment:%' OR key LIKE 'social:%'"
    )
    users = PostgresUserRepository(pool)
    characters = PostgresCharacterRepository(pool)
    cache = PostgresGameplayState(pool, InMemoryStateCache())
    parties = PartyStore(PostgresPartyRepository(pool), cache)
    people = []
    for index, account in enumerate(accounts):
        await users.upsert(User(telegram_id=account))
        people.append(
            await characters.create(
                Character(
                    id=0,
                    user_id=account,
                    name=f"Сбор{index}",
                    race_id="human",
                    class_id="warrior",
                    city_id=content.cities[0].id,
                    level=6,
                    gold=137,
                )
            )
        )
    service = Recruitment(parties, characters).with_content(content)
    goal = goals(content, people[0])[0]
    listing, _ = await service.publish(people[0], goal.key, PACES[0], now=10000)
    assert listing
    try:
        yield SimpleNamespace(
            pool=pool,
            cache=cache,
            parties=parties,
            service=service,
            people=people,
            listing=listing,
            goal=goal,
        )
    finally:
        await characters.operations.recover()
        await pool.execute("DELETE FROM users WHERE telegram_id=ANY($1::bigint[])", accounts)
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key LIKE 'recruitment:%' OR key LIKE 'social:%'"
        )


@atomic_action
async def join_once(service, author_id, target_id, command, *, operation_id=None) -> str:
    actor = await service.characters.get(author_id)
    answer = await service.join(target_id, actor, now=10001)
    await service.ready(target_id, actor, enabled=True)
    if command == "break":
        raise RuntimeError("after roster and consent")
    return answer


async def test_sql_rollback_and_duplicate_command_keep_one_membership(recruits):
    e = recruits
    guest = e.people[1]
    with pytest.raises(RuntimeError):
        await join_once(
            e.service, guest.id, e.listing.id, "break", operation_id="m05:" + uuid4().hex
        )
    assert await e.parties.of(guest.id) is None
    assert (await e.service.get(e.listing.id)).ready == ()
    operation_id = "m05:" + uuid4().hex
    answers = await asyncio.gather(
        *(
            join_once(e.service, guest.id, e.listing.id, "join", operation_id=operation_id)
            for _ in range(2)
        )
    )
    assert answers[0] == answers[1] and "вступили" in answers[0]
    assert (await e.parties.of(guest.id)).members.count(guest.id) == 1
    assert (await e.service.get(e.listing.id)).ready == (guest.id,)
    assert (await e.service.characters.get(guest.id)).gold == 137


async def test_sql_last_seat_competing_parties_and_restart(recruits, content):
    e = recruits
    for one in e.people[1:4]:
        await e.service.join(e.listing.id, one, now=10001)
    other_cache = PostgresGameplayState(e.pool, InMemoryStateCache())
    other = Recruitment(
        PartyStore(PostgresPartyRepository(e.pool), other_cache),
        PostgresCharacterRepository(e.pool),
    ).with_content(content)
    answers = await asyncio.gather(
        e.service.join(e.listing.id, e.people[4], now=10002),
        other.join(e.listing.id, e.people[5], now=10002),
    )
    assert sum("вступили" in one for one in answers) == 1
    party = await e.parties.by_leader(e.people[0].id)
    assert len(party.members) == 5
    # В новом отряде один свободный герой. Два собравших зовут одного игрока.
    await e.parties.create(e.people[6].id)
    await e.parties.call(leader_id=e.people[6].id, invitee_id=e.people[7].id)
    assert "уже" in await e.parties.call(leader_id=e.people[0].id, invitee_id=e.people[7].id)
    assert (await e.parties.accept(e.people[7].id)).leader_id == e.people[6].id
    assert (await other.for_party(e.people[0].id)).id == e.listing.id
    assert len((await other.parties.by_leader(e.people[0].id)).members) == 5


async def test_sql_block_and_readiness_survive_temporary_cache_loss(recruits, content):
    e = recruits
    await e.service.join(e.listing.id, e.people[1], now=10001)
    await e.service.ready(e.listing.id, e.people[1], enabled=True)
    await e.service.block(e.people[0], e.people[2].id, enabled=True)
    service = Recruitment(
        PartyStore(
            PostgresPartyRepository(e.pool), PostgresGameplayState(e.pool, InMemoryStateCache())
        ),
        PostgresCharacterRepository(e.pool),
    ).with_content(content)
    assert (await service.get(e.listing.id)).ready == (e.people[1].id,)
    assert "блокировка" in await service.join(e.listing.id, e.people[2], now=10002)
    assert "блокировка" in await service.parties.call(
        leader_id=e.people[0].id, invitee_id=e.people[2].id
    )
    await service.leave(e.people[1].id)
    assert not (await e.service.get(e.listing.id)).ready


@pytest.mark.parametrize("kind", ["party", "guild"])
async def test_sql_expired_invitation_is_explained_and_renewable(
    recruits, content, kind, monkeypatch
):
    e = recruits
    owner, guest = e.people[:2]
    clock = [10000]
    monkeypatch.setattr("time.time", lambda: clock[0])
    if kind == "party":
        store = e.parties
        await store.call(leader_id=owner.id, invitee_id=guest.id, now=10000)
    else:
        store = GuildStore(PostgresGuildRepository(e.pool), e.cache)
        guild = await store.create("Сбор", owner.id)
        await store.call(guild_id=guild.id, invitee_id=guest.id, inviter_id=owner.id, now=10000)
    clock[0] += 86400
    recreated = PostgresGameplayState(e.pool, InMemoryStateCache())
    restored_store = (
        PartyStore(PostgresPartyRepository(e.pool), recreated)
        if kind == "party"
        else (GuildStore(PostgresGuildRepository(e.pool), recreated))
    )
    assert "истёк" in await restored_store.invitations.explanation(guest.id)
    assert await restored_store.invitations.repeat(guest.id)
    accepted = (
        await restored_store.accept(guest.id)
        if kind == "party"
        else (await restored_store.accept(guest.id, content))
    )
    assert accepted.has(guest.id)
    assert (await e.service.characters.get(guest.id)).gold == 137


async def test_sql_guild_accept_checks_current_rights_and_other_membership(recruits, content):
    e = recruits
    owner, guest, inviter = e.people[:3]
    guilds = GuildStore(PostgresGuildRepository(e.pool), e.cache)
    guild = await guilds.create("Сбор", owner.id)
    guild = guild.with_member(inviter.id, GuildRank.VETERAN, seats=12)
    await guilds.save(guild)
    assert await guilds.call(guild_id=guild.id, invitee_id=guest.id, inviter_id=inviter.id) == ""
    # Права на приглашение потеряны после его отправки.
    await guilds.save(guild.with_rank(inviter.id, GuildRank.RECRUIT))
    assert await guilds.accept(guest.id, content) is None
    await guilds.save(guild)
    another = await guilds.create("Другие", guest.id)
    assert await guilds.accept(guest.id, content) is None
    assert (await guilds.of(guest.id)).id == another.id
