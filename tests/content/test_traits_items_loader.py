"""Traits, items and loader failure behaviour."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent, ItemKind
from mmorpg.domain.procgen.items import parse_gear_id
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


def test_items_never_describe_themselves(content: GameContent) -> None:
    """Описания у вещи нет, и не может появиться незаметно.

    Вещи выпадают, куются и лежат на прилавке сотнями: фраза у каждой была бы либо
    выдумкой на месте, либо одной и той же фразой на сотню предметов.
    """
    assert not hasattr(content.items[0], "text")


def test_every_weapon_and_armour_kind_exists_in_the_world(content: GameContent) -> None:
    """Род, которым никто не дерётся, — это допуск, закрывающий класс насмерть."""
    worn_weapons = {item.weapon_type for item in content.items if item.is_weapon}
    worn_armour = {item.armor_type for item in content.items if item.is_armor}
    for kind in content.weapon_types:
        assert kind.id in worn_weapons, kind.id
    for kind in content.armor_types:
        assert kind.id in worn_armour, kind.id


def test_every_class_can_arm_and_dress_itself_from_the_first_level(content: GameContent) -> None:
    """Класс, которому нечего надеть на первом уровне, играет голыми руками.

    Проверяется по всему списку вещей, а не по прилавку: прилавок случаен, но
    того, что классу вообще недоступно, он не покажет никогда.
    """
    starting = [item for item in content.items if item.level <= 5]
    for klass in content.classes:
        assert any(item.is_weapon and klass.can_wield(item.weapon_type) for item in starting), (
            f"{klass.id} has no weapon to start with"
        )
        assert any(
            item.is_armor and item.slot == "body" and klass.can_wear(item.armor_type)
            for item in starting
        ), f"{klass.id} has nothing to wear on its back"


def test_a_skill_never_asks_for_a_weapon_its_class_cannot_hold(content: GameContent) -> None:
    by_id = {klass.id: klass for klass in content.classes}
    for skill in content.skills:
        klass = by_id.get(skill.owner_id)
        if klass is None or not skill.weapon_types:
            continue
        assert set(skill.weapon_types) <= set(klass.weapon_types), skill.code


def test_rarity_is_what_gives_stats_and_only_the_top_two_scale(content: GameContent) -> None:
    """Договор редкостей, как он назван игроку: 0, 1, 2, 2 плюс свойство, и растущая."""
    by_id = {rarity.id: rarity for rarity in content.rarities}
    assert [rarity.stats for rarity in content.rarities] == [0, 1, 2, 2, 2]
    assert not by_id["common"].special
    assert by_id["legendary"].special
    assert by_id["relic"].special
    assert by_id["relic"].scaling
    assert sum(rarity.scaling for rarity in content.rarities) == 1


def test_only_a_relic_is_kept_off_the_shelves(content: GameContent) -> None:
    for rarity in content.rarities:
        assert (rarity.weight == 0) is rarity.scaling, rarity.id


def test_a_relic_is_paid_only_for_a_whole_chain(content: GameContent) -> None:
    """Второй путь к реликтовой вещи — цепочка заданий, пройденная до конца."""
    relics = {rarity.id for rarity in content.rarities if rarity.scaling}
    by_id = {quest.id: quest for quest in content.quests}
    paid = [
        quest
        for quest in content.quests
        if (parsed := parse_gear_id(quest.reward_item)) and parsed[2] in relics
    ]
    assert paid, "реликтовое должно быть достижимо не только с логова"
    for quest in paid:
        assert not any(other.follows == quest.id for other in content.quests), quest.id
        length, walked = 1, quest
        while walked.follows and walked.follows in by_id:
            walked = by_id[walked.follows]
            length += 1
        assert length >= 4, quest.id


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


def test_an_item_description_is_reported(tmp_path: Path) -> None:
    """Описание вернулось бы тихо: его отказывается принимать загрузчик."""
    directory = _copy_content(tmp_path)
    path = directory / "items.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'id = "small_healing_potion"',
            'id = "small_healing_potion"'
            + chr(10)
            + 'text = "Одна фраза, которой здесь не место."',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("has a text field" in problem for problem in error.value.problems)


def test_gear_written_by_hand_is_reported(tmp_path: Path) -> None:
    """Снаряжение собирается из видов: написанное руками молча не имело бы чисел."""
    directory = _copy_content(tmp_path)
    path = directory / "items.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + chr(10).join(
            (
                "",
                "[[item]]",
                'id = "hand_written_sword"',
                'name = "Написанный меч"',
                'kind = "equipment"',
                'slot = "weapon"',
                'rarity = "common"',
                "level = 1",
                "price = 30",
                "",
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("equipment written by hand" in problem for problem in error.value.problems)


def test_an_unknown_weapon_kind_is_reported(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    path = directory / "items.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'weapon_type = "sword"', 'weapon_type = "halberd"', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("halberd" in problem for problem in error.value.problems)


def test_a_weapon_without_a_kind_is_reported(tmp_path: Path) -> None:
    directory = _copy_content(tmp_path)
    path = directory / "items.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'id = "sword"'
            + chr(10)
            + 'noun = "меч"'
            + chr(10)
            + 'gender = "m"'
            + chr(10)
            + 'slot = "weapon"'
            + chr(10)
            + 'weapon_type = "sword"',
            'id = "sword"'
            + chr(10)
            + 'noun = "меч"'
            + chr(10)
            + 'gender = "m"'
            + chr(10)
            + 'slot = "weapon"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("unknown weapon_type" in problem for problem in error.value.problems)


def test_a_skill_asking_for_a_weapon_its_class_never_holds_is_reported(tmp_path: Path) -> None:
    """Кнопка, которая не сработает никогда, — это баг, а не содержимое."""
    directory = _copy_content(tmp_path)
    path = directory / "skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'weapons = ["dagger"]', 'weapons = ["greatsword"]', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as error:
        load_content(directory)
    assert any("never wields" in problem for problem in error.value.problems)


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


def test_every_material_says_what_kind_of_stock_it_is(content: GameContent) -> None:
    """Сырьё без вида падало бы из любого узла: травы из рудной жилы и наоборот."""
    from mmorpg.domain.entities.content import ItemKind
    from mmorpg.domain.rules.adventure import GATHER_SOURCES

    materials = [item for item in content.items if item.kind is ItemKind.MATERIAL]
    assert materials
    for item in materials:
        assert item.source, item.id

    known = set(GATHER_SOURCES.values())
    for wanted in known:
        assert any(item.source == wanted for item in materials), wanted
