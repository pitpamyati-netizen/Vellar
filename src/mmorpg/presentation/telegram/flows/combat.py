"""The combat flow: one button press, one resolved turn.

The engine itself lives in ``domain.rules.combat``; this module maps button texts
onto :class:`CombatAction`, keeps the fight in a form that survives a round trip
through Redis, and decides which screen to show afterwards.

Every turn draws its randomness from ``blake2b(fight_seed, turn)``, so a fight is
reproducible from its seed and its sequence of actions.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionKind,
    ActionTag,
    CombatAction,
    CombatOutcome,
    CombatState,
    EnemyState,
    PlayerState,
    Trace,
)
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.effects import ActiveEffect, EffectStack
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank
from mmorpg.domain.procgen.enemies import generate_group
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules.combat import resolve_turn, start_combat
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.routing import Intent, resolve
from mmorpg.presentation.telegram.screens import combat as screens
from mmorpg.presentation.telegram.screens.base import Screen


@dataclass(frozen=True, slots=True)
class CombatSession:
    """An active fight, plus the seed it is being replayed from.

    ``depth`` is zero for a fight standing at a location node, and one to three
    for a descent into the city dungeons, where fights follow one another with no
    break in between.

    ``opponent`` is the character id of another player when this is a duel on a
    free location: the fight is against a snapshot of them, and the id is what
    the handler settles the stake against afterwards (``domain/rules/pvp.py``).
    """

    state: CombatState
    seed: bytes
    node: int = 0
    #: Волна узла, из которой вышел этот противник. Победа забирает единицу
    #: именно из неё: если узел успел смениться, забирать уже нечего
    #: (``domain/rules/nodes.py``).
    wave: int = 0
    depth: int = 0
    opponent: int = 0
    # A round of the arena. The stake is already paid when the fight opens,
    # and the outcome settles against the arena rather than against a player.
    arena: bool = False

    @property
    def in_descent(self) -> bool:
        return self.depth > 0

    @property
    def is_duel(self) -> bool:
        return self.opponent > 0

    def turn_seed(self) -> bytes:
        return derive(self.seed, "turn", self.state.turn)


def begin(
    content: GameContent,
    character: Character,
    enemies: tuple[Enemy, ...],
    *,
    seed: bytes,
    node: int,
    wave: int = 0,
    depth: int = 0,
    opponent: int = 0,
    arena: bool = False,
) -> CombatSession:
    return CombatSession(
        state=start_combat(content, character, enemies),
        seed=seed,
        node=node,
        wave=wave,
        depth=depth,
        opponent=opponent,
        arena=arena,
    )


def spawn_for_node(
    content: GameContent,
    *,
    seed: bytes,
    biome: str,
    level: int,
    rank: EnemyRank = EnemyRank.NORMAL,
) -> tuple[Enemy, ...]:
    """Who is standing at this node. Same seed, same opponents, every time."""
    return generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome=biome,
        level=level,
        rank=rank,
        elite_titles=content.elite_titles,
    )


def render(
    content: GameContent,
    character: Character,
    session: CombatSession,
    notice: str = "",
    extra: Sequence[str] = (),
    rows: Sequence[tuple[Label, ...]] = (),
    gold_lost: int = 0,
) -> Screen:
    state = session.state
    match state.outcome:
        case CombatOutcome.VICTORY:
            loot = tuple(
                content.item(item_id).name for item_id in state.loot if content.has_item(item_id)
            )
            return screens.victory_screen(state, extra=extra, rows=rows, loot=loot)
        case CombatOutcome.DEFEAT:
            return screens.defeat_screen(state, gold_lost)
        case CombatOutcome.FLED:
            return screens.escaped_screen(fled=True, state=state)
        case CombatOutcome.AVOIDED:
            return screens.escaped_screen(fled=False, state=state)
        case _:
            return screens.combat_screen(content, character, state, notice)


def action_for(
    content: GameContent, character: Character, session: CombatSession, text: str
) -> CombatAction | None:
    """Map a pressed button or typed command onto a combat action."""
    screen = render(content, character, session)
    command = resolve(text, screen)

    match command.intent:
        case Intent.ATTACK:
            return CombatAction(kind=ActionKind.ATTACK)
        case Intent.FLEE:
            return CombatAction(kind=ActionKind.FLEE)
        case Intent.RACIAL:
            return CombatAction(kind=ActionKind.RACIAL)
        case Intent.SKILL if command.number is not None:
            return CombatAction(kind=ActionKind.SKILL, slot=command.number - 1)
        case Intent.SELECT:
            return _action_from_label(content, character, session, command.argument)
        case _:
            return None


def _action_from_label(
    content: GameContent, character: Character, session: CombatSession, argument: str
) -> CombatAction | None:
    for slot in range(content.rules.active_slots):
        if argument.startswith(f"{slot + 1}."):
            return CombatAction(kind=ActionKind.SKILL, slot=slot)
    racial = screens.racial_label(content, character, session.state)
    if racial.matches(argument):
        return CombatAction(kind=ActionKind.RACIAL)
    # The label now carries its tag; the bare word a player typed before still works.
    if screens.attack_label().matches(argument) or labels.ATTACK.matches(argument):
        return CombatAction(kind=ActionKind.ATTACK)
    if labels.FLEE.matches(argument):
        return CombatAction(kind=ActionKind.FLEE)
    return None


def advance(
    content: GameContent, character: Character, session: CombatSession, text: str
) -> tuple[CombatSession, str]:
    """Resolve one turn. Returns the new session and a notice for unusable input."""
    if session.state.is_over:
        return session, ""

    action = action_for(content, character, session, text)
    if action is None:
        return session, "Не узнал действие. Нажмите кнопку из панели боя."

    state = resolve_turn(content, character, session.state, action, session.turn_seed())
    return replace(session, state=state), ""


# --- serialisation ----------------------------------------------------
#
# The fight lives in Redis with a TTL, so it has to survive JSON. Only what the
# engine needs is stored; everything derived is recomputed on load.


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


def serialise(session: CombatSession) -> str:
    state = session.state
    return json.dumps(
        {
            "seed": session.seed.hex(),
            "node": session.node,
            "wave": session.wave,
            "depth": session.depth,
            "opponent": session.opponent,
            "arena": session.arena,
            "turn": state.turn,
            "outcome": state.outcome.value,
            "experience": state.experience,
            "gold": state.gold,
            "loot": list(state.loot),
            "trace": [tag.value for tag in state.trace.tags],
            "player": {
                "name": state.player.name,
                "health": state.player.health,
                "max_health": state.player.max_health,
                "resource": state.player.resource,
                "max_resource": state.player.max_resource,
                "resource_name": state.player.resource_name,
                "shield": state.player.shield,
                "stunned": state.player.stunned,
                "free_cast": state.player.free_cast,
                "evade": state.player.evade_charges,
                "cooldowns": dict(state.player.cooldowns),
                "effects": _effects_to_json(state.player.effects),
            },
            "enemies": [
                {
                    "index": enemy.index,
                    "health": enemy.health,
                    "stunned": enemy.stunned,
                    "effects": _effects_to_json(enemy.effects),
                    "enemy": {
                        "archetype": enemy.enemy.archetype_id,
                        "name": enemy.enemy.name,
                        "kind": enemy.enemy.kind.value,
                        "level": enemy.enemy.level,
                        "max_health": enemy.enemy.max_health,
                        "damage": enemy.enemy.damage,
                        "armor": enemy.enemy.armor,
                        "initiative": enemy.enemy.initiative,
                        "rank": enemy.enemy.rank.value,
                        "loot": list(enemy.enemy.loot),
                        "gold": enemy.enemy.gold,
                    },
                }
                for enemy in state.enemies
            ],
        },
        ensure_ascii=False,
    )


def deserialise(raw: str) -> CombatSession:
    data = json.loads(raw)
    player_raw = data["player"]
    player = PlayerState(
        name=player_raw["name"],
        health=player_raw["health"],
        max_health=player_raw["max_health"],
        resource=player_raw["resource"],
        max_resource=player_raw["max_resource"],
        resource_name=player_raw["resource_name"],
        effects=_effects_from_json(player_raw["effects"]),
        cooldowns=MappingProxyType(dict(player_raw["cooldowns"])),
        shield=player_raw["shield"],
        stunned=player_raw["stunned"],
        free_cast=player_raw["free_cast"],
        evade_charges=player_raw["evade"],
    )
    enemies = tuple(
        EnemyState(
            enemy=Enemy(
                archetype_id=entry["enemy"]["archetype"],
                name=entry["enemy"]["name"],
                kind=EnemyKind(entry["enemy"]["kind"]),
                level=entry["enemy"]["level"],
                max_health=entry["enemy"]["max_health"],
                damage=entry["enemy"]["damage"],
                armor=entry["enemy"]["armor"],
                initiative=entry["enemy"]["initiative"],
                rank=EnemyRank(entry["enemy"]["rank"]),
                loot=tuple(entry["enemy"]["loot"]),
                gold=entry["enemy"]["gold"],
            ),
            health=entry["health"],
            effects=_effects_from_json(entry["effects"]),
            stunned=entry["stunned"],
            index=entry["index"],
        )
        for entry in data["enemies"]
    )
    state = CombatState(
        player=player,
        enemies=enemies,
        turn=data["turn"],
        outcome=CombatOutcome(data["outcome"]),
        experience=data["experience"],
        gold=data["gold"],
        loot=tuple(data["loot"]),
        trace=Trace(tuple(ActionTag(tag) for tag in data.get("trace", ()))),
    )
    return CombatSession(
        state=state,
        seed=bytes.fromhex(data["seed"]),
        node=data["node"],
        wave=int(data.get("wave", 0)),
        depth=int(data.get("depth", 0)),
        opponent=int(data.get("opponent", 0)),
        arena=bool(data.get("arena", False)),
    )
