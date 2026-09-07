"""Гильдейская война: очко берут за своих врагов, а итог подводит счёт (ADR 0077)."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.content import GuildTier
from mmorpg.domain.rules import guild_war as war_rules
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank, Standing
from mmorpg.domain.rules.guild_war import War

_TIERS: tuple[GuildTier, ...] = (
    GuildTier(level=1, name="Товарищество", deeds=0, seats=12, store_slots=12),
    GuildTier(level=2, name="Артель", deeds=150, seats=15, gold_percent=2, store_slots=18),
)


def a_guild(guild_id: int, name: str, *, founder: int, gold: int = 100_000) -> Guild:
    return Guild(
        id=guild_id,
        name=name,
        founder_id=founder,
        members=(GuildMember(founder, GuildRank.FOUNDER), GuildMember(founder + 100)),
        vault_gold=gold,
    )


def a_war(**over: int) -> War:
    return War(id=1, challenger_id=1, defender_id=2, stake=500, started=10, ends=13, **over)


def test_the_stake_grows_with_the_tier() -> None:
    """Ставка меряется боем уровня ступени: у выросшей гильдии она выше."""
    low = war_rules.war_stake(_TIERS, Standing(tier=_TIERS[0]))
    high = war_rules.war_stake(_TIERS, Standing(tier=_TIERS[1]))
    assert 0 < low < high


def test_a_war_runs_by_rotations_and_never_by_clock() -> None:
    war = a_war()
    assert war.rotations_left(10) == 3
    assert war.rotations_left(12) == 1
    assert war.rotations_left(99) == 0
    assert not war.due(12)
    assert war.due(13)
    assert not replace(war, over=True).due(13), "закрытую войну не подводят второй раз"


def test_only_the_foe_of_this_war_gives_a_point() -> None:
    war = a_war()
    assert war_rules.scores(winner_guild=1, loser_guild=2, war=war)
    assert war_rules.scores(winner_guild=2, loser_guild=1, war=war)
    assert not war_rules.scores(winner_guild=1, loser_guild=1, war=war), "свои не в счёт"
    assert not war_rules.scores(winner_guild=1, loser_guild=7, war=war), "чужие тоже"
    assert not war_rules.scores(winner_guild=1, loser_guild=2, war=None)
    assert not war_rules.scores(winner_guild=1, loser_guild=2, war=replace(war, over=True))


def test_the_higher_score_takes_both_stakes_and_a_draw_returns_them() -> None:
    won = a_war(challenger_score=7, defender_score=5)
    assert war_rules.winner_of(won) == 1
    assert war_rules.spoils(won) == {1: 1000, 2: 0}

    lost = a_war(challenger_score=2, defender_score=9)
    assert war_rules.winner_of(lost) == 2
    assert war_rules.spoils(lost) == {2: 1000, 1: 0}

    even = a_war(challenger_score=4, defender_score=4)
    assert war_rules.winner_of(even) == 0
    assert war_rules.spoils(even) == {1: 500, 2: 500}, "ставку возвращают: её сняли с казны"


def test_the_deeds_of_a_won_war_grow_with_the_tier() -> None:
    assert war_rules.war_deeds(Standing(tier=_TIERS[1])) > war_rules.war_deeds(
        Standing(tier=_TIERS[0])
    )


def test_only_the_founder_declares_and_accepts() -> None:
    guild = a_guild(1, "Стая", founder=10)
    assert war_rules.declares(guild, 10)
    assert not war_rules.declares(guild, 110)


def test_a_declaration_says_why_it_cannot_be_made() -> None:
    mine = a_guild(1, "Стая", founder=10)
    theirs = a_guild(2, "Медный Крест", founder=20)

    def refusal(**over: object) -> str:
        args: dict[str, object] = {
            "guild": mine,
            "actor_id": 10,
            "foe": theirs,
            "foe_name": theirs.name,
            "stake": 500,
            "at_war": False,
            "foe_at_war": False,
        }
        args.update(over)
        return war_rules.declare_refusal(**args)  # type: ignore[arg-type]

    assert refusal() == ""
    assert "основатель" in refusal(actor_id=110)
    assert "нет" in refusal(guild=None)
    assert "нет" in refusal(foe=None)
    assert "собой" in refusal(foe=mine)
    assert "уже воюете" in refusal(at_war=True)
    assert "уже воюет" in refusal(foe_at_war=True)
    assert "уже послан" in refusal(called=True)
    assert "казне" in refusal(guild=replace(mine, vault_gold=10))


def test_a_challenge_is_accepted_only_when_both_vaults_hold_the_stake() -> None:
    mine = a_guild(2, "Медный Крест", founder=20)
    challenger = a_guild(1, "Стая", founder=10)

    def refusal(**over: object) -> str:
        args: dict[str, object] = {
            "guild": mine,
            "actor_id": 20,
            "challenger": challenger,
            "stake": 500,
            "at_war": False,
            "challenger_at_war": False,
        }
        args.update(over)
        return war_rules.accept_refusal(**args)  # type: ignore[arg-type]

    assert refusal() == ""
    assert "основатель" in refusal(actor_id=120)
    assert "никто не вызывал" in refusal(challenger=None)
    assert "уже воюете" in refusal(at_war=True)
    assert "другую войну" in refusal(challenger_at_war=True)
    assert "казне" in refusal(guild=replace(mine, vault_gold=1))
    assert "ставки уже нет" in refusal(challenger=replace(challenger, vault_gold=1))


def test_a_war_knows_its_sides_and_their_score() -> None:
    war = a_war(challenger_score=3, defender_score=1)
    assert war.has(1) and war.has(2) and not war.has(3)
    assert war.foe_of(1) == 2
    assert war.foe_of(2) == 1
    assert war.foe_of(9) == 0
    assert war.score_of(1) == 3
    assert war.score_of(2) == 1
    assert war.score_of(9) == 0
