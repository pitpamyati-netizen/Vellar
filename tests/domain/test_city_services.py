"""The rules behind the four city sections: vault, mentor, tavern, dungeons."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen.dungeons import DUNGEONS_PER_CITY, dungeon_floor, roll_dungeons
from mmorpg.domain.rules.bank import (
    Transfer,
    TransferKind,
    VaultRefusal,
    apply_transfer,
    deposit_fee,
    largest_deposit,
    plan_deposit,
    plan_withdrawal,
    vault_limit,
    vault_room,
)
from mmorpg.domain.rules.tavern import roll_rumours
from mmorpg.domain.rules.training import can_train, train_stat

WORLD_SEED = "vellar-test"
CYCLE = 100


@pytest.fixture
def hero() -> Character:
    return Character(
        id=1, user_id=42, name="Аргус", race_id="human", class_id="warrior", level=4, gold=1000
    )


# --- the vault --------------------------------------------------------


def test_the_limit_grows_with_the_level() -> None:
    assert vault_limit(1) < vault_limit(10) < vault_limit(300)
    # A level below one is still a character, not an empty vault.
    assert vault_limit(0) == vault_limit(1)


def test_a_deposit_always_costs_at_least_one_gold() -> None:
    assert deposit_fee(0) == 0
    assert deposit_fee(1) == 1
    assert deposit_fee(1000) == 20


def test_a_deposit_moves_the_amount_and_pays_the_duty(hero: Character) -> None:
    plan = plan_deposit(hero, 100)
    assert isinstance(plan, Transfer)
    assert (plan.amount, plan.fee) == (100, 2)

    after = apply_transfer(hero, plan)
    assert after.bank_gold == 100
    assert after.gold == hero.gold - 102


def test_a_withdrawal_is_free_and_gives_everything_back(hero: Character) -> None:
    holder = replace(hero, bank_gold=250)
    plan = plan_withdrawal(holder, 250)
    assert isinstance(plan, Transfer)
    assert plan.fee == 0

    after = apply_transfer(holder, plan)
    assert (after.bank_gold, after.gold) == (0, holder.gold + 250)


@pytest.mark.parametrize(
    ("character_fields", "kind", "amount", "refusal"),
    [
        ({}, TransferKind.DEPOSIT, 0, VaultRefusal.NOTHING),
        ({}, TransferKind.WITHDRAW, -5, VaultRefusal.NOTHING),
        ({"gold": 10}, TransferKind.DEPOSIT, 100, VaultRefusal.NO_GOLD),
        ({}, TransferKind.WITHDRAW, 1, VaultRefusal.NO_STORED),
        ({"gold": 10_000_000}, TransferKind.DEPOSIT, 9_999_999, VaultRefusal.OVER_LIMIT),
    ],
)
def test_every_refusal_has_its_own_reason(
    hero: Character,
    character_fields: dict[str, int],
    kind: TransferKind,
    amount: int,
    refusal: VaultRefusal,
) -> None:
    character = replace(hero, **character_fields)
    plan = (
        plan_deposit(character, amount)
        if kind is TransferKind.DEPOSIT
        else plan_withdrawal(character, amount)
    )
    assert plan is refusal


def test_the_largest_deposit_fits_together_with_its_duty(hero: Character) -> None:
    for gold in (0, 1, 25, 51, 100, 999, 1000):
        character = replace(hero, gold=gold)
        amount = largest_deposit(character)
        assert amount + deposit_fee(amount) <= gold
        if amount:
            assert isinstance(plan_deposit(character, amount), Transfer)


def test_the_largest_deposit_stops_at_the_room_left(hero: Character) -> None:
    full = replace(hero, gold=10_000, bank_gold=vault_limit(hero.level))
    assert vault_room(full) == 0
    assert largest_deposit(full) == 0


# --- the mentor -------------------------------------------------------


def test_a_point_becomes_a_number_on_the_sheet(hero: Character) -> None:
    trainee = replace(hero, unspent_stat_points=2)
    assert can_train(trainee) is True

    trained = train_stat(trainee, StatCode.STR)
    assert trained is not None
    assert trained.allocated.STR == hero.allocated.STR + 1
    assert trained.unspent_stat_points == 1


def test_without_a_point_the_mentor_does_nothing(hero: Character) -> None:
    assert can_train(hero) is False
    assert train_stat(hero, StatCode.LCK) is None


# --- the tavern -------------------------------------------------------


def test_the_summary_is_about_the_locations_near_the_level(content: GameContent) -> None:
    city = content.city("farhold")
    rumours = roll_rumours(world_seed=WORLD_SEED, city=city, cycle=CYCLE, level=1)
    assert len(rumours) == 3
    assert all(rumour.nodes >= 8 for rumour in rumours)
    assert [rumour.slot for rumour in rumours] == sorted(rumour.slot for rumour in rumours)
    # The lowest location of the city covers level 1, so it is talked about.
    assert rumours[0].level_min == 1


def test_the_summary_changes_with_the_watch(content: GameContent) -> None:
    city = content.city("farhold")
    now = roll_rumours(world_seed=WORLD_SEED, city=city, cycle=CYCLE, level=5)
    again = roll_rumours(world_seed=WORLD_SEED, city=city, cycle=CYCLE, level=5)
    later = roll_rumours(world_seed=WORLD_SEED, city=city, cycle=CYCLE + 1, level=5)
    assert now == again
    assert now != later


def test_a_quiet_location_says_so(content: GameContent) -> None:
    city = content.city("farhold")
    rumours = roll_rumours(world_seed=WORLD_SEED, city=city, cycle=CYCLE, level=1)
    for rumour in rumours:
        assert rumour.quiet == (rumour.elites + rumour.caches + rumour.shrines == 0)


# --- dungeons ---------------------------------------------------------


def test_dungeons_do_not_rotate(content: GameContent) -> None:
    city = content.city("farhold")
    first = roll_dungeons(
        world_seed=WORLD_SEED, city_id=city.id, level_min=city.level_min, level_max=city.level_max
    )
    again = roll_dungeons(
        world_seed=WORLD_SEED, city_id=city.id, level_min=city.level_min, level_max=city.level_max
    )
    assert first == again
    assert len(first) == DUNGEONS_PER_CITY


def test_a_dungeon_is_named_and_priced(content: GameContent) -> None:
    city = content.city("farhold")
    dungeons = roll_dungeons(
        world_seed=WORLD_SEED, city_id=city.id, level_min=city.level_min, level_max=city.level_max
    )
    names = {dungeon.name for dungeon in dungeons}
    assert len(names) == len(dungeons)
    for dungeon in dungeons:
        assert " " in dungeon.name
        assert dungeon.level_min < dungeon.level_max
        assert dungeon.floors >= 2
        assert dungeon.party >= 2
        assert dungeon.fee > 0
        assert dungeon.covers(dungeon.level_min) is True
        assert dungeon.covers(dungeon.level_max + 1) is False


def test_a_dungeon_of_a_one_level_city_still_has_a_band() -> None:
    dungeons = roll_dungeons(world_seed=WORLD_SEED, city_id="tiny", level_min=7, level_max=7)
    for dungeon in dungeons:
        assert dungeon.level_max > dungeon.level_min


def test_floors_are_told_apart_and_stay_the_same(content: GameContent) -> None:
    city = content.city("farhold")
    dungeon = roll_dungeons(
        world_seed=WORLD_SEED, city_id=city.id, level_min=city.level_min, level_max=city.level_max
    )[0]

    first = dungeon_floor(world_seed=WORLD_SEED, dungeon=dungeon, floor=1)
    second = dungeon_floor(world_seed=WORLD_SEED, dungeon=dungeon, floor=2)
    assert first == dungeon_floor(world_seed=WORLD_SEED, dungeon=dungeon, floor=1)
    assert first.nodes != second.nodes
    assert first.is_connected
    assert "ярус 1" in first.name
