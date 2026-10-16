"""Прибавка начальной локации доходит до игроков одной общей суммой."""

from dataclasses import replace

import pytest

from mmorpg.application.services.battle import BattleKind, begin
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.domain.rules import participation, progression
from mmorpg.domain.rules import party as party_rules
from mmorpg.infrastructure.persistence.memory import InMemoryInventoryRepository
from mmorpg.presentation.telegram.handlers import combat


@pytest.mark.parametrize("members", [1, 2, 5])
@pytest.mark.parametrize("participation_rule", [0, 1])
@pytest.mark.parametrize("race_id", ["dwarf", "human"])
@pytest.mark.parametrize(
    "city_id,slot,kind,expected_pool",
    [
        ("farhold", 1, BattleKind.NODE, 111),
        ("farhold", 5, BattleKind.NODE, 111),
        ("dusk_harbor", 1, BattleKind.NODE, 101),
        ("farhold", 1, BattleKind.DESCENT, 101),
        ("farhold", 0, BattleKind.NODE, 101),
    ],
)
async def test_settlement_applies_location_bonus_once_before_sharing(
    content: GameContent,
    members: int,
    participation_rule: int,
    race_id: str,
    city_id: str,
    slot: int,
    kind: BattleKind,
    expected_pool: int,
) -> None:
    heroes = tuple(
        Character(
            id=100 + index,
            user_id=1000 + index,
            name=f"Путник {index}",
            race_id=race_id,
            class_id="warrior",
            tutorial=63,
        )
        for index in range(members)
    )
    session, roster = begin(
        content,
        battle_id="early-experience",
        attackers=[(hero, True) for hero in heroes],
        seed=b"early-experience",
        city_id=city_id,
        slot=slot,
        kind=kind,
    )
    session = replace(
        session,
        participation_rule=participation_rule,
        state=replace(
            session.state, outcome=BattleOutcome.DECIDED, winner=0, experience=101, gold=25
        ),
    )
    payouts = {hero.id: combat.Payout() for hero in heroes}
    updated = {hero.id: hero for hero in heroes}
    await combat._settle_world(
        content,
        session,
        roster,
        session.participants(),
        (),
        payouts,
        updated,
        InMemoryInventoryRepository(),
        {},
    )
    splitter = participation.reward_shares if participation_rule else party_rules.split
    shares = splitter(expected_pool, members)
    for hero, share in zip(heroes, shares, strict=True):
        expected = progression.earned(content, hero, share)
        assert payouts[hero.id].experience == expected
        assert updated[hero.id].experience == hero.experience + expected
    if race_id == "dwarf":
        assert sum(payout.experience for payout in payouts.values()) == sum(shares)
    assert tuple(payout.gold for payout in payouts.values()) == splitter(25, members)


async def test_zero_experience_stays_zero(content: GameContent) -> None:
    hero = Character(
        id=1, user_id=1, name="Путник", race_id="dwarf", class_id="warrior", tutorial=63
    )
    session, roster = begin(
        content,
        battle_id="empty-experience",
        attackers=[(hero, True)],
        seed=b"empty",
        city_id="farhold",
        slot=1,
    )
    payouts = {hero.id: combat.Payout()}
    updated = {hero.id: hero}
    await combat._settle_world(
        content,
        session,
        roster,
        session.participants(),
        (),
        payouts,
        updated,
        InMemoryInventoryRepository(),
        {},
    )
    assert payouts[hero.id].experience == updated[hero.id].experience == 0
