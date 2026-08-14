"""Traits, items and loader failure behaviour."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent, ItemKind
from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT

MINIMUM_TRAITS = 60


def test_at_least_sixty_traits(content: GameContent) -> None:
    assert len(content.traits) >= MINIMUM_TRAITS


def test_traits_cover_every_category(content: GameContent) -> None:
    for category in content.trait_categories:
        assert content.traits_in_category(category), category


def test_traits_never_grant_skills(content: GameContent) -> None:
    """The anti-bloat rule, enforced structurally: a Trait has modifiers only."""
    trait_fields = (
        set(vars(content.traits[0]).keys()) if hasattr(content.traits[0], "__dict__") else set()
    )
    forbidden = {"skill", "skills", "active", "grants"}
    assert not (trait_fields & forbidden)
    for trait in content.traits:
        assert trait.modifiers, trait.id
        for value in trait.modifiers.values():
            assert isinstance(value, float)


def test_dark_traits_have_a_real_penalty(content: GameContent) -> None:
    """A dark trait must pay for its upside; "lower is better" keys count inverted."""
    for trait in content.traits_in_category("dark"):
        verdicts = [content.is_bonus(key, value) for key, value in trait.modifiers.items()]
        assert any(verdicts), f"{trait.id} has no upside"
        assert not all(verdicts), f"{trait.id} has no penalty"


def test_inverted_modifiers_are_read_correctly(content: GameContent) -> None:
    assert content.is_bonus("shop_price_percent", -12) is True
    assert content.is_bonus("shop_price_percent", 12) is False
    assert content.is_bonus("damage_percent", 10) is True
    assert content.is_bonus("damage_taken_percent", 10) is False


def test_trait_ids_and_names_are_unique(content: GameContent) -> None:
    assert len({trait.id for trait in content.traits}) == len(content.traits)
    assert len({trait.name for trait in content.traits}) == len(content.traits)


def test_equipment_never_grants_active_skills(content: GameContent) -> None:
    """Equipment only modifies skills the character already has."""
    for item in content.items:
        for skill_code in item.skill_modifiers:
            assert content.has_skill(skill_code), f"{item.id} -> {skill_code}"


def test_consumables_have_effects_and_stack(content: GameContent) -> None:
    consumables = [item for item in content.items if item.kind is ItemKind.CONSUMABLE]
    assert consumables
    for item in consumables:
        assert item.effect is not None, item.id
        assert item.stack > 1, item.id
        assert item.slot == "none", item.id


def test_rarities_are_ordered_by_scarcity(content: GameContent) -> None:
    weights = [rarity.weight for rarity in content.rarities]
    assert weights == sorted(weights, reverse=True)
    factors = [rarity.price_factor for rarity in content.rarities]
    assert factors == sorted(factors)


def test_lookups_are_indexed(content: GameContent) -> None:
    assert content.race("human").name
    assert content.character_class("mage").resource.name == "Чары"
    assert content.item("healing_potion").kind is ItemKind.CONSUMABLE
    assert content.city("farhold").location(1).slot == 1
    with pytest.raises(KeyError):
        content.race("no_such_race")


# --- loader failure modes -------------------------------------------


def _copy_content(tmp_path: Path) -> Path:
    target = tmp_path / "content"
    shutil.copytree(CONTENT_ROOT, target)
    return target


def test_missing_file_is_reported(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    (directory / "races.toml").unlink()
    with pytest.raises(ContentError, match=r"missing content file: races\.toml"):
        load_content(directory)


def test_unknown_modifier_is_reported(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    path = directory / "traits.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "modifiers = { damage_percent = 10, armor_percent = -10 }",
            "modifiers = { damage_percent = 10, nonsense_percent = -10 }",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("nonsense_percent" in problem for problem in error.value.problems)


def test_world_gap_is_reported(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    path = directory / "world.toml"
    text = path.read_text(encoding="utf-8")
    # Break the very first location so levels 2-4 stop being covered.
    text = text.replace(
        'name = "Луга у Заставы"\nbiome = "луга"\nlevel_min = 1\nlevel_max = 4',
        'name = "Луга у Заставы"\nbiome = "луга"\nlevel_min = 1\nlevel_max = 1',
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("levels without any location" in problem for problem in error.value.problems)


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    path = directory / "races.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("bonuses = { STR = 1, INT = 1, CHA = 1 }", "bonuses = { STR = 9 }")
    text = text.replace('active = "race_high_elf_mana_surge"', 'active = "no_such_skill"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContentError) as error:
        load_content(directory)
    problems = error.value.problems
    assert any("budget" in problem for problem in problems)
    assert any("no_such_skill" in problem for problem in problems)
