"""Запись боя сохраняет повадку врага и следующий результат его действий."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mmorpg.application.services.battle import (
    BattleSession,
    BattleStore,
    begin,
    deserialise,
    serialise,
)
from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import ActionKind, BattleAction, EventKind
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRole
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules.combat import act
from mmorpg.infrastructure.cache.memory import InMemoryStateCache


def role_session(
    content: GameContent, role: EnemyRole, *, battle_id: str = "role-check", hero_id: int = 1
) -> tuple[BattleSession, dict[int, Character]]:
    character = Character(
        id=hero_id,
        user_id=hero_id,
        name="Проверка",
        race_id="human",
        class_id="warrior",
        level=30,
        allocated=StatBlock.from_mapping({"END": 100}),
    )
    foe = Enemy(
        archetype_id="role-check",
        name="Противник",
        kind=EnemyKind.HUMANOID,
        role=role,
        level=30,
        max_health=20_000,
        damage=3,
        armor=0,
        initiative=0,
        loot=(),
        gold=0,
    )
    session, roster = begin(
        content,
        battle_id=battle_id,
        attackers=[(character, True)],
        enemies=[foe, replace(foe, name="Раненый товарищ", role=EnemyRole.BRUTE)],
        seed=b"roles-survive-storage",
    )
    session = replace(
        session,
        state=replace(
            session.state,
            combatants=tuple(
                replace(one, health=one.max_health // 5) for one in session.state.combatants
            ),
        ),
    )
    return session, roster


@pytest.mark.parametrize("role", tuple(EnemyRole))
def test_every_enemy_role_survives_json_and_keeps_the_same_continuation(
    content: GameContent, role: EnemyRole
) -> None:
    session, roster = role_session(content, role)
    restored = deserialise(serialise(session))
    assert restored == session
    state, resumed = session.state, restored.state
    action = BattleAction(kind=ActionKind.DEFEND)
    events = []
    for step in range(6):
        state = act(content, roster, state, action, f"role-round-{step}".encode())
        resumed = act(content, roster, resumed, action, f"role-round-{step}".encode())
        assert resumed == state
        events.extend(state.events)
    if role in {EnemyRole.CASTER, EnemyRole.HEALER}:
        assert any(event.kind is EventKind.ROLE_MOVE and event.actor_id == 2 for event in events)
    if role is EnemyRole.WARRIOR:
        assert state.by_id(2).effects.has(StatusKind.GUARD)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", tuple(EnemyRole))
async def test_battle_store_preserves_roles_between_every_turn(
    content: GameContent, role: EnemyRole
) -> None:
    store = BattleStore(InMemoryStateCache())
    session, roster = role_session(content, role)
    direct = session.state
    session = await store.save(session)
    for step in range(6):
        restored = await store.load(session.id)
        assert restored is not None
        assert restored.state == direct
        action = BattleAction(kind=ActionKind.DEFEND)
        seed = f"stored-role-round-{step}".encode()
        direct = act(content, roster, direct, action, seed)
        actual = act(content, roster, restored.state, action, seed)
        assert actual == direct
        session = await store.save(replace(restored, state=actual))


def test_old_enemy_json_without_role_keeps_the_previous_brute_default(content: GameContent) -> None:
    session, _ = role_session(content, EnemyRole.WARRIOR)
    saved = json.loads(serialise(session))
    for one in saved["combatants"]:
        if one["enemy"]:
            del one["enemy"]["role"]
    restored = deserialise(json.dumps(saved))
    assert all(one.enemy.role is EnemyRole.BRUTE for one in restored.state.combatants if one.enemy)
