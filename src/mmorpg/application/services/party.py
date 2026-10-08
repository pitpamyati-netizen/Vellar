"""Отряд: как он собирается и где лежит.

Правила отряда - в ``domain/rules/party.py``; здесь только те действия, из которых
оно состоит: завести, позвать, согласиться, уйти, расформировать.

Состав лежит в базе и держится, пока отряд не расформируют или пока из него не
уйдёт собравший (``PartyRepository``, ADR 0029): постоянный состав нельзя терять
между заходами. Приглашение действует сутки; постоянное игровое хранилище
сохраняет его после срока, чтобы объяснить поздний ответ и проверить повтор
(ADR 0092). Временный адаптер предназначен только для локальных проверок.
"""

from __future__ import annotations

import json
from uuid import uuid4

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.invitations import DAY, Invitations
from mmorpg.domain.ports.repositories import PartyRepository, StateCache
from mmorpg.domain.rules.party import Party

#: Сколько ждёт ответа зов.
CALL_TTL = DAY


class PartyStore:
    """Состав отрядов и приглашения с отдельным сроком действия."""

    def __init__(
        self, roster: PartyRepository, cache: StateCache, *, call_ttl: int = CALL_TTL
    ) -> None:
        self._roster = roster
        self._cache = cache
        self._call_ttl = call_ttl

    @property
    def invitations(self) -> Invitations:
        return Invitations(self._cache, "party", self._call_ttl)

    @staticmethod
    def _call_key(character_id: int) -> str:
        return f"party-call:{character_id}"

    async def of(self, character_id: int) -> Party | None:
        """Отряд, в котором стоит этот персонаж. ``None`` - он сам по себе."""
        return await self._roster.of(character_id)

    async def by_leader(self, leader_id: int) -> Party | None:
        return await self._roster.by_leader(leader_id)

    async def save(self, party: Party) -> None:
        previous = await self.by_leader(party.leader_id)
        if previous:
            for member in previous.members:
                if not party.has(member):
                    await self.reset_ready(party.leader_id, member)
        await self._roster.save(party)

    async def reset_ready(self, leader_id: int, member_id: int, *, close: bool = False) -> None:
        identity = await self._cache.get(f"recruitment:party:{leader_id}")
        key = f"recruitment:listing:{identity}" if identity else ""
        raw = await self._cache.get(key) if key else None
        if raw:
            listing = json.loads(raw)
            listing["ready"] = [one for one in listing["ready"] if one != member_id]
            if close:
                listing["status"] = "closed"
                listing["ready"] = []
            await self._cache.set(key, json.dumps(listing), 31536000)

    @atomic_action
    async def create(self, leader_id: int) -> Party | None:
        """Завести отряд. ``None`` - этот игрок уже в отряде.

        Отряд из одного человека - это отряд: он заведён нарочно, и звать в него
        можно с первой же минуты (``domain/rules/party.py``).
        """
        if await self._roster.of(leader_id) is not None:
            return None
        party = Party(leader_id=leader_id)
        await self._roster.save(party)
        await self._cache.set(f"social:party-generation:{leader_id}", uuid4().hex, 31536000)
        return party

    @atomic_action
    async def disband(self, party: Party) -> None:
        """Распустить отряд. Тот, кто ушёл последним, гасит свет."""
        await self._roster.disband(party.leader_id)
        await self.reset_ready(party.leader_id, party.leader_id, close=True)
        await self._cache.set(f"social:party-generation:{party.leader_id}", uuid4().hex, 31536000)

    @atomic_action
    async def call(
        self, *, leader_id: int, invitee_id: int, inviter_id: int = 0, now: int | None = None
    ) -> str:
        stamp = await self._cache.get(f"social:party-generation:{leader_id}") or ""
        return await self.invitations.send(
            leader_id, inviter_id or leader_id, invitee_id, now=now, group_stamp=stamp
        )

    async def called_by(self, invitee_id: int) -> int:
        """Кто зовёт этого персонажа. Ноль - никто."""
        return await self.invitations.current(invitee_id)

    async def forget_call(self, invitee_id: int) -> None:
        await self.invitations.finish(invitee_id)

    @atomic_action
    async def accept(self, invitee_id: int) -> Party | None:
        """Согласиться идти вместе. ``None`` - звать уже некому.

        Отряд к этому времени уже заведён: звать умеет только тот, у кого он
        есть. Распущенный, пока зов висел, отряд заново не собирается - зов
        просто оказался ни к чему.
        """
        leader_id = await self.called_by(invitee_id)
        if not leader_id or leader_id == invitee_id:
            return None
        if await self._roster.of(invitee_id) is not None:
            return None
        party = await self._roster.by_leader(leader_id)
        if party is None:
            return None
        call = await self.invitations.get(invitee_id)
        stamp = await self._cache.get(f"social:party-generation:{leader_id}") or ""
        if call and (call.group_stamp != stamp or not party.has(call.inviter_id)):
            return None
        if party.full or party.has(invitee_id):
            return party
        if await self.invitations.blocked(leader_id, invitee_id):
            return None
        for member in party.members:
            if await self.invitations.blocked(member, invitee_id) or await self.invitations.blocked(
                invitee_id, member
            ):
                return None
        joined = party.with_member(invitee_id)
        await self._roster.save(joined)
        await self.reset_ready(leader_id, invitee_id)
        await self.invitations.finish(invitee_id, "accepted")
        return joined

    @atomic_action
    async def leave(self, character_id: int) -> Party | None:
        """Уйти из отряда. Ушёл собравший - отряда больше нет.

        Оставшийся один собравший остаётся с заведённым отрядом: расформировать
        его - отдельное движение, а не побочный итог чужого ухода.
        """
        party = await self._roster.of(character_id)
        if party is None:
            return None
        left = party.without(character_id)
        await self.reset_ready(party.leader_id, character_id)
        if left.disbanded:
            await self.disband(party)
            return None
        await self._roster.save(left)
        return left
