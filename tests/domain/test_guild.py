"""Гильдия: кто что вправе сделать и почему нельзя, если нельзя (ADR 0030)."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.rules import guild as guild_rules
from mmorpg.domain.rules.guild import Guild, GuildMember, GuildRank


def a_guild(**ranks: GuildRank) -> Guild:
    """Гильдия из именованных участников: ``a_guild(argus=FOUNDER, mira=OFFICER)``."""
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


def test_the_founder_and_officers_can_invite_members_cannot() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.OFFICER, tien=GuildRank.MEMBER)
    assert guild.can_invite(1) and guild.can_invite(2)
    assert not guild.can_invite(3)


def test_only_the_founder_hands_out_rank_and_only_between_member_and_officer() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.OFFICER, tien=GuildRank.MEMBER)
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=2, target_id=3, to=GuildRank.OFFICER)
        != ""
    ), "офицер звания не раздаёт"
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=3, to=GuildRank.OFFICER)
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


def test_kicking_reaches_only_downward() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.OFFICER, tien=GuildRank.MEMBER)
    assert guild_rules.kick_refusal(guild=guild, actor_id=2, target_id=3) == ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=2, target_id=1) != ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=3, target_id=2) != ""
    assert guild_rules.kick_refusal(guild=guild, actor_id=1, target_id=1) != ""


def test_the_vault_takes_from_officers_and_up_only() -> None:
    guild = replace(a_guild(argus=GuildRank.FOUNDER, tien=GuildRank.MEMBER), vault_gold=500)
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=200) == ""
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=2, amount=200) != ""
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=600) != ""
    assert guild_rules.withdraw_refusal(guild=guild, actor_id=1, amount=0) != ""


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


def test_a_member_added_carries_the_member_rank_and_leaving_shrinks_the_roster() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER)
    grown = guild.with_member(9)
    assert grown.rank_of(9) is GuildRank.MEMBER
    assert grown.without(9).rank_of(9) is None


def test_promotion_and_demotion_keep_everyone_else_in_place() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.MEMBER, tien=GuildRank.MEMBER)
    up = guild.with_rank(2, GuildRank.OFFICER)
    assert up.rank_of(2) is GuildRank.OFFICER
    assert up.rank_of(3) is GuildRank.MEMBER
    assert up.rank_of(1) is GuildRank.FOUNDER


def test_adding_someone_already_in_or_a_full_guild_changes_nothing() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER)
    assert guild.with_member(1) is guild, "уже в гильдии"
    packed = replace(
        guild,
        members=tuple(GuildMember(i, GuildRank.MEMBER) for i in range(guild_rules.MAX_MEMBERS)),
    )
    assert packed.with_member(999) is packed, "гильдия полна"


def test_invite_names_the_reason_it_refuses() -> None:
    assert (
        guild_rules.invite_refusal(
            guild=None, inviter_id=1, invitee_name="Тьен", invitee_in_guild=False
        )
        != ""
    )
    guild = a_guild(argus=GuildRank.FOUNDER, tien=GuildRank.MEMBER)
    assert "офицер" in guild_rules.invite_refusal(
        guild=guild, inviter_id=2, invitee_name="Кто-то", invitee_in_guild=False
    )
    assert (
        guild_rules.invite_refusal(
            guild=guild, inviter_id=1, invitee_name="Тьен", invitee_in_guild=True
        )
        != ""
    )
    packed = replace(
        guild,
        members=(
            GuildMember(1, GuildRank.FOUNDER),
            *(GuildMember(i + 10, GuildRank.MEMBER) for i in range(guild_rules.MAX_MEMBERS - 1)),
        ),
    )
    assert "помещается" in guild_rules.invite_refusal(
        guild=packed, inviter_id=1, invitee_name="Ещё", invitee_in_guild=False
    )


def test_rank_change_spells_out_every_no() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.OFFICER)
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=99, to=GuildRank.OFFICER)
        != ""
    ), "нет такого в гильдии"
    assert (
        guild_rules.rank_change_refusal(guild=guild, actor_id=1, target_id=2, to=GuildRank.OFFICER)
        != ""
    ), "у него уже это звание"


def test_kicking_someone_who_is_not_there_is_refused() -> None:
    guild = a_guild(argus=GuildRank.FOUNDER, mira=GuildRank.MEMBER)
    assert guild_rules.kick_refusal(guild=guild, actor_id=1, target_id=404) != ""
