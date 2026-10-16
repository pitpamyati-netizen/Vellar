"""Прежний поход и снимок боя читаются новыми правилами после записи SQL."""

from dataclasses import replace
from uuid import uuid4

import pytest

from mmorpg.application.services.battle import BattleStore
from mmorpg.domain.entities.location import EnemyRole
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.infrastructure.persistence.gameplay import PostgresGameplayState
from mmorpg.presentation.telegram.flows.play import LocationSession, build_location, location_known
from tests.application.test_enemy_role_persistence import role_session

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_old_battle_retains_the_original_location_and_pre_merge_content(pool, content):
    battle_id = f"world-merge:{uuid4()}"
    hero_id = -(uuid4().int % (2**62))
    store = BattleStore(PostgresGameplayState(pool, InMemoryStateCache()))
    # Содержимое идущего до обновления боя: все пять мест открыты, диапазоны прежние.
    old_cities = tuple(
        replace(
            city,
            locations=tuple(replace(one, merged_into=0, habitats=()) for one in city.locations),
        )
        for city in content.cities
    )
    old_content = replace(
        content, cities=old_cities, _cities_by_id={one.id: one for one in old_cities}
    )
    session, _ = role_session(old_content, EnemyRole.HEALER, battle_id=battle_id, hero_id=hero_id)
    session = replace(session, city_id="farhold", slot=2, node=3, epoch=7)
    try:
        session = await store.pin(session, old_content)
        session = await store.save(session)
        reopened = BattleStore(PostgresGameplayState(pool, InMemoryStateCache()))
        restored = await reopened.load(battle_id)
        assert restored is not None
        assert (restored.city_id, restored.slot, restored.node, restored.epoch) == (
            "farhold",
            2,
            3,
            7,
        )
        pinned = await reopened.content(restored, content)
        assert len(pinned.city("farhold").active_locations) == 5
        assert len(content.city("farhold").active_locations) == 4
        visit = LocationSession(
            city_id=restored.city_id, slot=restored.slot, node=restored.node, epoch=restored.epoch
        )
        assert location_known(content, visit)
        assert build_location(content, "world-design", visit) == build_location(
            pinned, "world-design", visit
        )
        assert restored.state == session.state
    finally:
        await pool.execute(
            "DELETE FROM gameplay_state WHERE key = ANY($1::text[])",
            [BattleStore.key_of(battle_id), BattleStore.key_for_character(hero_id)],
        )
