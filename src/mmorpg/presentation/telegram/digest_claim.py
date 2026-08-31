"""Начисление надбавки за дело со сводки — раз за переворот прилавка (ADR 0053).

Дела сводки — чистая функция от ``(город, переворот, уровень)`` в
``domain/rules/digest.py``. Здесь то, чего домен не делает: разовость (ключ со
сроком в кэше, как у волн узлов и роамера) и выдача надбавки прямо там, где дело
закрылось, — победой в бою, приходом по дороге или пройденным логовом. Второе
сообщение об этом даёт вызывающий, как и об уровне.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg import economy_log
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.ports.repositories import StateCache
from mmorpg.domain.procgen.seeds import rotation_index, seconds_left_in_rotation
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules.digest import Deed
from mmorpg.domain.rules.progression import earned, grant_experience


@dataclass(frozen=True, slots=True)
class Claim:
    """Закрытое дело сводки: персонаж уже с надбавкой и фраза о ней."""

    character: Character
    line: str
    levelled: bool


def _key(character_id: int, rotation: int) -> str:
    return f"digest:{character_id}:{rotation}"


async def already_claimed(
    cache: StateCache, character_id: int, *, now: int, rotation_seconds: int
) -> bool:
    rotation = rotation_index(now, rotation_seconds)
    return await cache.get(_key(character_id, rotation)) is not None


def hunt_deed(deeds: tuple[Deed, ...], *, slot: int, archetype_ids: tuple[str, ...]) -> Deed | None:
    return next(
        (
            one
            for one in deeds
            if digest_rules.closes_hunt(one, slot=slot, archetype_ids=archetype_ids)
        ),
        None,
    )


def cull_deed(deeds: tuple[Deed, ...], slot: int) -> Deed | None:
    return next((one for one in deeds if digest_rules.closes_cull(one, slot=slot)), None)


def haul_deed(deeds: tuple[Deed, ...], city_id: str) -> Deed | None:
    return next((one for one in deeds if digest_rules.closes_haul(one, city_id=city_id)), None)


def delve_deed(
    deeds: tuple[Deed, ...], *, dungeon_id: str = "", roamer_cleared: bool = False
) -> Deed | None:
    return next(
        (
            one
            for one in deeds
            if digest_rules.closes_delve(one, dungeon_id=dungeon_id, roamer_cleared=roamer_cleared)
        ),
        None,
    )


async def claim(
    cache: StateCache,
    content: GameContent,
    character: Character,
    deed: Deed,
    *,
    now: int,
    rotation_seconds: int,
) -> Claim | None:
    """Отметить надбавку и выдать её. ``None`` — за этот переворот уже брали."""
    rotation = rotation_index(now, rotation_seconds)
    key = _key(character.id, rotation)
    if await cache.get(key) is not None:
        return None
    await cache.set(key, "1", seconds_left_in_rotation(now, rotation_seconds))

    gold, experience = digest_rules.reward(deed.level)
    given = character.with_gold(gold)
    grown, level_up = grant_experience(content, given, experience)
    economy_log.record(economy_log.DIGEST, gold, character_id=character.id)
    line = (
        f"Сводка заставы: дело закрыто ({deed.where}). "
        f"Надбавка: {gold} золота и {earned(content, given, experience)} опыта."
    )
    return Claim(character=grown, line=line, levelled=level_up.levels_gained > 0)
