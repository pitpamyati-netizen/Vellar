"""Гильдия: кто что вправе сделать, чем она растёт и что даёт (ADR 0030, 0076)."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.content import GameContent, GuildTier
from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank


def a_guild(**ranks: GuildRank) -> Guild:
    """Гильдия из именованных участников: ``a_guild(argus=FOUNDER, mira=ELDER)``."""
    ids = {name: index + 1 for index, name in enumerate(ranks)}
    founder = next(cid for name, cid in ids.items() if ranks[name] is GuildRank.FOUNDER)
    return Guild(
        id=1,
        name="Стая",
        founder_id=founder,
        members=tuple(GuildMember(ids[name], rank) for name, rank in ranks.items()),
    )


def test_a_fresh_guild_is_just_its_founder() -> None:
    guild = Guild(id=1, name="Стая", founder_id=7).with_member(7, GuildRank.FOUNDER)
    assert guild.size == 1
    assert guild.rank_of(7) is GuildRank.FOUNDER


def test_veterans_and_up_can_invite_and_nobody_below_them() -> None:
    guild = a_guild(
        argus=GuildRank.FOUNDER,
        mira=GuildRank.ELDER,
        tien=GuildRank.VETERAN,
        doven=GuildRank.MEMBER,
        nov=GuildRank.RECRUIT,
    )
    assert guild.can_invite(1) and guild.can_invite(2) and guild.can_invite(3)
    assert not guild.can_invite(4)
    assert not guild.can_invite(5)


def test_only_the_founder_hands_out_rank_and_never_a_second_founder() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.ELDER, tien=GuildRank.MEMBER)
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=2, target_id=3, to=GuildRank.VETERAN)
        != ""
    ), "звания раздаёт не старейшина"
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=3, to=GuildRank.VETERAN)
        == ""
    )
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=1, to=GuildRank.MEMBER)
        != ""
    ), "своё звание основатель не трогает"
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=3, to=GuildRank.FOUNDER)
        != ""
    ), "второго основателя не бывает"


def test_rank_moves_one_step_at_a_time_and_stops_at_the_ends() -> None:
    assert guild_rules.raised(GuildRank.RECRUIT) is GuildRank.MEMBER
    assert guild_rules.raised(GuildRank.VETERAN) is GuildRank.ELDER
    assert guild_rules.raised(GuildRank.ELDER) is GuildRank.ELDER, "выше - только передача гильдии"
    assert guild_rules.lowered(GuildRank.ELDER) is GuildRank.VETERAN
    assert guild_rules.lowered(GuildRank.RECRUIT) is GuildRank.RECRUIT, "ниже новика выгоняют"


def test_kicking_reaches_only_downward_and_only_from_an_elder() -> None:
    guild = a_guild(
        argus=GuildRank.FOUNDER,
        mira=GuildRank.ELDER,
        tien=GuildRank.VETERAN,
        doven=GuildRank.MEMBER,
    )
    assert guild_rules.kick_refusal(guild=guild, actor_id=2, target_id=4) == ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=3, target_id=4) != "", "ветеран не гонит"
    assert guild_rules.kick_refusal(guild=guild, actor_id=2, target_id=1) != ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=1, target_id=1) != ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=1, target_id=404) != ""


def test_the_guild_is_handed_over_whole_and_the_old_founder_stays_an_elder() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.MEMBER)
    assert guild_rules.succeed_refusal(guild=guild, actor_id=2, target_id=1) != ""
    assert guild_rules.succeed_refusal(guild=guild, actor_id=1, target_id=1) != ""
    assert guild_rules.succeed_refusal(guild=guild, actor_id=1, target_id=404) != ""
    assert guild_rules.succeed_refusal(guild=None, actor_id=1, target_id=2) != ""
    assert guild_rules.succeed_refusal(guild=guild, actor_id=1, target_id=2) == ""

    handed = guild.succeeded_by(2)
    assert handed.founder_id == 2
    assert handed.rank_of(2) is GuildRank.FOUNDER
    assert handed.rank_of(1) is GuildRank.ELDER
    assert guild.succeeded_by(404) is guild, "постороннему гильдию не передают"


def test_the_vault_pays_by_rank_and_never_past_the_rotation_limit() -> None:
    guild = replace(
        a_guild(
            argus=GuildRank.FOUNDER,
            mira=GuildRank.MEMBER,
            nov=GuildRank.RECRUIT,
        ),
        vault_gold=1_000_000,
    )
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=200, level=20) == ""
    assert "не берёт" in guild_rules.withdraw_refusal(
        guild=guild, actor_id=3, amount=200, level=20
    ), "новик из казны не берёт"
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=0, level=20) != ""
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=404, amount=10, level=20) != ""

    limit = guild_rules.withdraw_limit(GuildRank.MEMBER, 20)
    fights = guild_rules.WITHDRAW_FIGHTS[GuildRank.MEMBER]
    assert limit is not None and limit == fights * guild_rules.fight_worth(20)
    # Предел растёт со званием и меряется боями, а не написанным числом.
    assert guild_rules.withdraw_limit(GuildRank.ELDER, 20) > limit
    assert guild_rules.withdraw_limit(GuildRank.MEMBER, 150) > limit
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=2, amount=limit, level=20) == ""
    assert (
        guild_rules.withdraw_refusal(guild=guild, actor_id=2, amount=limit + 1, level=20) != ""
    ), "за переворот участник берёт не больше предела"
    assert (
        guild_rules.withdraw_refusal(guild=guild, actor_id=2, amount=1, level=20, taken=limit) != ""
    ), "уже взятое считается"

    # У основателя предела нет вовсе, и потому у него нет и ряда «осталось».
    assert guild_rules.withdraw_limit(GuildRank.FOUNDER, 20) is None
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=900_000, level=1) == "", (
        "основатель берёт без предела"
    )


def test_the_vault_never_goes_below_what_is_in_it() -> None:
    guild = replace(a_guild(argus=GuildRank.FOUNDER), vault_gold=500)
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=600, level=20) != ""


def test_founding_is_gated_by_level_gold_and_a_free_name() -> None:
    assert (
        guild_rules.found_refusal(level=5, gold=999, in_guild=False, name_taken=False, name="Стая")
        != ""
    )
    assert (
        guild_rules.found_refusal(level=20, gold=100, in_guild=False, name_taken=False, name="Стая")
        != ""
    )
    assert (
        guild_rules.found_refusal(level=20, gold=999, in_guild=True, name_taken=False, name="Стая")
        != ""
    )
    assert (
        guild_rules.found_refusal(level=20, gold=999, in_guild=False, name_taken=True, name="Стая")
        != ""
    )
    assert (
        guild_rules.found_refusal(level=20, gold=999, in_guild=False, name_taken=False, name="ы")
        != ""
    )
    assert "буквы" in guild_rules.name_refusal("12345")
    assert (
        guild_rules.found_refusal(level=20, gold=999, in_guild=False, name_taken=False, name="Стая")
        == ""
    )


def test_a_member_added_is_a_recruit_and_leaving_shrinks_the_roster() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER)
    grown = guild.with_member(9)
    assert grown.rank_of(9) is GuildRank.RECRUIT
    assert grown.without(9).rank_of(9) is None


def test_promotion_and_demotion_keep_everyone_else_in_place() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.MEMBER, tien=GuildRank.MEMBER)
    up = guild.with_rank(2, GuildRank.VETERAN)
    assert up.rank_of(2) is GuildRank.VETERAN
    assert up.rank_of(3) is GuildRank.MEMBER
    assert up.rank_of(1) is GuildRank.FOUNDER


def test_a_full_guild_takes_nobody_and_seats_come_from_the_tier() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER)
    assert guild.with_member(1) is guild, "уже в гильдии"
    packed = replace(
        guild,
        members=tuple(GuildMember(i, GuildRank.MEMBER) for i in range(12)),
    )
    assert packed.with_member(999, seats=12) is packed, "мест на этой ступени больше нет"
    assert packed.with_member(999, seats=15).size == 13, "ступень выше вмещает больше"


def test_invite_names_the_reason_it_refuses(content: GameContent) -> None:
    empty = guild_rules.Standing(seats=12)
    assert (
        guild_rules.invite_refusal(
            guild=None, place=empty, inviter_id=1, invitee_name="Тьен", invitee_in_guild=False
        )
        != ""
    )
    guild = a_guild(argus=GuildRank.FOUNDER, tien=GuildRank.MEMBER)
    place = guild_rules.standing(content, guild)
    assert "ветеран" in guild_rules.invite_refusal(
        guild=guild, place=place, inviter_id=2, invitee_name="Кто-то", invitee_in_guild=False
    )
    assert (
        guild_rules.invite_refusal(
            guild=guild, place=place, inviter_id=1, invitee_name="Тьен", invitee_in_guild=True
        )
        != ""
    )
    packed = guild_rules.standing(
        content,
        replace(
            guild,
            members=tuple(GuildMember(i + 10, GuildRank.MEMBER) for i in range(place.seats)),
        ),
    )
    assert "места заняты" in guild_rules.invite_refusal(
        guild=guild, place=packed, inviter_id=1, invitee_name="Ещё", invitee_in_guild=False
    )


def test_rank_change_spells_out_every_no() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.ELDER)
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=99, to=GuildRank.VETERAN)
        != ""
    ), "нет такого в гильдии"
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=2, to=GuildRank.ELDER)
        != ""
    ), "у него уже это звание"


# --- гильдия растёт (ADR 0076) ---------------------------------------


def test_a_guild_climbs_the_ladder_by_deeds_and_the_ladder_is_content(
    content: GameContent,
) -> None:
    """Ступень считается из деяний, а не хранится: разойтись с базой ей негде."""
    ladder = content.guild_tiers
    assert ladder, "лестница ступеней объявлена в content/guilds.toml"

    fresh = guild_rules.standing(content, a_guild(argus=GuildRank.FOUNDER))
    assert fresh.tier is ladder[0]
    assert fresh.level == 1
    assert fresh.seats == ladder[0].seats
    assert fresh.next_tier is ladder[1]
    assert fresh.deeds_left == ladder[1].deeds

    grown = guild_rules.standing(
        content, replace(a_guild(argus=GuildRank.FOUNDER), deeds=ladder[1].deeds)
    )
    assert grown.tier is ladder[1]
    assert grown.deeds_left == ladder[2].deeds - ladder[1].deeds

    topped = guild_rules.standing(
        content, replace(a_guild(argus=GuildRank.FOUNDER), deeds=ladder[-1].deeds * 10)
    )
    assert topped.tier is ladder[-1]
    assert topped.next_tier is None and topped.deeds_left == 0
    assert topped.seats <= guild_rules.MAX_MEMBERS


def test_a_world_without_tiers_leaves_the_guild_where_it_stood(content: GameContent) -> None:
    """Содержимое переживает код: гильдия без ступеней держит состав и казну."""
    plain = content.rebuilt(guild_tiers=())
    place = guild_rules.standing(plain, a_guild(argus=GuildRank.FOUNDER))
    assert place.tier is None
    assert place.level == 1 and place.name == ""
    assert place.seats == guild_rules.MAX_MEMBERS
    assert not place.pays
    assert guild_rules.fight_bonus(place, experience=1000, gold=1000) == (0, 0)


def test_a_deposit_is_measured_in_fights_of_your_own_level() -> None:
    """Одна и та же плата значит одно и то же на первом уровне и на сто пятидесятом."""
    for level in (1, 20, 150):
        one_fight = guild_rules.fight_worth(level)
        assert guild_rules.deeds_for_deposit(one_fight, level) == 1
        assert guild_rules.deeds_for_deposit(one_fight * 7, level) == 7
        assert guild_rules.deeds_for_deposit(one_fight // 2, level) == 0
    assert guild_rules.deeds_for_deposit(0, 20) == 0
    assert guild_rules.deeds_for_deposit(-500, 20) == 0


def test_the_tier_pays_its_people_exactly_what_it_promises() -> None:
    tier = GuildTier(level=3, name="Братчина", deeds=400, seats=18, exp_percent=2, gold_percent=6)
    place = guild_rules.Standing(tier=tier, deeds=400, seats=18)
    assert place.pays
    assert guild_rules.fight_bonus(place, experience=1000, gold=500) == (20, 30)
    assert guild_rules.fight_bonus(place, experience=0, gold=0) == (0, 0)


def test_a_contribution_is_remembered_per_person() -> None:
    guild = Guild(
        id=1,
        name="Стая",
        founder_id=1,
        members=(GuildMember(1, GuildRank.FOUNDER, 300), GuildMember(2, GuildRank.MEMBER, 40)),
        deeds=340,
    )
    assert guild.contributed_by(1) == 300
    assert guild.contributed_by(2) == 40
    assert guild.contributed_by(404) == 0
    # Звание меняют, вклад остаётся: это память о том, кто держал гильдию.
    assert guild.with_rank(2, GuildRank.VETERAN).contributed_by(2) == 40
