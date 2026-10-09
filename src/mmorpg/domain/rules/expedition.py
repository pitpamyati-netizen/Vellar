"""Общий обработчик каталоговых встреч поверх существующего движка."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.content import Encounter, GameContent
from mmorpg.domain.entities.location import Enemy, EnemyRank
from mmorpg.domain.procgen.enemies import generate_enemy
from mmorpg.domain.procgen.seeds import derive


def meeting(content: GameContent, city_id: str, dungeon_id: str, layer: int) -> Encounter | None:
    if not content.has_city(city_id):
        return None
    city = content.city(city_id)
    if not city.has_dungeon(dungeon_id):
        return None
    encounters = city.dungeon(dungeon_id).encounters
    return encounters[layer] if 0 <= layer < len(encounters) else None


def foes(
    content: GameContent,
    encounter: Encounter,
    *,
    seed: bytes,
    level: int,
    stakes: float,
    bounty: float,
    participants: int = 1,
) -> tuple[Enemy, ...]:
    if not 1 <= participants <= 5:
        raise ValueError("Expedition supports one to five participants")
    by_id = {one.id: one for one in content.enemy_archetypes}
    generated = tuple(
        generate_enemy(
            derive(seed, "meeting", index),
            archetypes=(by_id[key],),
            biome=by_id[key].biomes[0],
            level=level,
            rank=EnemyRank(encounter.rank),
            members=len(encounter.enemies),
            stakes=stakes * encounter.stakes,
            bounty=bounty,
            dungeon=True,
        )
        for index, key in enumerate(encounter.enemies)
    )
    # Дополнительные герои увеличивают только запас сил противника, а не урон
    # или базовую награду: у менее быстрого товарища появляется время действовать.
    percent = 100 + encounter.health_per_extra_member * (participants - 1)
    return tuple(replace(one, max_health=one.max_health * percent // 100) for one in generated)
