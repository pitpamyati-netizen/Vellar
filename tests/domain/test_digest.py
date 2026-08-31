"""Сводка заставы: направленные дела — чистая функция от (город, переворот, уровень).

Тесты свойств здесь и есть спецификация: дела обязаны быть определёнными,
указывать на существующие места и породы и платить в объявленных границах
(ADR 0053, 0054).
"""

from __future__ import annotations

from mmorpg.domain.entities import GameContent
from mmorpg.domain.procgen.enemies import GOLD_BASE, GOLD_PER_LEVEL, candidates
from mmorpg.domain.rules import digest as digest_rules
from mmorpg.domain.rules.digest import DeedKind

WORLD_SEED = "vellar-test"


def _deeds(content: GameContent, city_id: str, rotation: int, level: int):
    return digest_rules.digest(content, WORLD_SEED, city_id, rotation, level)


def test_four_deeds_in_fixed_order(content: GameContent) -> None:
    deeds = _deeds(content, "farhold", 10, 8)
    assert len(deeds) == 4
    assert deeds[0].kind is DeedKind.HUNT
    assert deeds[1].kind is DeedKind.CULL
    assert deeds[3].kind is DeedKind.DELVE


def test_hunt_names_an_archetype_that_fits_its_place(content: GameContent) -> None:
    city = content.city("dusk_harbor")
    for deed in _deeds(content, "dusk_harbor", 7, 40):
        if deed.kind is not DeedKind.HUNT:
            continue
        biome = city.location(deed.slot).biome
        fitting = {one.id for one in candidates(content.enemy_archetypes, biome, dungeon=False)}
        assert deed.archetype_id in fitting


def test_deterministic_for_same_arguments(content: GameContent) -> None:
    assert _deeds(content, "farhold", 42, 12) == _deeds(content, "farhold", 42, 12)


def test_the_set_is_not_frozen_across_rotations(content: GameContent) -> None:
    seen = {tuple(_deeds(content, "dusk_harbor", rot, 40)) for rot in range(40)}
    assert len(seen) > 1


def test_city_and_level_change_the_set(content: GameContent) -> None:
    here = _deeds(content, "farhold", 42, 12)
    assert here != _deeds(content, "dusk_harbor", 42, 40)
    assert here != _deeds(content, "farhold", 42, 28)


def test_targets_are_real_places(content: GameContent) -> None:
    city = content.city("dusk_harbor")
    slots = {loc.slot for loc in city.locations}
    dungeons = {one.id for one in city.dungeons}
    for deed in _deeds(content, "dusk_harbor", 7, 40):
        if deed.kind in (DeedKind.CULL, DeedKind.HUNT):
            assert deed.slot in slots
        elif deed.kind is DeedKind.HAUL:
            assert content.has_city(deed.city_id) and deed.city_id != city.id
        else:
            assert deed.dungeon_id in dungeons


def test_haul_falls_back_when_no_neighbour_is_open(content: GameContent) -> None:
    # На первом городе на восьмом уровне открыт только он сам.
    deeds = _deeds(content, "farhold", 3, 8)
    assert all(deed.kind is not DeedKind.HAUL for deed in deeds)


def test_haul_points_at_an_open_neighbour(content: GameContent) -> None:
    hauls = [d for d in _deeds(content, "dusk_harbor", 5, 40) if d.kind is DeedKind.HAUL]
    assert hauls and hauls[0].city_id == "farhold"


def test_cull_deed_level_sits_inside_the_location_band(content: GameContent) -> None:
    city = content.city("farhold")
    for deed in _deeds(content, "farhold", 11, 50):
        if deed.kind is DeedKind.CULL:
            loc = city.location(deed.slot)
            assert loc.level_min <= deed.level <= loc.level_max


def test_reward_grows_with_level_and_stays_in_bounds(content: GameContent) -> None:
    low_gold, low_xp = digest_rules.reward(5)
    high_gold, high_xp = digest_rules.reward(50)
    assert 0 < low_gold < high_gold
    assert 0 < low_xp < high_xp
    # «Полтора-два обычных»: золото — ровно множитель от обычного боя уровня дела.
    plain = GOLD_BASE + GOLD_PER_LEVEL * 50
    assert 1.5 * plain <= high_gold <= 2.0 * plain


def test_closers_match_only_their_own_deed(content: GameContent) -> None:
    deeds = _deeds(content, "dusk_harbor", 5, 40)
    cull = next(d for d in deeds if d.kind is DeedKind.CULL)
    delve = next(d for d in deeds if d.kind is DeedKind.DELVE)
    hunt = next(d for d in deeds if d.kind is DeedKind.HUNT)

    ids = (hunt.archetype_id,)
    assert digest_rules.closes_hunt(hunt, slot=hunt.slot, archetype_ids=ids)
    assert not digest_rules.closes_hunt(hunt, slot=hunt.slot, archetype_ids=("nope",))
    assert not digest_rules.closes_hunt(hunt, slot=hunt.slot + 99, archetype_ids=ids)
    assert not digest_rules.closes_hunt(cull, slot=cull.slot, archetype_ids=ids)

    assert digest_rules.closes_cull(cull, slot=cull.slot)
    assert not digest_rules.closes_cull(cull, slot=cull.slot + 99)
    assert not digest_rules.closes_cull(delve, slot=cull.slot)

    assert digest_rules.closes_delve(delve, dungeon_id=delve.dungeon_id)
    assert digest_rules.closes_delve(delve, roamer_cleared=True)
    assert not digest_rules.closes_delve(delve, dungeon_id="nope")
    assert not digest_rules.closes_delve(cull, roamer_cleared=True)

    haul = next((d for d in deeds if d.kind is DeedKind.HAUL), None)
    assert haul is not None
    assert digest_rules.closes_haul(haul, city_id=haul.city_id)
    assert not digest_rules.closes_haul(haul, city_id="")
