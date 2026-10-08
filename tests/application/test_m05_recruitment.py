"""Набор, согласие, перерыв и защита при изменившемся составе."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mmorpg.application.services.battle import BattleStore, begin
from mmorpg.application.services.invitations import Invitations
from mmorpg.application.services.party import PartyStore
from mmorpg.application.services.recruitment import Recruitment
from mmorpg.domain.entities import Character
from mmorpg.domain.rules.recruitment import PACES, goals
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.memory import (
    InMemoryCharacterRepository,
    InMemoryPartyRepository,
)
from mmorpg.presentation.telegram.flows.state import Descent, LocationSession, PlayState
from mmorpg.presentation.telegram.handlers.combat import _party_of
from mmorpg.presentation.telegram.handlers.play import _claim_roamer


@pytest.fixture
async def gathering(content):
    characters = InMemoryCharacterRepository()
    cache = InMemoryStateCache()
    parties = PartyStore(InMemoryPartyRepository(), cache)
    people = tuple(
        [
            await characters.create(
                Character(
                    id=0,
                    user_id=700000 + index,
                    name=f"Путник{index}",
                    race_id="human",
                    class_id="warrior",
                    level=6,
                    city_id=content.cities[0].id,
                    gold=83,
                )
            )
            for index in range(7)
        ]
    )
    service = Recruitment(parties, characters).with_content(content)
    goal = goals(content, people[0])[0]
    listing, notice = await service.publish(people[0], goal.key, PACES[0], now=100)
    assert listing and "опубликован" in notice
    return service, people, listing, goal


async def test_join_pause_leave_return_close_and_expiry_preserve_values(gathering):
    service, people, listing, goal = gathering
    owner, guest = people[:2]
    assert "вступили" in await service.join(listing.id, guest, now=101)
    assert await service.companions(await service.parties.of(guest.id), goal_key=goal.key) == ()
    assert "подтверждена" in await service.ready(listing.id, guest, enabled=True)
    assert (await service.get(listing.id)).ready == (guest.id,)
    assert "снята" in await service.ready(listing.id, guest, enabled=False)
    await service.ready(listing.id, guest, enabled=True)
    await service.leave(guest.id)
    assert guest.id not in (await service.get(listing.id)).ready
    assert "вступили" in await service.join(listing.id, guest, now=102)
    assert (await service.get(listing.id)).ready == ()
    assert "только" in await service.close(listing.id, guest.id)
    assert "закрыт" in await service.close(listing.id, owner.id)
    assert "завершён" in await service.join(listing.id, people[2], now=103)
    assert await service.board(now=104) == ()
    assert (await service.characters.get(guest.id)).gold == 83
    assert (await service.parties.of(guest.id)).members == (owner.id, guest.id)


async def test_board_expiry_does_not_disband_or_erase_readiness(gathering):
    service, people, listing, goal = gathering
    await service.join(listing.id, people[1], now=101)
    await service.ready(listing.id, people[1], enabled=True)
    assert await service.board(now=listing.expires_at) == ()
    assert (await service.get(listing.id)).status == "expired"
    assert (await service.parties.of(people[1].id)).has(people[0].id)
    assert await service.companions(await service.parties.of(people[1].id), goal_key=goal.key) == (
        people[1].id,
    )
    newer, _ = await service.publish(people[0], goal.key, PACES[1], now=listing.expires_at + 1)
    assert newer.id != listing.id and not newer.ready
    assert "изменилась" in await service.ready(listing.id, people[1], enabled=True)


async def test_last_seat_and_second_party_do_not_steal_member(gathering):
    service, people, listing, _ = gathering
    for guest in people[1:4]:
        await service.join(listing.id, guest, now=101)
    results = await asyncio.gather(*(service.join(listing.id, one, now=102) for one in people[4:6]))
    assert sum("вступили" in one for one in results) == 1
    assert sum("заняты" in one for one in results) == 1
    party = await service.parties.by_leader(people[0].id)
    assert len(party.members) == 5
    outsider = people[6]
    own = await service.parties.create(outsider.id)
    assert "другом" in await service.join(listing.id, outsider, now=103)
    assert await service.parties.of(outsider.id) == own


async def test_level_change_busy_and_missing_content_are_checked_again(gathering, content):
    service, people, listing, _ = gathering
    guest = await service.characters.save(replace(people[1], level=40))
    assert "уровень" in (await service.join(listing.id, guest, now=101)).lower()
    await service.characters.save(replace(guest, level=6))
    session, _ = begin(
        content,
        battle_id="held",
        attackers=[(people[1], True)],
        defenders=[(people[2], True)],
        seed=b"m05",
    )
    await BattleStore(service._cache).save(session)
    assert "бой" in await service.join(listing.id, people[1], now=101)
    await service._cache.delete(f"battle-of:{people[1].id}")
    await service.join(listing.id, people[1], now=101)
    await service.characters.save(replace(await service.characters.get(people[1].id), level=40))
    assert "уровень" in (await service.ready(listing.id, people[1], enabled=True)).lower()
    service.with_content(content.rebuilt(cities=()))
    assert await service.board(now=102) == ()


async def test_old_listing_cannot_join_recreated_party_or_consent_to_new_goal(gathering):
    service, people, listing, goal = gathering
    party = await service.parties.of(people[0].id)
    await service.parties.disband(party)
    await service.parties.create(people[0].id)
    assert "завершён" in await service.join(listing.id, people[1], now=101)
    newer, _ = await service.publish(people[0], goal.key, PACES[1], now=102)
    assert newer.id != listing.id
    assert "завершён" in await service.join(listing.id, people[1], now=103)
    assert not (await service.parties.of(people[0].id)).has(people[1].id)


async def test_block_member_and_nonleader_leave_without_affecting_property(gathering):
    service, people, listing, _ = gathering
    owner, first, second = people[:3]
    await service.join(listing.id, first, now=101)
    await service.join(listing.id, second, now=102)
    await service.ready(listing.id, first, enabled=True)
    assert "заблокирован" in await service.block(owner, first.id, enabled=True)
    assert await service.parties.of(first.id) is None
    assert "блокировка" in await service.join(listing.id, first, now=103)
    await service.block(owner, first.id, enabled=False)
    await service.join(listing.id, first, now=104)
    assert first.id not in (await service.get(listing.id)).ready
    await service.block(second, first.id, enabled=True)
    assert await service.parties.of(second.id) is None
    assert "блокировка" in await service.join(listing.id, second, now=105)
    for one in people:
        assert (await service.characters.get(one.id)).gold == 83


async def test_no_silent_pull_into_battle_and_other_goals_stay_solo(gathering, content):
    service, people, listing, goal = gathering
    owner, guest = people[:2]
    await service.join(listing.id, guest, now=101)
    flow = PlayState(
        fight="node:0", session=LocationSession(city_id=goal.city_id, slot=int(goal.target))
    )
    battle = BattleStore(service._cache)
    assert (
        await _party_of(
            owner, service.parties, service.characters, battle, flow=flow, content=content
        )
        == ()
    )
    await service.ready(listing.id, owner, enabled=True)
    assert (
        await _party_of(
            owner, service.parties, service.characters, battle, flow=flow, content=content
        )
        == ()
    )
    await service.ready(listing.id, guest, enabled=True)
    assert await _party_of(
        owner, service.parties, service.characters, battle, flow=flow, content=content
    ) == (guest,)
    assert (
        await _party_of(
            owner,
            service.parties,
            service.characters,
            battle,
            flow=replace(flow, fight="arena"),
            content=content,
        )
        == ()
    )
    await service.characters.save(replace(guest, city_id=content.cities[1].id))
    assert (
        await _party_of(
            owner, service.parties, service.characters, battle, flow=flow, content=content
        )
        == ()
    )


async def test_direct_party_departure_resets_ready_before_direct_reinvite(gathering, monkeypatch):
    service, people, listing, _ = gathering
    owner, guest = people[:2]
    await service.join(listing.id, guest, now=101)
    await service.ready(listing.id, guest, enabled=True)
    await service.parties.leave(guest.id)
    await service.parties.call(leader_id=owner.id, invitee_id=guest.id)
    assert (await service.parties.accept(guest.id)).has(guest.id)
    assert guest.id not in (await service.get(listing.id)).ready


@pytest.mark.parametrize("change", ["city", "guest_level", "starter_level", "pause", "busy"])
async def test_group_roamer_requires_current_available_consented_companion(
    gathering, content, change
):
    service, people, listing, goal = gathering
    owner, guest = people[:2]
    await service.join(listing.id, guest, now=101)
    await service.ready(listing.id, owner, enabled=True)
    await service.ready(listing.id, guest, enabled=True)
    descent = Descent(city_id=goal.city_id, slot=int(goal.target), roamer=True, group=True)
    locations = SimpleNamespace(claim_roamer=AsyncMock(return_value=True))
    assert not await _claim_roamer(
        owner, descent, locations, service.parties, characters=service.characters, content=content
    )
    locations.claim_roamer.reset_mock()
    match change:
        case "city":
            await service.characters.save(replace(guest, city_id=content.cities[1].id))
        case "guest_level":
            await service.characters.save(replace(guest, level=40))
        case "starter_level":
            owner = await service.characters.save(replace(owner, level=40))
        case "pause":
            await service.ready(listing.id, guest, enabled=False)
        case "busy":
            battle, _ = begin(
                content,
                battle_id="another",
                attackers=[(guest, True)],
                defenders=[(people[2], True)],
                seed=b"m05",
            )
            await BattleStore(service._cache).save(battle)
    refusal = await _claim_roamer(
        owner, descent, locations, service.parties, characters=service.characters, content=content
    )
    assert "В одиночку" in refusal
    locations.claim_roamer.assert_not_awaited()


@pytest.mark.parametrize("kind", ["party", "guild"])
async def test_expired_invite_retains_explanation_and_recipient_can_repeat(kind):
    cache = InMemoryStateCache()
    calls = Invitations(cache, kind, ttl=10)
    assert await calls.send(1, 1, 2, now=100) == ""
    assert await calls.current(2, now=109) == 1
    assert await calls.current(2, now=110) == 0
    assert "истёк" in await calls.explanation(2, now=110)
    assert await calls.repeat(2, now=5000)
    assert await calls.current(2, now=5001) == 1
    await calls.finish(2)
    assert not await calls.repeat(2, now=5002)


@pytest.mark.parametrize("kind", ["party", "guild"])
async def test_invitation_spam_block_budget_and_no_overwrite(kind):
    cache = InMemoryStateCache()
    calls = Invitations(cache, kind)
    assert await calls.send(1, 1, 2, now=100) == ""
    assert "уже" in await calls.send(3, 3, 2, now=101)
    assert (await calls.get(2)).group_id == 1
    await calls.finish(2)
    assert "недоступен" in await calls.send(1, 1, 2, now=102)
    for invitee in range(3, 7):
        assert await calls.send(1, 1, invitee, now=103) == ""
    assert "пять" in await calls.send(1, 1, 7, now=104)
    assert await calls.send(1, 1, 7, now=180) == ""
    await calls.block(2, 1)
    assert "блокировка" in await calls.send(1, 1, 2, now=10000)
    other_kind = Invitations(cache, "guild" if kind == "party" else "party")
    assert "блокировка" in await other_kind.send(1, 1, 2, now=10000)
    assert not await calls.repeat(2, now=10000)


async def test_party_invitation_cannot_move_member_from_another_party_or_recreated_group():
    parties = PartyStore(InMemoryPartyRepository(), InMemoryStateCache())
    await parties.create(1)
    await parties.call(leader_id=1, invitee_id=2)
    await parties.create(2)
    assert await parties.accept(2) is None
    assert (await parties.of(2)).leader_id == 2
    await parties.disband(await parties.of(2))
    await parties.disband(await parties.of(1))
    await parties.create(1)
    assert await parties.accept(2) is None
