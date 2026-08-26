"""Отряд: как он собирается и где лежит.

Правила отряда - в ``domain/rules/party.py``; здесь только хранение и те
действия, из которых оно состоит: завести, позвать, согласиться, уйти,
расформировать.

Всё со сроком. Отряд, о котором забыли, распадается сам через пару часов, а зов,
на который не ответили, - через несколько минут: висящее приглашение, которое
нельзя ни принять, ни отменить, хуже, чем никакого (``Claude.md``, правило 8).
"""

from __future__ import annotations

import json

from mmorpg.domain.ports.repositories import StateCache
from mmorpg.domain.rules.party import Party

#: Сколько живёт отряд без единого действия.
PARTY_TTL = 2 * 3600
#: И сколько ждёт ответа зов.
CALL_TTL = 300


class PartyStore:
    """Отряды и незакрытые приглашения."""

    def __init__(
        self, cache: StateCache, *, ttl: int = PARTY_TTL, call_ttl: int = CALL_TTL
    ) -> None:
        self._cache = cache
        self._ttl = ttl
        self._call_ttl = call_ttl

    @staticmethod
    def _party_key(leader_id: int) -> str:
        return f"party:{leader_id}"

    @staticmethod
    def _member_key(character_id: int) -> str:
        return f"party-of:{character_id}"

    @staticmethod
    def _call_key(character_id: int) -> str:
        return f"party-call:{character_id}"

    async def of(self, character_id: int) -> Party | None:
        """Отряд, в котором стоит этот персонаж. ``None`` - он сам по себе."""
        leader = await self._cache.get(self._member_key(character_id))
        if not leader:
            return None
        return await self.by_leader(int(leader))

    async def by_leader(self, leader_id: int) -> Party | None:
        raw = await self._cache.get(self._party_key(leader_id))
        if not raw:
            return None
        data = json.loads(raw)
        party = Party(
            leader_id=int(data["leader"]),
            members=tuple(int(one) for one in data["members"]),
        )
        return None if party.disbanded else party

    async def save(self, party: Party) -> None:
        if party.disbanded:
            await self.disband(party)
            return
        await self._cache.set(
            self._party_key(party.leader_id),
            json.dumps({"leader": party.leader_id, "members": list(party.members)}),
            self._ttl,
        )
        for member in party.members:
            await self._cache.set(self._member_key(member), str(party.leader_id), self._ttl)

    async def create(self, leader_id: int) -> Party | None:
        """Завести отряд. ``None`` - этот игрок уже в отряде.

        Отряд из одного человека - это отряд: он заведён нарочно, и звать в него
        можно с первой же минуты (``domain/rules/party.py``).
        """
        if await self.of(leader_id) is not None:
            return None
        party = Party(leader_id=leader_id)
        await self.save(party)
        return party

    async def disband(self, party: Party) -> None:
        """Распустить отряд. Тот, кто ушёл последним, гасит свет."""
        await self._cache.delete(self._party_key(party.leader_id))
        for member in party.members:
            await self._cache.delete(self._member_key(member))

    async def call(self, *, leader_id: int, invitee_id: int) -> None:
        """Позвать. Зов один: второй затирает первый, и это правильно."""
        await self._cache.set(self._call_key(invitee_id), str(leader_id), self._call_ttl)

    async def called_by(self, invitee_id: int) -> int:
        """Кто зовёт этого персонажа. Ноль - никто."""
        leader = await self._cache.get(self._call_key(invitee_id))
        return int(leader) if leader else 0

    async def forget_call(self, invitee_id: int) -> None:
        await self._cache.delete(self._call_key(invitee_id))

    async def accept(self, invitee_id: int) -> Party | None:
        """Согласиться идти вместе. ``None`` - звать уже некому.

        Отряд к этому времени уже заведён: звать умеет только тот, у кого он
        есть. Распущенный, пока зов висел, отряд заново не собирается - зов
        просто оказался ни к чему.
        """
        leader_id = await self.called_by(invitee_id)
        await self.forget_call(invitee_id)
        if not leader_id or leader_id == invitee_id:
            return None
        party = await self.by_leader(leader_id)
        if party is None:
            return None
        if party.full or party.has(invitee_id):
            return party
        joined = party.with_member(invitee_id)
        await self.save(joined)
        return joined

    async def leave(self, character_id: int) -> Party | None:
        """Уйти из отряда. Ушёл собравший - отряда больше нет.

        Оставшийся один собравший остаётся с заведённым отрядом: расформировать
        его - отдельное движение, а не побочный итог чужого ухода.
        """
        party = await self.of(character_id)
        if party is None:
            return None
        left = party.without(character_id)
        await self._cache.delete(self._member_key(character_id))
        if left.disbanded:
            await self.disband(party)
            return None
        await self.save(left)
        return left
