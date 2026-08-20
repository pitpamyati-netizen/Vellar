"""Род оружия, род доспеха и броня, которая наконец что-то значит.

Здесь проверяется не формула, а обещание: доспех держит удар, кинжал бьёт иначе,
чем двуручник, класс носит своё, а умение без своего оружия не срабатывает.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules.combat import blow_of
from mmorpg.domain.rules.stats import derived_stats


def hero(class_id: str, level: int = 20, **worn: str) -> Character:
    character = Character(
        id=1, user_id=1, name="Проба", race_id="human", class_id=class_id, level=level
    )
    for slot, item_id in worn.items():
        character = replace(character, equipment=character.equipment.equip(slot, item_id))
    return character


# --- броня -----------------------------------------------------------


def test_armour_pieces_carry_a_number_of_their_own(content: GameContent) -> None:
    """Нагрудник даёт броню сам, а не процентом от выносливости."""
    assert gear.armor_of(content, content.item("chain_shirt")) > 0
    assert gear.armor_of(content, content.item("rusty_sword")) == 0
    assert gear.armor_of(content, content.item("copper_charm")) == 0
    assert gear.armor_of(content, content.item("wolf_pelt")) == 0


def test_heavier_armour_of_the_same_level_holds_more(content: GameContent) -> None:
    cloth = gear.armor_of(content, content.item("runed_robe"))
    plate = gear.armor_of(content, content.item("iron_cuirass"))
    assert plate > cloth


def test_a_breastplate_holds_more_than_boots_of_the_same_kind(content: GameContent) -> None:
    """Место решает не меньше рода: четыре мелочи не одевают лучше нагрудника."""
    body = gear.armor_of(content, content.item("iron_cuirass"))
    feet = gear.armor_of(content, content.item("plate_greaves"))
    assert body > feet


def test_worn_armour_reaches_the_character_sheet(content: GameContent) -> None:
    bare = derived_stats(content, hero("warrior"))
    dressed = derived_stats(content, hero("warrior", body="chain_shirt"))
    assert dressed.armor > bare.armor


def test_armour_that_left_the_game_neither_gives_nor_breaks(content: GameContent) -> None:
    """Надетое переживает содержимое (``Claude.md``, правило 8)."""
    ghost = hero("warrior", body="no_such_item")
    assert gear.worn_armor(content, ghost.equipment.item_ids()) == 0
    assert derived_stats(content, ghost).armor >= 0


# --- оружие ----------------------------------------------------------


def test_a_weapon_makes_the_standard_blow_heavier(content: GameContent) -> None:
    bare = blow_of(content, hero("warrior"))
    armed = blow_of(content, hero("warrior", weapon="rusty_sword"))
    assert armed > bare


def test_a_two_hander_hits_harder_than_a_dagger(content: GameContent) -> None:
    heavy = blow_of(content, hero("warrior", weapon="heavy_blade"))
    light = blow_of(content, hero("rogue", weapon="bone_dagger"))
    assert content.weapon_type("greatsword").damage > content.weapon_type("dagger").damage
    assert heavy > 0 and light > 0


def test_no_weapon_is_weaker_than_bare_hands(content: GameContent) -> None:
    """Кинжал, который хуже кулака, — не выбор, а ошибка."""
    for kind in content.weapon_types:
        assert kind.damage >= gear.UNARMED_DAMAGE, kind.id


def test_bare_hands_are_the_unit(content: GameContent) -> None:
    assert gear.blow_factor(content, hero("warrior")) == gear.UNARMED_DAMAGE
    # Не оружие в слоте оружия сюда тоже не попадает.
    assert gear.weapon_type_of(content, hero("warrior", weapon="no_such_item")) == ""


def test_a_kind_gives_what_the_kind_promises(content: GameContent) -> None:
    """Прыть кинжала — свойство кинжалов вообще, а не этого клинка."""
    bundle = mods.equipment_modifiers(content, ("bone_dagger",))
    assert (
        bundle["initiative_percent"]
        == content.weapon_type("dagger").modifiers["initiative_percent"]
    )


def test_a_full_plate_set_costs_initiative_four_times(content: GameContent) -> None:
    worn = ("iron_cuirass", "iron_gauntlets", "plate_greaves")
    bundle = gear.type_modifiers(content, worn)
    single = content.armor_type("heavy").modifiers["initiative_percent"]
    assert bundle["initiative_percent"] == pytest.approx(single * len(worn))


# --- допуски ---------------------------------------------------------


def test_a_class_wears_its_own(content: GameContent) -> None:
    rogue = hero("rogue")
    assert gear.equip_refusal(content, rogue, content.item("bone_dagger")) == ""
    assert gear.equip_refusal(content, rogue, content.item("heavy_blade"))
    assert gear.equip_refusal(content, rogue, content.item("runed_plate"))


def test_a_refusal_says_what_and_why(content: GameContent) -> None:
    said = gear.equip_refusal(content, hero("mage"), content.item("runed_plate"))
    assert "Рунная броня" in said
    assert content.armor_type("heavy").name.lower() in said
    assert content.character_class("mage").name.lower() in said


def test_a_trinket_belongs_to_everyone(content: GameContent) -> None:
    for klass in content.classes:
        assert gear.equip_refusal(content, hero(klass.id), content.item("copper_charm")) == ""


def test_a_skill_asks_for_its_weapon(content: GameContent) -> None:
    shot = content.skill("ranger_aimed_shot")
    assert gear.skill_refusal(content, hero("ranger", weapon="hunting_bow"), shot) == ""
    assert "лук" in gear.skill_refusal(content, hero("ranger"), shot)
    with_knife = hero("ranger", weapon="bone_dagger")
    assert content.weapon_type("dagger").name.lower() in gear.skill_refusal(
        content, with_knife, shot
    )


def test_a_skill_without_a_demand_works_with_anything(content: GameContent) -> None:
    cleave = content.skill("warrior_cleave")
    assert cleave.weapon_types == ()
    assert gear.skill_refusal(content, hero("warrior"), cleave) == ""
