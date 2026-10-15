"""Настоящая запись PostgreSQL сохраняет повадки и продолжение боя."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.combat import ActionKind, BattleAction
from mmorpg.domain.entities.location import EnemyRole
from mmorpg.domain.rules.combat import act
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from tests.application.test_enemy_role_persistence import role_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.mark.parametrize("role", tuple(EnemyRole))
async def test_postgres_keeps_each_enemy_role_and_the_same_turns(pool, content, role):
    battle_id = f"role-preservation:{uuid4()}"
    hero_id = -(uuid4().int % (2**62))
    store = BattleStore(PostgresGameplayState(pool, InMemoryStateCache()))
    session, roster = role_session(content, role, battle_id=battle_id, hero_id=hero_id)
    direct = session.state
    try:
        session = await store.save(session)
        for step in range(4):
            # Новый адаптер не может получить состояние из прежней памяти.
            store = BattleStore(PostgresGameplayState(pool, InMemoryStateCache()))
            restored = await store.load(session.id)
            assert restored is not None
            assert restored.state == direct
            assert restored.state.by_id(2).enemy.role is role
            action = BattleAction(kind=ActionKind.DEFEND)
            seed = f"postgres-role-{step}".encode()
            direct = act(content, roster, direct, action, seed)
            actual = act(content, roster, restored.state, action, seed)
            assert actual == direct
            session = await store.save(replace(restored, state=actual))
    finally:
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key = ANY($1::text[])",
            [BattleStore.key_of(battle_id), BattleStore.key_for_character(hero_id)],
        )
