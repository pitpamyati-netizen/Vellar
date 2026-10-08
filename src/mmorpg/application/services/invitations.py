"""Приглашения с явным сроком, сохранённым отказом и защитой от повторов."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from mmorpg.application.operations import atomic_action
from mmorpg.domain.ports.repositories import StateCache

DAY = 86400
RETAIN = 365 * DAY


@dataclass(frozen=True, slots=True)
class Invitation:
    group_id: int
    inviter_id: int
    expires_at: int
    status: str = "pending"
    group_stamp: str = ""
    identity: str = ""

    def live(self, now: int) -> bool:
        return self.status == "pending" and now < self.expires_at


class Invitations:
    def __init__(self, cache: StateCache, kind: str, ttl: int = DAY) -> None:
        self._cache = cache
        self.kind = kind
        self.ttl = ttl

    def key(self, invitee_id: int) -> str:
        return f"social:call:{self.kind}:{invitee_id}"

    async def get(self, invitee_id: int) -> Invitation | None:
        raw = await self._cache.get(self.key(invitee_id))
        return Invitation(**json.loads(raw)) if raw else None

    async def blocked(self, owner_id: int, other_id: int) -> bool:
        return bool(await self._cache.get(f"social:block:{owner_id}:{other_id}"))

    @atomic_action
    async def block(self, owner_id: int, other_id: int, *, enabled: bool = True) -> None:
        key = f"social:block:{owner_id}:{other_id}"
        if enabled:
            await self._cache.set(key, "1", RETAIN)
            call = await self.get(owner_id)
            if call and call.inviter_id == other_id:
                await self.finish(owner_id, "blocked")
        else:
            await self._cache.delete(key)

    async def _save(self, invitee_id: int, call: Invitation) -> None:
        await self._cache.set(self.key(invitee_id), json.dumps(asdict(call)), RETAIN)

    @atomic_action
    async def send(
        self,
        group_id: int,
        inviter_id: int,
        invitee_id: int,
        *,
        now: int | None = None,
        group_stamp: str = "",
    ) -> str:
        now = int(time.time()) if now is None else now
        if inviter_id == invitee_id:
            return "Самого себя пригласить нельзя."
        if await self.blocked(invitee_id, inviter_id) or await self.blocked(inviter_id, invitee_id):
            return "Приглашение недоступно: между вами установлена блокировка."
        previous = await self.get(invitee_id)
        if previous and previous.live(now):
            return "У игрока уже есть приглашение. Дождитесь его решения; прежний зов сохранён."
        pair = f"social:cooldown:{self.kind}:{inviter_id}:{invitee_id}"
        next_send = int(await self._cache.get(pair) or "0")
        if now < next_send:
            return "Повторный зов пока недоступен. Позванный может сам запросить повтор."
        budget = f"social:budget:{inviter_id}"
        minute, count = json.loads(await self._cache.get(budget) or "[-1, 0]")
        used = int(count) if minute == now // 60 else 0
        if used >= 5:
            return "Вы уже отправили пять приглашений за минуту. Повторите позже."
        await self._cache.set(budget, json.dumps([now // 60, used + 1]), RETAIN)
        await self._cache.set(pair, str(now + 3600), RETAIN)
        await self._save(
            invitee_id,
            Invitation(
                group_id, inviter_id, now + self.ttl, group_stamp=group_stamp, identity=uuid4().hex
            ),
        )
        return ""

    @atomic_action
    async def finish(self, invitee_id: int, status: str = "declined") -> None:
        call = await self.get(invitee_id)
        if call:
            await self._save(invitee_id, replace(call, status=status))
        # Старый зов больше не импортируется после отказа.
        await self._cache.delete(f"{self.kind}-call:{invitee_id}")

    @atomic_action
    async def current(self, invitee_id: int, *, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        call = await self.get(invitee_id)
        if call is None:
            legacy = await self._cache.get(f"{self.kind}-call:{invitee_id}")
            if legacy:
                call = Invitation(
                    int(legacy),
                    int(legacy) if self.kind == "party" else 0,
                    now + self.ttl,
                    identity=uuid4().hex,
                )
                await self._save(invitee_id, call)
                await self._cache.delete(f"{self.kind}-call:{invitee_id}")
        if (
            call
            and call.live(now)
            and not await self.blocked(invitee_id, call.inviter_id)
            and not (await self.blocked(call.inviter_id, invitee_id))
        ):
            return call.group_id
        return 0

    @atomic_action
    async def repeat(self, invitee_id: int, *, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        call = await self.get(invitee_id)
        if call is None or call.status != "pending":
            return False
        if await self.blocked(invitee_id, call.inviter_id) or await self.blocked(
            call.inviter_id, invitee_id
        ):
            return False
        await self._save(invitee_id, replace(call, expires_at=now + self.ttl))
        return True

    async def explanation(self, invitee_id: int, *, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        call = await self.get(invitee_id)
        if call and call.status == "pending" and not call.live(now):
            command = "/отряд" if self.kind == "party" else "/гильдия"
            return (
                "Срок приглашения истёк. Вы ничего не потеряли. "
                f"Наберите «{command} повторить»: игра заново проверит свободное место."
            )
        return ""
