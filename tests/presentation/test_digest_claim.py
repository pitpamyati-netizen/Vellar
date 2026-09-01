"""Надбавка за дело со сводки: раз за переворот, потом снова (ADR 0053)."""

from __future__ import annotations

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.infrastructure.cache.memory import InMemoryStateCache
from mmorpg.presentation.telegram import digest_claim

WORLD_SEED = "vellar-test"
ROTATION_SECONDS = 1_800


@pytest.fixture
def hero() -> Character:
    return Character(
        id=7, user_id=1, name="Аргус", race_id="human", class_id="warrior", level=40, gold=100
    )


def _deeds(content: GameContent, now: int, level: int = 40):
    rotation = now // ROTATION_SECONDS
    return digest_rules.digest(content, WORLD_SEED, "dusk_harbor", rotation, level)


async def test_claim_pays_once_and_then_refuses_within_the_rotation(
    content: GameContent, hero: Character
) -> None:
    cache = InMemoryStateCache()
    now = 1_800_000  # середина какого-то переворота
    deeds = _deeds(content, now)
    cull = next(d for d in deeds if d.kind is digest_rules.DeedKind.CULL)

    first = await digest_claim.claim(
        cache, content, hero, cull, now=now, rotation_seconds=ROTATION_SECONDS
    )
    assert first is not None
    assert first.character.gold > hero.gold  # надбавка золотом
    assert await digest_claim.already_claimed(
        cache, hero.id, now=now, rotation_seconds=ROTATION_SECONDS
    )

    second = await digest_claim.claim(
        cache, content, first.character, cull, now=now + 60, rotation_seconds=ROTATION_SECONDS
    )
    assert second is None


async def test_the_next_rotation_pays_again(content: GameContent, hero: Character) -> None:
    cache = InMemoryStateCache()
    early = 1_800_000
    later = early + ROTATION_SECONDS  # следующий переворот

    await digest_claim.claim(
        cache,
        content,
        hero,
        _deeds(content, early)[0],
        now=early,
        rotation_seconds=ROTATION_SECONDS,
    )
    again = await digest_claim.claim(
        cache,
        content,
        hero,
        _deeds(content, later)[0],
        now=later,
        rotation_seconds=ROTATION_SECONDS,
    )
    assert again is not None


async def test_deed_finders_match_the_right_deed(content: GameContent, hero: Character) -> None:
    deeds = _deeds(content, 1_800_000)
    cull = next(d for d in deeds if d.kind is digest_rules.DeedKind.CULL)
    delve = next(d for d in deeds if d.kind is digest_rules.DeedKind.DELVE)

    hunt = next(d for d in deeds if d.kind is digest_rules.DeedKind.HUNT)
    ids = (hunt.archetype_id,)
    assert digest_claim.hunt_deed(deeds, slot=hunt.slot, archetype_ids=ids) is hunt
    assert digest_claim.hunt_deed(deeds, slot=hunt.slot, archetype_ids=("nope",)) is None

    assert digest_claim.cull_deed(deeds, cull.slot) is cull
    assert digest_claim.cull_deed(deeds, 999) is None
    assert digest_claim.delve_deed(deeds, dungeon_id=delve.dungeon_id) is delve
    assert digest_claim.delve_deed(deeds, roamer_cleared=True) is delve
    haul = next((d for d in deeds if d.kind is digest_rules.DeedKind.HAUL), None)
    if haul is not None:
        assert digest_claim.haul_deed(deeds, haul.city_id) is haul

    search = next((d for d in deeds if d.kind is digest_rules.DeedKind.SEARCH), None)
    if search is not None:
        assert (
            digest_claim.search_deed(deeds, slot=search.slot, node_kind=search.node_kind) is search
        )
        assert digest_claim.search_deed(deeds, slot=search.slot, node_kind="shrine") is None
