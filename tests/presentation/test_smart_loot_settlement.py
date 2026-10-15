"""Реальная раздача различает номер героя и его временное место в бою."""

import random
from dataclasses import replace

import pytest

from mmorpg.application.services.battle import begin
from mmorpg.domain.entities import Character
from mmorpg.domain.entities.combat import BattleOutcome
from mmorpg.domain.procgen import items
from mmorpg.infrastructure.persistence.memory import InMemoryInventoryRepository
from mmorpg.presentation.telegram.handlers import combat


@pytest.mark.parametrize("seed", range(8))
async def test_class_loot_reaches_the_mage_when_character_ids_differ_from_fighter_ids(
    content, seed
):
    warrior = Character(
        id=205,
        user_id=9205,
        name="Воин",
        race_id="human",
        class_id="warrior",
        level=76,
    )
    mage = Character(
        id=901,
        user_id=9901,
        name="Маг",
        race_id="human",
        class_id="mage",
        level=76,
    )
    roll = items.new_roll(
        content,
        random.Random(17),
        content.gear_archetype("staff"),
        class_id="mage",
    )
    staff = items.gear_id("staff", 76, "rare", roll)
    session, roster = begin(
        content,
        battle_id=f"class-loot-{seed}",
        attackers=[(warrior, True), (mage, True)],
        seed=seed.to_bytes(4, "big"),
    )
    assert set(roster) == {1, 2} and {one.id for one in roster.values()} == {205, 901}
    session = replace(
        session,
        state=replace(
            session.state,
            outcome=BattleOutcome.DECIDED,
            winner=0,
            loot=(staff,),
        ),
    )
    inventory = InMemoryInventoryRepository()
    payouts = {one.id: combat.Payout() for one in roster.values()}
    updated = {one.id: one for one in roster.values()}
    await combat._settle_world(
        content,
        session,
        roster,
        session.participants(),
        (),
        payouts,
        updated,
        inventory,
        {},
    )
    assert await inventory.count(mage.id, staff) == 1
    assert await inventory.count(warrior.id, staff) == 0
    assert payouts[mage.id].loot == (content.item(staff).choice_name,)
    assert payouts[warrior.id].loot == ()
