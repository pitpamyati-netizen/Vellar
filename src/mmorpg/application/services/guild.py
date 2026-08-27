"""Гильдия: где лежит её состав и как в неё зовут.

Правила - в ``domain/rules/guild.py``; здесь только хранение и оркестровка:
завести, позвать, согласиться, уйти, распустить, сменить звание, выгнать,
подвинуть казну.

Состав лежит в базе (``GuildRepository``, ADR 0030): гильдию нельзя терять между
заходами. Приглашения - в кэше со сроком, как и в отряд: зов, который нельзя ни
принять, ни отменить, хуже, чем никакого.
"""

from __future__ import annotations

from mmorpg.domain.ports.repositories import GuildRepository, StateCache
from mmorpg.domain.rules.guild import Guild, GuildRank

#: Сколько ждёт ответа зов в гильдию.
CALL_TTL = 600


class GuildStore:
    """Гильдии (в базе) и незакрытые приглашения (в кэше со сроком)."""

    def __init__(
        self, roster: GuildRepository, cache: StateCache, *, call_ttl: int = CALL_TTL
    ) -> None:
        self._roster = roster
        self._cache = cache
        self._call_ttl = call_ttl

    @staticmethod
    def _call_key(character_id: int) -> str:
        return f"guild-call:{character_id}"

    async def of(self, character_id: int) -> Guild | None:
        return await self._roster.of(character_id)

    async def by_id(self, guild_id: int) -> Guild | None:
        return await self._roster.by_id(guild_id)

    async def by_name(self, name: str) -> Guild | None:
        return await self._roster.by_name(name)

    async def create(self, name: str, founder_id: int) -> Guild:
        return await self._roster.create(name, founder_id)

    async def disband(self, guild: Guild) -> None:
        await self._roster.disband(guild.id)

    async def save(self, guild: Guild) -> None:
        await self._roster.save(guild)

    async def deposit(self, guild: Guild, amount: int) -> None:
        await self._roster.deposit(guild.id, amount)

    async def withdraw(self, guild: Guild, amount: int) -> bool:
        return await self._roster.withdraw(guild.id, amount)

    async def call(self, *, guild_id: int, invitee_id: int) -> None:
        await self._cache.set(self._call_key(invitee_id), str(guild_id), self._call_ttl)

    async def called_to(self, invitee_id: int) -> int:
        guild_id = await self._cache.get(self._call_key(invitee_id))
        return int(guild_id) if guild_id else 0

    async def forget_call(self, invitee_id: int) -> None:
        await self._cache.delete(self._call_key(invitee_id))

    async def accept(self, invitee_id: int) -> Guild | None:
        """Согласиться вступить. ``None`` — звать уже некому или гильдии нет."""
        guild_id = await self.called_to(invitee_id)
        await self.forget_call(invitee_id)
        if not guild_id:
            return None
        guild = await self._roster.by_id(guild_id)
        if guild is None:
            return None
        if guild.full or guild.has(invitee_id):
            return guild
        joined = guild.with_member(invitee_id, GuildRank.MEMBER)
        await self._roster.save(joined)
        return joined

    async def leave(self, character_id: int) -> Guild | None:
        """Уйти из гильдии. Основатель так уйти не может — он её распускает."""
        guild = await self._roster.of(character_id)
        if guild is None or guild.founder_id == character_id:
            return None
        await self._roster.save(guild.without(character_id))
        return guild
