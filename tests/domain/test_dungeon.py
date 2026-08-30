"""Данж: развилки только вперёд, сложность поднимает ставку, условия детерминированы.

Тесты свойств здесь и есть спецификация захода (``domain/rules/dungeon.py``,
ADR 0036).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from mmorpg.domain.entities import GameContent
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.procgen.enemies import generate_group
from mmorpg.domain.rules import dungeon


def test_difficulty_of_falls_back_to_recon() -> None:
    assert dungeon.difficulty_of("grim") is dungeon.Difficulty.GRIM
    assert dungeon.difficulty_of("nonsense") is dungeon.Difficulty.RECON
    assert dungeon.room_of("lair") is dungeon.RoomKind.LAIR
    assert dungeon.room_of("nonsense") is dungeon.RoomKind.SKIRMISH


def test_recon_is_the_only_difficulty_without_conditions() -> None:
    seed = dungeon.run_seed("world", "farhold", "d1", dungeon.Difficulty.RECON, 7)
    assert dungeon.conditions_for(seed, dungeon.Difficulty.RECON) == ()
    assert len(dungeon.conditions_for(seed, dungeon.Difficulty.DELVE)) == 1
    grim = dungeon.conditions_for(seed, dungeon.Difficulty.GRIM)
    assert len(grim) == 2
    # Два условия - это всегда одна беда и одно благо: заход не бывает ни
    # целиком гиблым, ни целиком щедрым.
    assert {one.good for one in grim} == {True, False}


def test_conditions_are_deterministic_by_seed() -> None:
    seed = dungeon.run_seed("world", "farhold", "d2", dungeon.Difficulty.GRIM, 99)
    first = dungeon.conditions_for(seed, dungeon.Difficulty.GRIM)
    second = dungeon.conditions_for(seed, dungeon.Difficulty.GRIM)
    assert [one.id for one in first] == [one.id for one in second]


@given(
    layer=st.integers(min_value=0, max_value=12),
    final=st.integers(min_value=2, max_value=12),
    started=st.integers(min_value=0, max_value=10_000),
)
def test_fork_options_are_forward_only_and_distinct(layer: int, final: int, started: int) -> None:
    seed = dungeon.run_seed("world", "farhold", "d1", dungeon.Difficulty.DELVE, started)
    options = dungeon.room_options(seed, layer, final)
    assert len(options) == len(set(options)), "две двери с одной надписью читались бы одной строкой"
    if layer <= 0:
        assert options == (dungeon.RoomKind.SKIRMISH,)
    elif layer >= final:
        assert options == (dungeon.RoomKind.LAIR, dungeon.RoomKind.STAIRS)
    else:
        assert 2 <= len(options) <= 3
        assert dungeon.RoomKind.LAIR not in options
        assert dungeon.RoomKind.STAIRS not in options


def test_fork_options_repeat_for_the_same_seed() -> None:
    seed = dungeon.run_seed("world", "farhold", "d1", dungeon.Difficulty.GRIM, 5)
    assert dungeon.room_options(seed, 2, 6) == dungeon.room_options(seed, 2, 6)


def test_final_layer_grows_with_base_depth_and_difficulty() -> None:
    assert dungeon.final_layer(dungeon.DESCENT_DEPTH, dungeon.Difficulty.RECON) == 3
    assert dungeon.final_layer(3, dungeon.Difficulty.RECON) == 3
    assert dungeon.final_layer(3, dungeon.Difficulty.DELVE) == 4
    assert dungeon.final_layer(5, dungeon.Difficulty.GRIM) == 7
    assert dungeon.final_layer(0, dungeon.Difficulty.RECON) == dungeon.MIN_FINAL_LAYER


def test_room_rank_maps_to_enemy_rank() -> None:
    assert dungeon.RoomKind.SKIRMISH.rank is EnemyRank.NORMAL
    assert dungeon.RoomKind.HOLLOW.rank is EnemyRank.NORMAL
    assert dungeon.RoomKind.BEAST.rank is EnemyRank.ELITE
    assert dungeon.RoomKind.LAIR.rank is EnemyRank.BOSS


def test_difficulty_carries_an_affix_budget() -> None:
    """Разведка - без прозвищ, гиблый спуск - по два (ADR 0042)."""
    assert dungeon.spec_of(dungeon.Difficulty.RECON).affix_chance == 0.0
    assert dungeon.spec_of(dungeon.Difficulty.RECON).affix_count == 0
    assert dungeon.spec_of(dungeon.Difficulty.DELVE).affix_count == 1
    assert dungeon.spec_of(dungeon.Difficulty.GRIM).affix_count == 2
    assert (
        dungeon.spec_of(dungeon.Difficulty.GRIM).affix_chance
        > dungeon.spec_of(dungeon.Difficulty.DELVE).affix_chance
    )


def test_affix_odds_only_touch_elites_and_bosses() -> None:
    assert dungeon.affix_odds(EnemyRank.NORMAL) == dungeon.AffixOdds(0.0, 0)
    assert dungeon.affix_odds(EnemyRank.ELITE).chance > 0.0
    assert dungeon.affix_odds(EnemyRank.BOSS).chance > dungeon.affix_odds(EnemyRank.ELITE).chance


def test_bounty_multiplies_across_conditions() -> None:
    rich = next(one for one in dungeon.CONDITIONS if one.id == "rich_seam")
    assert dungeon.bounty_of(()) == 1.0
    assert dungeon.bounty_of((rich,)) == rich.bounty
    assert dungeon.bounty_of((rich, rich)) == rich.bounty * rich.bounty


def test_stakes_makes_the_dungeon_enemy_tougher_and_richer(content: GameContent) -> None:
    archetypes = content.enemy_archetypes
    seed = b"same-room"
    plain = generate_group(seed, archetypes=archetypes, biome="луга", level=20)
    steep = generate_group(
        seed, archetypes=archetypes, biome="луга", level=20, stakes=2.0, bounty=1.0
    )
    assert sum(one.max_health for one in steep) > sum(one.max_health for one in plain)
    assert sum(one.damage for one in steep) > sum(one.damage for one in plain)
    assert sum(one.gold for one in steep) > sum(one.gold for one in plain)
    assert all(one.stakes == 2.0 for one in steep)


def test_bounty_lifts_gold_without_touching_the_fight(content: GameContent) -> None:
    archetypes = content.enemy_archetypes
    seed = b"same-room"
    plain = generate_group(seed, archetypes=archetypes, biome="луга", level=20)
    richer = generate_group(
        seed, archetypes=archetypes, biome="луга", level=20, stakes=1.0, bounty=1.5
    )
    assert sum(one.gold for one in richer) > sum(one.gold for one in plain)
    assert [one.max_health for one in richer] == [one.max_health for one in plain]
    assert [one.damage for one in richer] == [one.damage for one in plain]
