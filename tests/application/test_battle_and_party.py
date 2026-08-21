"""Бой, который лежит один на всех, и отряд, который в него идёт.

Проверяется то, чего не видно из домена: что бой переживает дорогу через общее
хранилище, что занятость снимается вместе с ним и что отряд собирается только
по согласию того, кого позвали (ADR 0021).
"""

from __future__ import annotations

import pytest

from mmorpg.application.services import battle as battle_service
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Verdict
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules.combat import act
from mmorpg.domain.rules.party import Party
from mmorpg.infrastructure.cache.memory import InMemoryStateCache

SEED = b"battle-store-0001"


def a_hero(name: str, character_id: int) -> Character:
    return Character(
        id=character_id,
        user_id=500 + character_id,
        name=name,
        race_id="human",
        class_id="warrior",
        level=12,
        gold=200,
        loadout=SkillLoadout(
            actives=("warrior_cleave", None, None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


@pytest.fixture
def cache() -> InMemoryStateCache:
    return InMemoryStateCache()


def a_battle(content: GameContent, *, live_defender: bool = True) -> battle_service.BattleSession:
    session, _ = battle_service.begin(
        content,
        battle_id="duel-1",
        attackers=[(a_hero("Аргус", 1), True)],
        defenders=[(a_hero("Мирна", 2), live_defender)],
        seed=SEED,
        kind=battle_service.BattleKind.DUEL,
        owner=1,
    )
    return session


# --- запись боя --------------------------------------------------------


async def test_a_battle_survives_the_road_through_the_store(
    content: GameContent, cache: InMemoryStateCache
) -> None:
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    restored = await store.load(session.id)
    assert restored is not None
    assert restored.state.combatants == session.state.combatants
    assert restored.state.order == session.state.order
    assert restored.kind is battle_service.BattleKind.DUEL
    assert restored.owner == 1


async def test_both_sides_are_marked_busy(content: GameContent, cache: InMemoryStateCache) -> None:
    """В два боя сразу не зовут: занятость лежит на каждом участнике."""
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    assert await store.busy(1) == session.id
    assert await store.busy(2) == session.id
    assert await store.busy(3) is None


async def test_a_finished_battle_frees_everybody_but_stays_readable(
    content: GameContent, cache: InMemoryStateCache
) -> None:
    """Экран итога - настоящий экран, и читается он по той же записи."""
    store = battle_service.BattleStore(cache)
    session = a_battle(content)
    await store.save(session)

    roster = battle_service.roster_for(session, {1: a_hero("Аргус", 1), 2: a_hero("Мирна", 2)})
    current = session.state.active
    assert current is not None
    finished = act(content, roster, session.state, BattleAction(kind=ActionKind.YIELD), SEED)
    assert finished.is_over

    settled = battle_service.settled(session)
    await store.release(settled)

    assert await store.busy(1) is None, "занятость снимается вместе с боем"
    kept = await store.load(session.id)
    assert kept is not None and kept.settled, "сама запись ещё на месте"

    await store.forget(settled)
    assert await store.load(session.id) is None


async def test_a_battle_that_expired_frees_the_character(cache: InMemoryStateCache) -> None:
    """Пропавший бой освобождает: персонажу нельзя остаться занятым навсегда."""
    store = battle_service.BattleStore(cache)
    await cache.set(store.key_for_character(7), "gone-battle", 60)
    assert await store.busy(7) is None


def test_roster_maps_fighters_to_the_characters_behind_them(content: GameContent) -> None:
    session = a_battle(content)
    roster = battle_service.roster_for(session, {1: a_hero("Аргус", 1), 2: a_hero("Мирна", 2)})
    assert sorted(roster) == [1, 2]
    assert roster[1].name == "Аргус"


def test_the_engine_side_gets_no_telegram_id(content: GameContent) -> None:
    """Слепку писать некому: он ходит сам (``BattleSession.live_participants``)."""
    session = a_battle(content, live_defender=False)
    assert [one.character_id for one in session.live_participants()] == [1]
    assert len(session.participants()) == 2


def test_the_verdict_is_read_per_participant(content: GameContent) -> None:
    session = a_battle(content)
    for one in session.participants():
        assert session.state.verdict_for(one.id) is Verdict.ONGOING


# --- отряд -------------------------------------------------------------


async def test_a_party_is_born_when_the_call_is_accepted(cache: InMemoryStateCache) -> None:
    parties = PartyStore(cache)
    await parties.call(leader_id=1, invitee_id=2)
    assert await parties.called_by(2) == 1

    party = await parties.accept(2)
    assert party is not None
    assert party.members == (1, 2)
    assert await parties.of(1) == party
    assert await parties.of(2) == party
    assert await parties.called_by(2) == 0, "зов израсходован"


async def test_nobody_joins_a_party_they_were_not_called_to(cache: InMemoryStateCache) -> None:
    parties = PartyStore(cache)
    assert await parties.accept(5) is None
    assert await parties.of(5) is None


async def test_declining_forgets_the_call(cache: InMemoryStateCache) -> None:
    parties = PartyStore(cache)
    await parties.call(leader_id=1, invitee_id=2)
    await parties.forget_call(2)
    assert await parties.accept(2) is None


async def test_leaving_shrinks_the_party_and_the_leader_ends_it(
    cache: InMemoryStateCache,
) -> None:
    parties = PartyStore(cache)
    await parties.save(Party(leader_id=1, members=(1, 2, 3)))

    left = await parties.leave(3)
    assert left is not None and left.members == (1, 2)

    assert await parties.leave(1) is None, "ушёл собравший - отряда больше нет"
    assert await parties.of(2) is None


def test_the_party_has_a_ceiling_and_a_level_window() -> None:
    full = Party(leader_id=1, members=tuple(range(1, party_rules.MAX_MEMBERS + 1)))
    assert full.full
    assert "не помещается" in party_rules.invite_refusal(
        inviter_level=10,
        invitee_name="Мирна",
        invitee_level=10,
        party=full,
        invitee_in_party=False,
    )
    assert "Разница уровней" in party_rules.invite_refusal(
        inviter_level=10,
        invitee_name="Мирна",
        invitee_level=10 + party_rules.LEVEL_WINDOW + 1,
        party=None,
        invitee_in_party=False,
    )
    assert (
        party_rules.invite_refusal(
            inviter_level=10,
            invitee_name="Мирна",
            invitee_level=12,
            party=None,
            invitee_in_party=False,
        )
        == ""
    )


def test_the_loot_goes_round_the_party() -> None:
    """Добыча раздаётся по кругу, а не оседает у собравшего отряд."""
    import random

    shares = party_rules.distribute(("a", "b", "c", "d"), (1, 2), random.Random(7))
    assert sorted(len(items) for items in shares.values()) == [2, 2]
    assert sorted(item for items in shares.values() for item in items) == ["a", "b", "c", "d"]
