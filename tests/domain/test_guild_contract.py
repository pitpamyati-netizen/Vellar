"""Подряд гильдии: три дела на переворот, и платят за них гильдии (ADR 0077)."""

from __future__ import annotations

from mmorpg.domain.entities.content import GuildTier
from mmorpg.domain.rules.guild import Standing, band_level, fight_worth
from mmorpg.domain.rules.guild_contract import Contract, ContractKind, contracts

_TIERS: tuple[GuildTier, ...] = tuple(
    GuildTier(
        level=level,
        name=f"ступень {level}",
        deeds=level * 100,
        seats=10 + level,
        store_slots=10 + level,
    )
    for level in range(1, 8)
)


def place(level: int) -> Standing:
    return Standing(tier=_TIERS[level - 1], seats=12, members=5, store_slots=12)


def deal(level: int, kind: ContractKind, *, rotation: int = 3, guild_id: int = 1) -> Contract:
    made = contracts(
        _TIERS, place(level), world_seed="vellar-prime", guild_id=guild_id, rotation=rotation
    )
    return next(one for one in made if one.kind is kind)


def test_the_same_rotation_asks_the_same_thing() -> None:
    """Подряд - чистая функция: экран и зачёт обязаны видеть одно и то же."""
    first = contracts(_TIERS, place(3), world_seed="s", guild_id=7, rotation=11)
    again = contracts(_TIERS, place(3), world_seed="s", guild_id=7, rotation=11)
    assert first == again
    assert first != contracts(_TIERS, place(3), world_seed="s", guild_id=7, rotation=12)
    assert first != contracts(_TIERS, place(3), world_seed="s", guild_id=8, rotation=11)


def test_all_three_deeds_are_asked_every_rotation() -> None:
    made = contracts(_TIERS, place(1), world_seed="s", guild_id=1, rotation=1)
    assert tuple(one.kind for one in made) == tuple(ContractKind)
    assert all(one.target > 0 and one.line for one in made)


def test_a_grown_guild_is_asked_for_more_and_paid_more() -> None:
    small = contracts(_TIERS, place(1), world_seed="s", guild_id=1, rotation=4)
    big = contracts(_TIERS, place(7), world_seed="s", guild_id=1, rotation=4)
    for below, above in zip(small, big, strict=True):
        assert above.target > below.target, above.kind
        assert above.reward_deeds > below.reward_deeds, above.kind


def test_gold_brought_to_the_vault_is_paid_in_deeds_alone() -> None:
    """Золото за снесённое золото - петля, в которой казна растёт сама."""
    tithe = deal(4, ContractKind.TITHE)
    assert tithe.reward_gold == 0
    assert tithe.reward_deeds > 0
    assert "деяний" in tithe.pay
    assert "золота" not in tithe.pay

    cull = deal(4, ContractKind.CULL)
    assert cull.reward_gold > 0
    assert "золота" in cull.pay


def test_the_tithe_is_measured_in_fights_of_the_tier() -> None:
    """Взнос просят в золоте, а меряют боем: у ступени 7 он не тот, что у первой."""
    for level in (1, 7):
        tithe = deal(level, ContractKind.TITHE)
        assert tithe.target % fight_worth(band_level(_TIERS, place(level))) == 0


def test_a_deed_closes_only_when_the_count_reaches_it() -> None:
    cull = deal(2, ContractKind.CULL)
    assert not cull.done(cull.target - 1)
    assert cull.done(cull.target)
    assert cull.done(cull.target * 2)


def test_a_guild_without_tiers_still_gets_a_contract() -> None:
    """Содержимое переживает код: лестницы может не оказаться вовсе."""
    made = contracts((), Standing(), world_seed="s", guild_id=1, rotation=1)
    assert len(made) == len(ContractKind)
    assert all(one.level >= 1 for one in made)
