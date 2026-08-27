"""Отряд: как он собирается и где лежит.

Правила отряда - в ``domain/rules/party.py``; здесь только те действия, из которых
оно состоит: завести, позвать, согласиться, уйти, расформировать.

Состав лежит в базе и держится, пока отряд не расформируют или пока из него не
уйдёт собравший (``PartyRepository``, ADR 0029): постоянный состав нельзя терять
между заходами. Приглашения - другое дело: они висят в кэше со сроком, потому
что зов, на который не ответили, лучше убрать самому, чем оставить висеть
(``Claude.md``, правило 8).
"""

from __future__ import annotations

from mmorpg.domain.ports.repositories import PartyRepository, StateCache
from mmorpg.domain.rules.party import Party

#: Сколько ждёт ответа зов.
CALL_TTL = 300


class PartyStore:
    """Отряды (в базе) и незакрытые приглашения (в кэше со сроком)."""

    def __init__(
        self, roster: PartyRepository, cache: StateCache, *, call_ttl: int = CALL_TTL
    ) -> None:
        self._roster = roster
        self._cache = cache
        self._call_ttl = call_ttl

    @staticmethod
    def _call_key(character_id: int) -> str:
        return f"party-call:{character_id}"

    async def of(self, character_id: int) -> Party | None:
        """Отряд, в котором стоит этот персонаж. ``None`` - он сам по себе."""
        return await self._roster.of(character_id)

    async def by_leader(self, leader_id: int) -> Party | None:
        return await self._roster.by_leader(leader_id)

    async def save(self, party: Party) -> None:
        await self._roster.save(party)

    async def create(self, leader_id: int) -> Party | None:
        """Завести отряд. ``None`` - этот игрок уже в отряде.

        Отряд из одного человека - это отряд: он заведён нарочно, и звать в него
        можно с первой же минуты (``domain/rules/party.py``).
        """
        if await self._roster.of(leader_id) is not None:
            return None
        party = Party(leader_id=leader_id)
        await self._roster.save(party)
        return party

    async def disband(self, party: Party) -> None:
        """Распустить отряд. Тот, кто ушёл последним, гасит свет."""
        await self._roster.disband(party.leader_id)

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
        party = await self._roster.by_leader(leader_id)
        if party is None:
            return None
        if party.full or party.has(invitee_id):
            return party
        joined = party.with_member(invitee_id)
        await self._roster.save(joined)
        return joined

    async def leave(self, character_id: int) -> Party | None:
        """Уйти из отряда. Ушёл собравший - отряда больше нет.

        Оставшийся один собравший остаётся с заведённым отрядом: расформировать
        его - отдельное движение, а не побочный итог чужого ухода.
        """
        party = await self._roster.of(character_id)
        if party is None:
            return None
        left = party.without(character_id)
        if left.disbanded:
            await self._roster.disband(party.leader_id)
            return None
        await self._roster.save(left)
        return left
