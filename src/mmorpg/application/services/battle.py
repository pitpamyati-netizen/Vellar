"""Бой, который лежит один на всех.

Один бой - одна запись в общем хранилище, и у каждого его участника в данных
автомата только номер этой записи. Иначе поединок двоих был бы двумя разными
боями, каждый со своей правдой о том, чьё сейчас здоровье, и сходились бы они
только случайно (ADR 0021).

Здесь же живёт занятость: пока персонаж в бою, его нельзя вызвать во второй, и
проверяется это по той же записи, а не по чужому экрану.

Всё со сроком, как и всё, что игра кладёт в кэш (``Claude.md``, правило 8):
брошенный бой исчезает сам, и это не потеря - раны в нём остались
незаписанными, а персонаж цел.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionTag,
    BattleEvent,
    BattleOutcome,
    BattleState,
    Combatant,
    CombatantKind,
    EventKind,
    Trace,
)
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack
from mmorpg.domain.entities.location import DamageElement, Enemy, EnemyKind, EnemyRank
from mmorpg.domain.ports.repositories import StateCache
from mmorpg.domain.rules.combat import hero_combatant, monster_combatant, open_battle

#: Сколько живёт брошенный бой. Час: дольше держать чужие раны в подвешенном
#: состоянии незачем, а короче - и отлучившийся на обед теряет поединок.
BATTLE_TTL = 3600


class BattleKind(StrEnum):
    """Откуда взялся этот бой. Решает, чем за него платят."""

    NODE = "node"
    DESCENT = "descent"
    ARENA = "arena"
    DUEL = "duel"


@dataclass(frozen=True, slots=True)
class BattleSession:
    """Идущий бой и всё, что о нём нужно знать снаружи движка.

    ``owner`` - тот, чей это поход: по нему списывается волна узла и считается
    глубина спуска. Остальные идут с ним, а не вместо него.
    """

    id: str
    state: BattleState
    seed: bytes
    kind: BattleKind = BattleKind.NODE
    owner: int = 0
    city_id: str = ""
    slot: int = 0
    node: int = 0
    #: Волна узла, из которой вышел противник. Победа забирает единицу именно из
    #: неё: если узел успел смениться, забирать уже нечего.
    wave: int = 0
    depth: int = 0
    #: Расчёт после боя уже проведён: кто добил, тот и заплатил всем.
    settled: bool = False

    @property
    def in_descent(self) -> bool:
        return self.depth > 0

    @property
    def is_duel(self) -> bool:
        return self.kind is BattleKind.DUEL

    @property
    def is_arena(self) -> bool:
        return self.kind is BattleKind.ARENA

    def participants(self) -> tuple[Combatant, ...]:
        """Герои этого боя - все, за кем стоит персонаж."""
        return tuple(one for one in self.state.combatants if one.is_hero)

    def live_participants(self) -> tuple[Combatant, ...]:
        """Те, кому придёт сообщение о том, что случилось."""
        return tuple(one for one in self.participants() if one.live and one.user_id)

    def combatant_of(self, character_id: int) -> Combatant | None:
        for one in self.state.combatants:
            if one.is_hero and one.character_id == character_id:
                return one
        return None


def begin(
    content: GameContent,
    *,
    battle_id: str,
    attackers: Sequence[tuple[Character, bool]],
    defenders: Sequence[tuple[Character, bool]] = (),
    enemies: Sequence[Enemy] = (),
    seed: bytes,
    kind: BattleKind = BattleKind.NODE,
    owner: int = 0,
    city_id: str = "",
    slot: int = 0,
    node: int = 0,
    wave: int = 0,
    depth: int = 0,
) -> tuple[BattleSession, dict[int, Character]]:
    """Собрать бой из тех, кто в нём участвует.

    ``attackers`` и ``defenders`` - персонажи и то, живой ли за ними игрок:
    слепок противника на арене ходит сам, но дерётся своими умениями. Второй
    член ответа - персонажи по номерам бойцов: по нему движок читает умения и
    оружие (``domain/rules/combat.act``).
    """
    combatants: list[Combatant] = []
    roster: dict[int, Character] = {}
    next_id = 1

    for character, live in attackers:
        combatants.append(
            hero_combatant(content, character, combatant_id=next_id, side=0, live=live)
        )
        roster[next_id] = character
        next_id += 1

    for character, live in defenders:
        combatants.append(
            hero_combatant(content, character, combatant_id=next_id, side=1, live=live)
        )
        roster[next_id] = character
        next_id += 1

    for enemy in enemies:
        combatants.append(monster_combatant(enemy, combatant_id=next_id, side=1))
        next_id += 1

    state = open_battle(content, roster, combatants, seed)
    session = BattleSession(
        id=battle_id,
        state=state,
        seed=seed,
        kind=kind,
        owner=owner,
        city_id=city_id,
        slot=slot,
        node=node,
        wave=wave,
        depth=depth,
    )
    return session, roster


def roster_for(session: BattleSession, characters: Mapping[int, Character]) -> dict[int, Character]:
    """Персонажи по номерам бойцов; ``characters`` - по номерам персонажей."""
    return {
        one.id: characters[one.character_id]
        for one in session.state.combatants
        if one.is_hero and one.character_id in characters
    }


class BattleStore:
    """Где лежит бой и кто в нём занят.

    Две записи на бой: сам бой и по строке на каждого участника - «этот в том
    бою». Вторая нужна не для скорости, а для правила: во второй бой человека не
    зовут, пока не кончился первый.
    """

    def __init__(self, cache: StateCache, *, ttl: int = BATTLE_TTL) -> None:
        self._cache = cache
        self._ttl = ttl

    @staticmethod
    def key_of(battle_id: str) -> str:
        return f"battle:{battle_id}"

    @staticmethod
    def key_for_character(character_id: int) -> str:
        return f"battle-of:{character_id}"

    async def load(self, battle_id: str) -> BattleSession | None:
        raw = await self._cache.get(self.key_of(battle_id))
        return deserialise(raw) if raw else None

    async def save(self, session: BattleSession) -> None:
        await self._cache.set(self.key_of(session.id), serialise(session), self._ttl)
        for one in session.participants():
            if one.character_id:
                await self._cache.set(
                    self.key_for_character(one.character_id), session.id, self._ttl
                )

    async def busy(self, character_id: int) -> str | None:
        """Номер боя, в котором этот персонаж сейчас стоит. ``None`` - свободен."""
        battle_id = await self._cache.get(self.key_for_character(character_id))
        if not battle_id:
            return None
        session = await self.load(battle_id)
        if session is None or session.state.is_over:
            await self._cache.delete(self.key_for_character(character_id))
            return None
        return battle_id

    async def release(self, session: BattleSession) -> None:
        """Бой кончен и рассчитан: занятость снята, а запись ещё стоит.

        Стоит она затем, что экран итога - настоящий экран: с него жмут «Идти
        глубже» и «Главное меню», и хендлеру нужно прочитать, чем всё кончилось.
        Убирает её тот, кто с этого экрана уходит (``_leave_to_play``).
        """
        await self._cache.set(self.key_of(session.id), serialise(session), self._ttl)
        for one in session.participants():
            if one.character_id:
                await self._cache.delete(self.key_for_character(one.character_id))

    async def forget(self, session: BattleSession) -> None:
        """Убрать кончившийся бой и снять занятость со всех его участников."""
        await self._cache.delete(self.key_of(session.id))
        for one in session.participants():
            if one.character_id:
                await self._cache.delete(self.key_for_character(one.character_id))


# --- дорога через хранилище -------------------------------------------
#
# Бой лежит в общем хранилище со сроком, поэтому он обязан пережить JSON.
# Хранится только то, что нужно движку; всё производное считается заново.


def _effects_to_json(stack: EffectStack) -> list[dict[str, object]]:
    return [
        {
            "id": effect.id,
            "name": effect.name,
            "modifiers": dict(effect.modifiers),
            "turns": effect.turns_left,
            "good": effect.beneficial,
        }
        for effect in stack
    ]


def _effects_from_json(raw: list[dict[str, Any]]) -> EffectStack:
    stack = EffectStack()
    for entry in raw:
        stack = stack.apply(
            ActiveEffect(
                id=str(entry["id"]),
                name=str(entry["name"]),
                modifiers={str(key): float(value) for key, value in entry["modifiers"].items()},
                turns_left=int(entry["turns"]),
                beneficial=bool(entry["good"]),
            )
        )
    return stack


def _enemy_to_json(enemy: Enemy) -> dict[str, object]:
    return {
        "archetype": enemy.archetype_id,
        "name": enemy.name,
        "kind": enemy.kind.value,
        "level": enemy.level,
        "max_health": enemy.max_health,
        "damage": enemy.damage,
        "armor": enemy.armor,
        "initiative": enemy.initiative,
        "rank": enemy.rank.value,
        "loot": list(enemy.loot),
        "gold": enemy.gold,
        "element": enemy.element.value,
    }


def _enemy_from_json(raw: Mapping[str, Any]) -> Enemy:
    return Enemy(
        archetype_id=str(raw["archetype"]),
        name=str(raw["name"]),
        kind=EnemyKind(raw["kind"]),
        level=int(raw["level"]),
        max_health=int(raw["max_health"]),
        damage=int(raw["damage"]),
        armor=int(raw["armor"]),
        initiative=float(raw["initiative"]),
        rank=EnemyRank(raw["rank"]),
        loot=tuple(raw["loot"]),
        gold=int(raw["gold"]),
        element=DamageElement(raw.get("element", DamageElement.PHYSICAL.value)),
    )


def _combatant_to_json(one: Combatant) -> dict[str, object]:
    return {
        "id": one.id,
        "side": one.side,
        "kind": one.kind.value,
        "name": one.name,
        "level": one.level,
        "max_health": one.max_health,
        "health": one.health,
        "max_resource": one.max_resource,
        "resource": one.resource,
        "resource_name": one.resource_name,
        "initiative": one.initiative,
        "live": one.live,
        "character_id": one.character_id,
        "user_id": one.user_id,
        "enemy": _enemy_to_json(one.enemy) if one.enemy is not None else None,
        "effects": _effects_to_json(one.effects),
        "cooldowns": dict(one.cooldowns),
        "shield": one.shield,
        "stunned": one.stunned,
        "free_cast": one.free_cast,
        "evade": one.evade_charges,
        "trace": [tag.value for tag in one.trace.tags],
        "focus": one.focus,
        "left": one.left,
        "breached": one.breached,
    }


def _combatant_from_json(raw: Mapping[str, Any]) -> Combatant:
    return Combatant(
        id=int(raw["id"]),
        side=int(raw["side"]),
        kind=CombatantKind(raw["kind"]),
        name=str(raw["name"]),
        level=int(raw["level"]),
        max_health=int(raw["max_health"]),
        health=int(raw["health"]),
        max_resource=int(raw["max_resource"]),
        resource=int(raw["resource"]),
        resource_name=str(raw["resource_name"]),
        initiative=float(raw["initiative"]),
        live=bool(raw["live"]),
        character_id=int(raw["character_id"]),
        user_id=int(raw["user_id"]),
        enemy=_enemy_from_json(raw["enemy"]) if raw["enemy"] else None,
        effects=_effects_from_json(raw["effects"]),
        cooldowns=MappingProxyType(
            {str(key): int(value) for key, value in raw["cooldowns"].items()}
        ),
        shield=int(raw["shield"]),
        stunned=int(raw["stunned"]),
        free_cast=bool(raw["free_cast"]),
        evade_charges=int(raw["evade"]),
        trace=Trace(tuple(ActionTag(tag) for tag in raw.get("trace", ()))),
        focus=int(raw.get("focus", 0)),
        left=bool(raw.get("left", False)),
        breached=bool(raw.get("breached", False)),
    )


def _event_to_json(event: BattleEvent) -> dict[str, object]:
    return {
        "kind": event.kind.value,
        "actor_id": event.actor_id,
        "target_id": event.target_id,
        "actor": event.actor,
        "target": event.target,
        "amount": event.amount,
        "skill": event.skill_name,
        "effect": event.effect_name,
        "turns": event.turns,
    }


def _event_from_json(raw: Mapping[str, Any]) -> BattleEvent:
    return BattleEvent(
        kind=EventKind(raw["kind"]),
        actor_id=int(raw["actor_id"]),
        target_id=int(raw["target_id"]),
        actor=str(raw["actor"]),
        target=str(raw["target"]),
        amount=int(raw["amount"]),
        skill_name=str(raw["skill"]),
        effect_name=str(raw["effect"]),
        turns=int(raw["turns"]),
    )


def serialise(session: BattleSession) -> str:
    state = session.state
    return json.dumps(
        {
            "id": session.id,
            "seed": session.seed.hex(),
            "battle_kind": session.kind.value,
            "owner": session.owner,
            "city_id": session.city_id,
            "slot": session.slot,
            "node": session.node,
            "wave": session.wave,
            "depth": session.depth,
            "settled": session.settled,
            "round": state.round,
            "order": list(state.order),
            "cursor": state.cursor,
            "outcome": state.outcome.value,
            "winner": state.winner,
            "experience": state.experience,
            "gold": state.gold,
            "loot": list(state.loot),
            "events": [_event_to_json(event) for event in state.events],
            "combatants": [_combatant_to_json(one) for one in state.combatants],
        },
        ensure_ascii=False,
    )


def deserialise(raw: str) -> BattleSession:
    data = json.loads(raw)
    state = BattleState(
        combatants=tuple(_combatant_from_json(entry) for entry in data["combatants"]),
        order=tuple(int(one) for one in data["order"]),
        cursor=int(data["cursor"]),
        round=int(data["round"]),
        outcome=BattleOutcome(data["outcome"]),
        winner=int(data["winner"]),
        events=tuple(_event_from_json(entry) for entry in data.get("events", ())),
        experience=int(data["experience"]),
        gold=int(data["gold"]),
        loot=tuple(data["loot"]),
    )
    return BattleSession(
        id=str(data["id"]),
        state=state,
        seed=bytes.fromhex(data["seed"]),
        kind=BattleKind(data.get("battle_kind", BattleKind.NODE.value)),
        owner=int(data.get("owner", 0)),
        city_id=str(data.get("city_id", "")),
        slot=int(data.get("slot", 0)),
        node=int(data.get("node", 0)),
        wave=int(data.get("wave", 0)),
        depth=int(data.get("depth", 0)),
        settled=bool(data.get("settled", False)),
    )


def settled(session: BattleSession) -> BattleSession:
    """Пометить бой рассчитанным: платят по нему один раз."""
    return replace(session, settled=True)
