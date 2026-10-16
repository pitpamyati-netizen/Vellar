"""Уплотнение мира сохраняет занятия, старые карты и достижимые задания."""

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.procgen.enemies import candidates, generate_group, role_of
from mmorpg.domain.procgen.location import generate_location
from mmorpg.domain.rules import digest, overlay, recruitment
from mmorpg.domain.rules.crafts import yields_here
from mmorpg.presentation.telegram.flows.play import (
    Clock,
    LocationSession,
    PlayState,
    advance,
    build_location,
    location_known,
    render,
)
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.quests import where_line


def test_safe_destinations_cover_every_city_level(content: GameContent) -> None:
    for city in content.cities:
        safe = [one for one in city.active_locations if not one.pvp]
        levels = {level for one in safe for level in range(one.level_min, one.level_max + 1)}
        assert set(range(city.level_min, city.level_max + 1)) <= levels
        assert len(safe) == 3
        assert city.destination(2).slot == 1
        assert city.location(2).biomes <= city.destination(2).biomes


def test_local_pools_have_real_diversity_without_universal_filler(content: GameContent) -> None:
    for city in content.cities:
        for location in city.locations:
            pool = candidates(content.enemy_archetypes, location.biomes)
            assert len(pool) >= 3, location.id
            assert len({role_of(one) for one in pool}) >= 2, location.id
            assert all("*" not in one.biomes for one in pool), location.id
    assert candidates(content.enemy_archetypes, "неизвестная местность")
    assert candidates(content.enemy_archetypes, "неизвестная местность", dungeon=True)


@pytest.mark.parametrize("underground", [False, True])
def test_groups_offer_different_species_and_remain_deterministic(
    content: GameContent, underground: bool
) -> None:
    biome = "штольни" if underground else "луга"
    seen_sizes = set()
    for index in range(100):
        seed = f"world-design-pack-{index}".encode()

        def generate(seed=seed):
            return generate_group(
                seed,
                archetypes=content.enemy_archetypes,
                biome=biome,
                level=30,
                dungeon=underground,
            )

        pack = generate()
        assert pack == generate()
        assert len({one.archetype_id for one in pack}) == len(pack)
        seen_sizes.add(len(pack))
        assert all(one.rank is EnemyRank.NORMAL for one in pack)
    assert seen_sizes == {1, 2, 3}


def test_legacy_map_keeps_its_topology_and_can_resume(content: GameContent) -> None:
    for city in content.cities:
        old = city.location(2)
        session = LocationSession(city_id=city.id, slot=2, node=3, epoch=7)
        assert location_known(content, session)
        actual = build_location(content, "world-design-test", session)
        previous = generate_location(
            world_seed="world-design-test",
            city_id=city.id,
            slot=2,
            name=old.name,
            biome=old.biome,
            level_min=old.level_min,
            level_max=old.level_max,
            epoch=7,
        )
        assert actual == previous
        assert actual.is_connected


def test_new_list_and_old_buttons_explain_the_combined_destination(content: GameContent) -> None:
    hero = Character(id=1, user_id=1, name="Аргус", race_id="human", class_id="warrior", level=10)
    flow = PlayState(screen=ScreenId.LOCATION_LIST, city_id="farhold")
    screen = render(content, hero, flow, world_seed="world-design-test", clock=Clock(now=1_000))
    buttons = [button.text for row in screen.rows for button in row]
    assert any(button.startswith("1. Луга у Заставы") for button in buttons)
    assert not any(button.startswith("2. Ольшаник") for button in buttons)
    assert "Ольшаник" in " ".join(screen.lines)
    rejected = advance(
        content, hero, flow, "2. Ольшаник", world_seed="world-design-test", clock=Clock(now=1_000)
    )
    assert rejected.screen is ScreenId.LOCATION_LIST
    assert not rejected.session.active
    assert "теперь входит" in rejected.notice
    old_flow = replace(
        flow, screen=ScreenId.LOCATION, session=LocationSession(city_id="farhold", slot=2, node=1)
    )
    continued = render(
        content, hero, old_flow, world_seed="world-design-test", clock=Clock(now=1_000)
    )
    assert continued.id is ScreenId.LOCATION
    assert "Ольшаник" in continued.lines[0]


def test_quest_instructions_and_recruitment_use_open_entrances(content: GameContent) -> None:
    for quest in content.quests:
        if quest.is_trial or not quest.location_slot:
            continue
        city = content.city(quest.city_id)
        destination = city.destination(quest.location_slot)
        assert f"{destination.slot}. {destination.name}" in where_line(content, quest)
        assert not destination.merged_into
    for city in content.cities:
        hero = Character(
            id=1,
            user_id=1,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            level=city.level_max,
            city_id=city.id,
        )
        assert all(
            goal.target != "2"
            for goal in recruitment.goals(content, hero)
            if goal.kind == "location"
        )


def test_digest_never_sends_a_novice_to_a_closed_or_retired_location(content: GameContent) -> None:
    for city in content.cities:
        for level in range(city.level_min, city.level_max + 1):
            for rotation in range(8):
                for deed in digest.digest(content, "world-design-test", city.id, rotation, level):
                    if deed.slot:
                        location = city.location(deed.slot)
                        assert not location.merged_into and not location.pvp
                        assert location.level_min <= level
    assert digest.digest(content, "world-design-test", "last_beacon", 1, 1) == ()


def test_keeper_rename_does_not_reopen_a_retired_location(content: GameContent) -> None:
    for key in ("quiet_meadows", "dusk_grove"):
        record = OverlayRecord(
            kind=OverlayKind.LOCATION,
            entity_id=key,
            fields=overlay.snapshot(content, OverlayKind.LOCATION, key),
        ).with_field("name", "Новое название")
        edited = overlay.apply(content, (record,)).city("farhold")
        original = next(one for one in content.city("farhold").locations if one.id == key)
        actual = edited.location(original.slot)
        assert actual.habitats == original.habitats
        assert actual.merged_into == original.merged_into
        assert actual.experience_bonus_percent == original.experience_bonus_percent


def test_combined_habitats_keep_every_previous_resource(content: GameContent) -> None:
    for city in content.cities:
        old = city.location(2)
        for level in range(old.level_min, old.level_max + 1):
            previous = yields_here(content, level=level, biomes=old.biomes)
            current = yields_here(content, level=level, biomes=city.destination(2).biomes)
            assert set(previous) <= set(current)


def test_keeper_cannot_break_a_combined_entry_by_moving_or_removing_it(
    content: GameContent,
) -> None:
    for key in ("quiet_meadows", "dusk_grove"):
        record = OverlayRecord(
            kind=OverlayKind.LOCATION,
            entity_id=key,
            fields=overlay.snapshot(content, OverlayKind.LOCATION, key),
        )
        for changed in (record.with_field("slot", "6"), record.with_field("city", "dusk_harbor")):
            assert any("объединённая область" in one for one in overlay.problems(content, changed))
    removal = OverlayRecord(kind=OverlayKind.LOCATION, entity_id="quiet_meadows", removed=True)
    assert any("прежние места" in one for one in overlay.problems(content, removal))


def test_old_snapshot_without_merge_fields_keeps_its_original_defaults(
    content: GameContent,
) -> None:
    from mmorpg.application.battle_snapshots import decode, encode

    original = replace(content.city("farhold").location(2), merged_into=0)
    saved = encode(original)
    saved["fields"].pop("merged_into")
    saved["fields"].pop("habitats")
    assert decode(saved) == original
