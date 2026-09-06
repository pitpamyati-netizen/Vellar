"""Гильдия: где лежит её состав, как в неё зовут и чем она растёт.

Правила - в ``domain/rules/guild.py``; здесь только хранение и оркестровка:
завести, позвать, согласиться, уйти, распустить, сменить звание, выгнать,
подвинуть казну, записать деяния и запомнить, сколько человек уже вынес из казны
за нынешний переворот прилавка (ADR 0076).

Состав лежит в базе (``GuildRepository``, ADR 0030): гильдию нельзя терять между
заходами. Приглашения - в кэше со сроком, как и в отряд: зов, который нельзя ни
принять, ни отменить, хуже, чем никакого.
"""

from __future__ import annotations

from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import GuildRepository, StateCache
from mmorpg.domain.procgen.seeds import rotation_index, seconds_left_in_rotation
from mmorpg.domain.rules import guild as guild_rules
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

    @staticmethod
    def _taken_key(guild_id: int, character_id: int, rotation: int) -> str:
        return f"guild-taken:{guild_id}:{character_id}:{rotation}"

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

    async def record_deeds(self, guild_id: int, character_id: int, deeds: int) -> None:
        """Записать деяния гильдии и вклад того, кто их сделал (ADR 0076)."""
        await self._roster.record_deeds(guild_id, character_id, deeds)

    async def taken(
        self, guild_id: int, character_id: int, *, now: int, rotation_seconds: int
    ) -> int:
        """Сколько этот человек уже вынес из казны за нынешний переворот прилавка.

        Счёт держит ключ со сроком до конца переворота - как разовость надбавки
        за дело сводки (``presentation/telegram/digest_claim.py``): предел
        выемки не стоит отдельной таблицы, а забыть его на перевороте - ровно то,
        что и задумано.
        """
        rotation = rotation_index(now, rotation_seconds)
        stored = await self._cache.get(self._taken_key(guild_id, character_id, rotation))
        return int(stored) if stored and stored.isdigit() else 0

    async def note_taken(
        self, guild_id: int, character_id: int, amount: int, *, now: int, rotation_seconds: int
    ) -> None:
        """Прибавить взятое к счёту переворота."""
        rotation = rotation_index(now, rotation_seconds)
        key = self._taken_key(guild_id, character_id, rotation)
        already = await self.taken(
            guild_id, character_id, now=now, rotation_seconds=rotation_seconds
        )
        await self._cache.set(
            key, str(already + max(0, amount)), seconds_left_in_rotation(now, rotation_seconds)
        )

    async def call(self, *, guild_id: int, invitee_id: int) -> None:
        await self._cache.set(self._call_key(invitee_id), str(guild_id), self._call_ttl)

    async def called_to(self, invitee_id: int) -> int:
        guild_id = await self._cache.get(self._call_key(invitee_id))
        return int(guild_id) if guild_id else 0

    async def forget_call(self, invitee_id: int) -> None:
        await self._cache.delete(self._call_key(invitee_id))

    async def accept(self, invitee_id: int, content: GameContent) -> Guild | None:
        """Согласиться вступить. ``None`` — звать уже некому или гильдии нет.

        Мест в гильдии столько, сколько даёт её ступень: пока зов висел, гильдия
        могла набраться под завязку (ADR 0076).
        """
        guild_id = await self.called_to(invitee_id)
        await self.forget_call(invitee_id)
        if not guild_id:
            return None
        guild = await self._roster.by_id(guild_id)
        if guild is None:
            return None
        seats = guild_rules.standing(content, guild).seats
        if guild.size >= seats or guild.has(invitee_id):
            return guild
        joined = guild.with_member(invitee_id, GuildRank.RECRUIT, seats=seats)
        await self._roster.save(joined)
        return joined

    async def leave(self, character_id: int) -> Guild | None:
        """Уйти из гильдии. Основатель так уйти не может — он её распускает."""
        guild = await self._roster.of(character_id)
        if guild is None or guild.founder_id == character_id:
            return None
        await self._roster.save(guild.without(character_id))
        return guild
