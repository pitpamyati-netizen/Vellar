"""Доска расширяет существующие отряды; все записи входят в общую операцию."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from mmorpg.application.operations import atomic_action
from mmorpg.application.services.battle import BattleStore
from mmorpg.application.services.invitations import DAY, RETAIN
from mmorpg.application.services.party import PartyStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import CharacterRepository
from mmorpg.domain.rules.party import LEVEL_WINDOW, Party
from mmorpg.domain.rules.recruitment import PACES, Goal, goals, level_refusal


@dataclass(frozen=True, slots=True)
class Listing:
    id: str
    leader_id: int
    goal_key: str
    pace: str
    low: int
    high: int
    expires_at: int
    group_stamp: str = ""
    ready: tuple[int, ...] = ()
    status: str = "open"


class Recruitment:
    def __init__(self, parties: PartyStore, characters: CharacterRepository) -> None:
        self._roster = parties._roster
        self._cache = parties._cache
        self.parties = parties
        self.characters = characters
        self.content: GameContent

    async def get(self, identity: str) -> Listing | None:
        raw = await self._cache.get(f"recruitment:listing:{identity}")
        if not raw:
            return None
        data = json.loads(raw)
        data["ready"] = tuple(data["ready"])
        return Listing(**data)

    async def _save(self, listing: Listing) -> None:
        await self._cache.set(
            f"recruitment:listing:{listing.id}", json.dumps(asdict(listing)), RETAIN
        )

    async def _index(self) -> list[str]:
        return list(json.loads(await self._cache.get("recruitment:board") or "[]"))

    async def for_party(self, leader_id: int) -> Listing | None:
        identity = await self._cache.get(f"recruitment:party:{leader_id}")
        listing = await self.get(identity) if identity else None
        stamp = await self._cache.get(f"social:party-generation:{leader_id}") or ""
        return listing if listing and listing.group_stamp == stamp else None

    async def goal(self, listing: Listing) -> Goal | None:
        leader = await self.characters.get(listing.leader_id)
        if leader is None:
            return None
        # Цель остаётся доступной для чтения, когда собравший перешёл в другой город.
        city = listing.goal_key.partition(":")[0]
        return next(
            (
                one
                for one in goals(self.content, replace(leader, city_id=city))
                if one.key == listing.goal_key
            ),
            None,
        )

    # Содержимое приносит вызывающий путь; оно никогда не читается с диска здесь.
    def with_content(self, content: GameContent) -> Recruitment:
        self.content = content
        return self

    @atomic_action
    async def board(self, *, now: int) -> tuple[Listing, ...]:
        result: list[Listing] = []
        for identity in await self._index():
            listing = await self.get(identity)
            if listing is None or listing.status != "open":
                continue
            party = await self.parties.by_leader(listing.leader_id)
            stamp = await self._cache.get(f"social:party-generation:{listing.leader_id}") or ""
            if (
                now >= listing.expires_at
                or party is None
                or await self.goal(listing) is None
                or (stamp != listing.group_stamp)
            ):
                await self._save(
                    replace(listing, status="expired" if now >= listing.expires_at else "closed")
                )
                continue
            result.append(listing)
        await self._cache.set("recruitment:board", json.dumps([one.id for one in result]), RETAIN)
        return tuple(result)

    @atomic_action
    async def publish(
        self,
        character: Character,
        goal_key: str,
        pace: str,
        *,
        now: int,
        low: int = 0,
        high: int = 0,
    ) -> tuple[Listing | None, str]:
        character = await self.characters.get(character.id) or character
        if await BattleStore(self._cache).busy(character.id):
            return None, "Сначала завершите текущий бой."
        goal = next((one for one in goals(self.content, character) if one.key == goal_key), None)
        if goal is None or pace not in PACES:
            return None, "Цель похода изменилась. Выберите её заново."
        low = low or max(1, character.level - LEVEL_WINDOW)
        high = high or min(150, character.level + LEVEL_WINDOW)
        if refusal := level_refusal(character.level, character.level, low, high):
            return None, refusal
        party = await self.parties.of(character.id)
        if party and party.leader_id != character.id:
            return None, "Набор публикует собравший отряд. Вы можете выйти и создать свой."
        if party is None:
            party = await self.parties.create(character.id)
        assert party is not None
        previous = await self.for_party(character.id)
        if previous and previous.status == "open" and now < previous.expires_at:
            stamp = await self._cache.get(f"social:party-generation:{character.id}") or ""
            if previous.group_stamp == stamp:
                return previous, "У вашего отряда уже есть набор. Закройте его перед новым."
        stamp = await self._cache.get(f"social:party-generation:{character.id}") or ""
        listing = Listing(
            uuid4().hex, character.id, goal.key, pace, low, high, now + DAY, group_stamp=stamp
        )
        await self._save(listing)
        await self._cache.set(f"recruitment:party:{character.id}", listing.id, RETAIN)
        index = [one.id for one in await self.board(now=now)]
        await self._cache.set("recruitment:board", json.dumps([*index, listing.id]), RETAIN)
        return listing, "Набор опубликован на сутки. Каждый участник сам подтверждает готовность."

    async def _refusal(self, listing: Listing, character: Character, party: Party) -> str:
        leader = await self.characters.get(party.leader_id)
        goal = await self.goal(listing)
        if leader is None or goal is None:
            return "Цель или отряд больше недоступны. Откройте доску заново."
        stamp = await self._cache.get(f"social:party-generation:{party.leader_id}") or ""
        if listing.group_stamp != stamp:
            return "Прежний отряд распущен. Выберите новый набор."
        if refusal := level_refusal(character.level, leader.level, listing.low, listing.high):
            return refusal
        if goal.key not in {
            one.key for one in goals(self.content, replace(character, city_id=goal.city_id))
        }:
            return "Эта цель ещё не открыта вашему герою. Выберите другой набор."
        for member_id in party.members:
            if await self.parties.invitations.blocked(
                member_id, character.id
            ) or await self.parties.invitations.blocked(character.id, member_id):
                return "В этом отряде есть игрок, с которым установлена блокировка."
        if await BattleStore(self._cache).busy(character.id):
            return "Сначала завершите текущий бой."
        return ""

    @atomic_action
    async def join(self, identity: str, character: Character, *, now: int) -> str:
        character = await self.characters.get(character.id) or character
        listing = await self.get(identity)
        if listing is None or listing.status != "open" or now >= listing.expires_at:
            return "Набор завершён или истёк. Вернитесь на доску и выберите другой."
        party = await self.parties.by_leader(listing.leader_id)
        if party is None:
            return "Отряд распущен. Выберите другой набор."
        existing = await self.parties.of(character.id)
        if existing:
            return (
                "Вы уже в этом отряде."
                if existing.leader_id == party.leader_id
                else ("Вы уже в другом отряде. Сначала выйдите из него.")
            )
        if party.full:
            return "Все места заняты. Выберите другой набор или вернитесь позже."
        if refused := await self._refusal(listing, character, party):
            return refused
        await self.parties.save(party.with_member(character.id))
        await self._save(
            replace(listing, ready=tuple(one for one in listing.ready if one != character.id))
        )
        return "Вы вступили в отряд. Осмотрите цель и подтвердите готовность, когда будете готовы."

    @atomic_action
    async def ready(self, identity: str, character: Character, *, enabled: bool) -> str:
        character = await self.characters.get(character.id) or character
        listing = await self.get(identity)
        party = await self.parties.of(character.id)
        if listing is None or party is None or party.leader_id != listing.leader_id:
            return "Вы больше не в этом отряде. Вернитесь на доску."
        current = await self.for_party(party.leader_id)
        if current is None or current.id != identity:
            return "Цель набора изменилась. Откройте свой набор и проверьте её заново."
        if enabled and (refused := await self._refusal(listing, character, party)):
            return refused
        if await BattleStore(self._cache).busy(character.id):
            return "Готовность следующего похода меняется после текущего боя."
        ready = tuple(one for one in listing.ready if one != character.id and party.has(one))
        await self._save(replace(listing, ready=(*ready, character.id) if enabled else ready))
        return "Готовность подтверждена." if enabled else "Готовность снята. Можно сделать перерыв."

    @atomic_action
    async def close(self, identity: str, actor_id: int) -> str:
        listing = await self.get(identity)
        if listing is None or listing.leader_id != actor_id:
            return "Закрыть набор может только собравший отряд."
        await self._save(replace(listing, status="closed"))
        return "Набор закрыт. Отряд и имущество участников сохранены."

    @atomic_action
    async def leave(self, character_id: int) -> None:
        party = await self.parties.of(character_id)
        if party and (listing := await self.for_party(party.leader_id)):
            await self._save(
                replace(
                    listing,
                    ready=tuple(one for one in listing.ready if one != character_id),
                    status="closed" if party.leader_id == character_id else listing.status,
                )
            )
        await self.parties.leave(character_id)

    @atomic_action
    async def block(self, character: Character, target_id: int, *, enabled: bool) -> str:
        if target_id == character.id or await self.characters.get(target_id) is None:
            return "Выберите другого существующего игрока."
        party = await self.parties.of(character.id)
        if party and party.has(target_id) and enabled:
            if await BattleStore(self._cache).busy(character.id) or await BattleStore(
                self._cache
            ).busy(target_id):
                return "Общий бой уже начат. Сначала выйдите из боя или завершите его."
            if party.leader_id == character.id:
                await self.leave(target_id)
            else:
                await self.leave(character.id)
        await self.parties.invitations.block(character.id, target_id, enabled=enabled)
        return (
            "Игрок заблокирован для приглашений и совместного набора."
            if enabled
            else ("Блокировка снята.")
        )

    async def companions(self, party: Party, *, goal_key: str) -> tuple[int, ...]:
        listing = await self.for_party(party.leader_id)
        if listing is None:
            return party.members
        if listing.goal_key != goal_key:
            return ()
        return tuple(one for one in party.members if one in listing.ready)
