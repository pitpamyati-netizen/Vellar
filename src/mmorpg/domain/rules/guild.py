"""Гильдия: объединение игроков надолго, со званиями и общим хранилищем.

Отряд собирают ради одного боя (``domain/rules/party.py``); гильдия - другое.
Она держится месяцами, в ней десятки человек, у каждого звание, и у неё есть
общий кошелёк, из которого берут по званию, а не по тому, кто первым дошёл.

Три звания и ничего между ними:

- **основатель** - один. Заводит гильдию, распускает её, раздаёт и снимает
  звание, выгоняет. Уйти он не может, не распустив: гильдия без основателя -
  это гильдия без того, кто за неё отвечает.
- **офицер** - зовёт новых и берёт из казны. Столько, сколько назначит
  основатель.
- **участник** - состоит и кладёт в казну. Взять из неё не может.

Правила здесь - чистые: кто что вправе сделать и почему нельзя, если нельзя.
Хранение - ``GuildRepository``; casна двигается условным ``UPDATE``, как кошелёк
персонажа (``Claude.md``, правило 8).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

#: Сколько человек помещается в гильдию.
MAX_MEMBERS = 30

#: С какого уровня можно завести гильдию и сколько это стоит золотом.
FOUND_LEVEL = 10
FOUND_COST = 500

#: Границы имени гильдии: не короче трёх букв, не длиннее двадцати четырёх.
NAME_MIN = 3
NAME_MAX = 24


class GuildRank(IntEnum):
    """Звание в гильдии. Больше значение - больше прав."""

    MEMBER = 0
    OFFICER = 1
    FOUNDER = 2

    @property
    def title(self) -> str:
        return {
            GuildRank.MEMBER: "участник",
            GuildRank.OFFICER: "офицер",
            GuildRank.FOUNDER: "основатель",
        }[self]


@dataclass(frozen=True, slots=True)
class GuildMember:
    character_id: int
    rank: GuildRank


@dataclass(frozen=True, slots=True)
class Guild:
    """Гильдия целиком: имя, состав со званиями и казна.

    Ничего производного тут не хранится: имена участников и их уровни экран
    приносит отдельно, как и у отряда.
    """

    id: int
    name: str
    founder_id: int
    members: tuple[GuildMember, ...] = ()
    vault_gold: int = 0

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def full(self) -> bool:
        return self.size >= MAX_MEMBERS

    def has(self, character_id: int) -> bool:
        return any(one.character_id == character_id for one in self.members)

    def rank_of(self, character_id: int) -> GuildRank | None:
        for one in self.members:
            if one.character_id == character_id:
                return one.rank
        return None

    def can_invite(self, character_id: int) -> bool:
        rank = self.rank_of(character_id)
        return rank is not None and rank >= GuildRank.OFFICER

    def can_take_from_vault(self, character_id: int) -> bool:
        rank = self.rank_of(character_id)
        return rank is not None and rank >= GuildRank.OFFICER

    def with_member(self, character_id: int, rank: GuildRank = GuildRank.MEMBER) -> Guild:
        if self.has(character_id) or self.full:
            return self
        return replace(self, members=(*self.members, GuildMember(character_id, rank)))

    def without(self, character_id: int) -> Guild:
        return replace(
            self, members=tuple(one for one in self.members if one.character_id != character_id)
        )

    def with_rank(self, character_id: int, rank: GuildRank) -> Guild:
        return replace(
            self,
            members=tuple(
                GuildMember(one.character_id, rank) if one.character_id == character_id else one
                for one in self.members
            ),
        )


def name_refusal(name: str) -> str:
    """Пусто, когда имя годится; иначе - чем не годится."""
    trimmed = name.strip()
    if not (NAME_MIN <= len(trimmed) <= NAME_MAX):
        return f"Имя гильдии - от {NAME_MIN} до {NAME_MAX} знаков."
    if not any(ch.isalpha() for ch in trimmed):
        return "В имени гильдии должны быть буквы."
    return ""


def found_refusal(*, level: int, gold: int, in_guild: bool, name_taken: bool, name: str) -> str:
    """Пусто, когда завести гильдию можно; иначе - почему нельзя."""
    if in_guild:
        return "Вы уже в гильдии. Выйдите из неё, прежде чем заводить свою."
    if level < FOUND_LEVEL:
        return f"Гильдию заводят с {FOUND_LEVEL} уровня. Ваш: {level}."
    if gold < FOUND_COST:
        return f"На грамоту нужно {FOUND_COST} золота. У вас {gold}."
    if refusal := name_refusal(name):
        return refusal
    if name_taken:
        return "Гильдия с таким именем уже есть."
    return ""


def invite_refusal(
    *,
    guild: Guild | None,
    inviter_id: int,
    invitee_name: str,
    invitee_in_guild: bool,
) -> str:
    """Пусто, когда звать можно; иначе - почему нельзя, целой фразой."""
    if guild is None:
        return "У вас нет гильдии."
    if not guild.can_invite(inviter_id):
        return "Звать в гильдию может основатель или офицер."
    if guild.full:
        return f"В гильдии уже {MAX_MEMBERS} человек: больше не помещается."
    if invitee_in_guild:
        return f"{invitee_name} уже в гильдии."
    return ""


def rank_change_refusal(*, guild: Guild, actor_id: int, target_id: int, to: GuildRank) -> str:
    """Пусто, когда звание можно сменить; иначе - почему нельзя.

    Звание раздаёт и снимает только основатель, и только между «участник» и
    «офицер»: второго основателя не бывает, а передача гильдии - отдельное дело,
    которого в игре пока нет.
    """
    if guild.rank_of(actor_id) is not GuildRank.FOUNDER:
        return "Звание в гильдии раздаёт только основатель."
    if target_id == actor_id:
        return "Своё звание основатель не меняет."
    if guild.rank_of(target_id) is None:
        return "Этого человека нет в гильдии."
    if to not in (GuildRank.MEMBER, GuildRank.OFFICER):
        return "Звание можно поднять до офицера или опустить до участника."
    if guild.rank_of(target_id) is to:
        return "У него уже это звание."
    return ""


def kick_refusal(*, guild: Guild, actor_id: int, target_id: int) -> str:
    if target_id == actor_id:
        return "Себя из гильдии не выгоняют: её распускают."
    actor = guild.rank_of(actor_id)
    target = guild.rank_of(target_id)
    if target is None:
        return "Этого человека нет в гильдии."
    if actor is None or actor <= target:
        return "Выгнать можно только того, кто ниже вас званием."
    return ""


def withdraw_refusal(*, guild: Guild, actor_id: int, amount: int) -> str:
    if amount <= 0:
        return "Назовите сумму."
    if not guild.can_take_from_vault(actor_id):
        return "Из казны берёт основатель или офицер. Участник только кладёт."
    if amount > guild.vault_gold:
        return f"В казне только {guild.vault_gold}."
    return ""
