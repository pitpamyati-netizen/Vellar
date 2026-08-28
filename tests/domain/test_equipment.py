"""Кости оружия, броня доспеха, характеристики от редкости и цена чужой вещи.

Здесь проверяется не формула, а обещание: доспех держит удар, оружие бьёт в
границах, редкость даёт числа, реликтовая вещь растёт вместе с героем, а чужое
можно надеть — просто дорого.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.procgen import items as gear_procgen
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import modifiers as mods
from mmorpg.domain.rules.combat import blow_range
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
    assert content.item("medium_body@14#common").armor > 0
    assert content.item("sword@14#common").armor == 0
    assert content.item("charm@14#common").armor == 0
    assert content.item("wolf_pelt").armor == 0


def test_heavier_armour_of_the_same_grade_holds_more(content: GameContent) -> None:
    cloth = content.item("cloth_body@45#common").armor
    light = content.item("light_body@45#common").armor
    heavy = content.item("heavy_body@45#common").armor
    assert heavy > light > cloth


def test_a_breastplate_holds_more_than_boots_of_the_same_kind(content: GameContent) -> None:
    """Место решает не меньше рода: четыре мелочи не одевают лучше нагрудника."""
    assert content.item("heavy_body@45#common").armor > content.item("heavy_feet@45#common").armor


def test_a_later_grade_holds_more_than_an_earlier_one(content: GameContent) -> None:
    grades = [content.item(f"heavy_body@{tier.level}#common").armor for tier in content.gear_tiers]
    assert grades == sorted(grades)
    assert grades[-1] > grades[0] * 10


def test_worn_armour_reaches_the_character_sheet(content: GameContent) -> None:
    bare = derived_stats(content, hero("warrior"))
    dressed = derived_stats(content, hero("warrior", body="medium_body@14#common"))
    assert dressed.armor > bare.armor


def test_armour_that_left_the_game_neither_gives_nor_breaks(content: GameContent) -> None:
    """Надетое переживает содержимое (``Claude.md``, правило 8)."""
    ghost = hero("warrior", body="no_such_item")
    assert gear.worn_armor(content, ghost.equipment.item_ids(), ghost.level) == 0
    assert derived_stats(content, ghost).armor >= 0


# --- оружие ----------------------------------------------------------


def test_a_weapon_beats_bare_hands(content: GameContent) -> None:
    bare = blow_range(content, hero("warrior"))
    armed = blow_range(content, hero("warrior", weapon="sword@14#common"))
    assert armed[1] > bare[1]


def test_damage_is_a_range_and_not_one_number(content: GameContent) -> None:
    """Урон бросается: «от 2 до 124» — это то, что случится."""
    sword = content.item("sword@26#common")
    assert sword.damage is not None
    assert sword.damage.low < sword.damage.high
    assert sword.damage.spoken() == f"от {sword.damage.low} до {sword.damage.high}"


def test_a_mace_swings_wider_than_a_sword(content: GameContent) -> None:
    """Размах — характер рода, и он держится на всех ступенях."""
    for tier in content.gear_tiers:
        mace = content.item(f"mace@{tier.level}#common").damage
        sword = content.item(f"sword@{tier.level}#common").damage
        assert mace is not None and sword is not None
        assert mace.high - mace.low > sword.high - sword.low, tier.level


def test_no_kind_of_weapon_is_a_choice_nobody_would_make(content: GameContent) -> None:
    """Между сильнейшим и слабейшим родом — меньше полутора раз."""
    averages = [kind.dice.average for kind in content.weapon_types]
    assert max(averages) / min(averages) <= 1.5


def test_bare_hands_are_worse_than_any_weapon(content: GameContent) -> None:
    fist = gear.weapon_dice(content, hero("warrior", level=1))
    for kind in content.weapon_types:
        assert kind.dice.average > fist.average, kind.id


def test_a_weapon_that_left_the_game_leaves_bare_hands(content: GameContent) -> None:
    assert gear.weapon_type_of(content, hero("warrior", weapon="no_such_item")) == ""


def test_a_kind_gives_what_the_kind_promises(content: GameContent) -> None:
    """Инициатива кинжала — свойство кинжалов вообще, а не этого клинка."""
    bundle = mods.equipment_modifiers(content, ("dagger@14#common",))
    assert (
        bundle["initiative_percent"]
        == content.weapon_type("dagger").modifiers["initiative_percent"]
    )


# --- стартовый / completion-комплект (fill_gear) -----------------------


def test_fill_gear_dresses_the_class_in_first_tier_common(content: GameContent) -> None:
    from mmorpg.domain.entities.character import Equipment

    first = content.gear_tiers[0].level
    filled = gear.fill_gear(
        content, "warrior", Equipment(), ("weapon", "head", "body", "hands", "feet")
    )
    for slot in ("weapon", "head", "body", "hands", "feet"):
        item_id = filled.item_in(slot)
        assert item_id is not None, slot
        item = content.item(item_id)
        assert item.level == first
        assert item.rarity == "common"
    # Доспех — самого крепкого рода, какой воин носит.
    heavy = content.character_class("warrior").armor_types[-1]
    assert content.item(filled.item_in("body")).armor_type == heavy


def test_fill_gear_leaves_worn_slots_alone(content: GameContent) -> None:
    from mmorpg.domain.entities.character import Equipment

    base = Equipment().equip("body", "heavy_body@14#rare")
    filled = gear.fill_gear(content, "warrior", base, ("weapon", "body", "head"))
    assert filled.item_in("body") == "heavy_body@14#rare"
    assert filled.item_in("head") is not None


def test_a_full_plate_set_costs_initiative_four_times(content: GameContent) -> None:
    worn = ("heavy_body@45#common", "heavy_hands@45#common", "heavy_feet@45#common")
    bundle = gear.type_modifiers(content, worn)
    single = content.armor_type("heavy").modifiers["initiative_percent"]
    assert bundle["initiative_percent"] == pytest.approx(single * len(worn))


# --- редкость --------------------------------------------------------


def test_rarity_is_what_gives_stats(content: GameContent) -> None:
    """Обычная вещь не даёт ни одной, необычная одну, остальные по две."""
    for rarity in content.rarities:
        item = content.item(f"sword@45#{rarity.id}")
        assert len(item.stat_bonuses) == rarity.stats, rarity.id
        assert all(value > 0 for value in item.stat_bonuses.values())


def test_only_the_two_top_rarities_carry_a_special_property(content: GameContent) -> None:
    for rarity in content.rarities:
        item = content.item(f"heavy_body@45#{rarity.id}")
        assert bool(item.modifiers) is rarity.special, rarity.id


#: Ключи, которые движок действительно читает. Список не выведен из содержимого
#: намеренно: половина словаря ``traits.toml`` не считается никем, и особое
#: свойство, попавшее в ту половину, было бы не прибавкой, а обещанием
#: (``Claude.md``, правило 7).
LIVE_KEYS = frozenset(
    {
        "damage_percent",
        "damage_taken_percent",
        "armor_percent",
        "health_percent",
        "accuracy_percent",
        "dodge_percent",
        "initiative_percent",
        "crit_chance_percent",
        "crit_damage_percent",
        "resource_percent",
        "resource_regen_percent",
        "cost_reduction_percent",
        "regen_per_turn_percent",
        "healing_done_percent",
    }
)


def test_a_special_property_is_a_key_the_engine_reads(content: GameContent) -> None:
    """Прибавка, которой никто не считает, — не свойство, а обещание."""
    for prop in content.special_properties:
        assert prop.key in LIVE_KEYS, prop.key
        assert prop.value != 0, prop.key


def test_every_live_key_is_read_by_something(content: GameContent) -> None:
    """Обратная сторона: список выше не должен обрасти мёртвыми ключами.

    Проверяется делом — вещь с этой прибавкой меняет то, что игра о герое
    говорит: характеристику, урон или броню.
    """
    plain = hero("warrior", level=50)
    base = derived_stats(content, plain)
    for key in sorted(LIVE_KEYS):
        bundle = mods.merge(mods.collect_modifiers(content, plain), {key: 25.0})
        assert bundle[key] == 25.0, key
    assert base.armor >= 0


def test_the_same_thing_is_the_same_thing_for_everyone(content: GameContent) -> None:
    """Собранная вещь выведена из своего же имени и не зависит ни от чего ещё."""
    once = content.item("ring@70#legendary")
    twice = gear_procgen.build(
        content,
        content.gear_archetype("ring"),
        70,
        content.rarity("legendary"),
    )
    assert once == twice


def test_a_relic_grows_with_the_hero_and_nothing_else_does(content: GameContent) -> None:
    relic = content.item("sword@70#relic")
    legendary = content.item("sword@70#legendary")

    assert gear_procgen.worn(content, legendary, 300) == legendary
    grown = gear_procgen.worn(content, relic, 300)
    assert grown.damage is not None and relic.damage is not None
    assert grown.damage.high > relic.damage.high
    assert min(grown.stat_bonuses.values()) > min(relic.stat_bonuses.values())


def test_a_relic_on_the_character_sheet_counts_by_the_hero(content: GameContent) -> None:
    young = derived_stats(content, hero("warrior", level=20, trinket="ring@70#relic"))
    old = derived_stats(content, hero("warrior", level=300, trinket="ring@70#relic"))
    assert old.max_health > young.max_health


def test_a_relic_never_falls_off_a_shelf(content: GameContent) -> None:
    """Реликтовое не выпадает случайно: его вес нулевой, и катать его нечем."""
    assert content.rarity("relic").weight == 0


# --- чужая вещь ------------------------------------------------------


def test_a_class_may_wear_anything_at_a_price(content: GameContent) -> None:
    """Запрета нет. Есть цена, и она в точности и инициативе."""
    rogue = hero("rogue", body="heavy_body@45#common")
    penalty = gear.proficiency_penalty(content, rogue)
    assert penalty["accuracy_percent"] < 0
    assert penalty["initiative_percent"] < 0

    bare = derived_stats(content, hero("rogue"))
    dressed = derived_stats(content, rogue)
    assert dressed.accuracy < bare.accuracy
    assert dressed.initiative < bare.initiative


def test_its_own_costs_nothing(content: GameContent) -> None:
    assert gear.proficiency_penalty(content, hero("rogue", body="light_body@45#common")) == {}
    assert gear.equip_warning(content, hero("rogue"), content.item("dagger@45#common")) == ""


def test_a_warning_says_what_and_how_much(content: GameContent) -> None:
    said = gear.equip_warning(content, hero("mage"), content.item("heavy_body@45#common"))
    assert content.item("heavy_body@45#common").name in said
    assert content.armor_type("heavy").name.lower() in said
    assert content.character_class("mage").name.lower() in said
    assert str(abs(int(gear.FOREIGN_ARMOR_ACCURACY))) in said


def test_a_warning_names_numbers_the_way_the_character_sheet_does(
    content: GameContent,
) -> None:
    """Инициативу зовут инициативой — так её называет экран характеристик.

    «Прыть» — это запас разбойника (``classes.toml``), и назвать ею инициативу
    значит сказать разбойнику, что у него тает ресурс. Ни одно имя ресурса класса
    не должно попасть в текст про штраф — ни сейчас, ни когда классов станет
    больше.
    """
    said = gear.equip_warning(content, hero("mage"), content.item("heavy_body@45#common")).lower()
    assert "инициатива" in said
    for klass in content.classes:
        assert klass.resource.name.lower() not in said, klass.resource.name


def test_a_trinket_belongs_to_everyone(content: GameContent) -> None:
    for klass in content.classes:
        assert gear.equip_warning(content, hero(klass.id), content.item("charm@45#rare")) == ""


def test_a_full_foreign_set_costs_more_than_one_piece(content: GameContent) -> None:
    one = gear.proficiency_penalty(content, hero("mage", body="heavy_body@45#common"))
    many = gear.proficiency_penalty(
        content,
        hero("mage", body="heavy_body@45#common", head="heavy_head@45#common"),
    )
    assert many["accuracy_percent"] < one["accuracy_percent"]


# --- умение и оружие -------------------------------------------------


def test_a_skill_asks_for_its_weapon(content: GameContent) -> None:
    """Здесь отказ, а не штраф: выстрелить без лука нечем."""
    shot = content.skill("ranger_tochnyy_vystrel")
    assert gear.skill_refusal(content, hero("ranger", weapon="bow@14#common"), shot) == ""
    assert "лук" in gear.skill_refusal(content, hero("ranger"), shot)
    with_knife = hero("ranger", weapon="dagger@14#common")
    assert content.weapon_type("dagger").name.lower() in gear.skill_refusal(
        content, with_knife, shot
    )


def test_a_skill_without_a_demand_works_with_anything(content: GameContent) -> None:
    cleave = content.skill("warrior_rassechenie")
    assert cleave.weapon_types == ()
    assert gear.skill_refusal(content, hero("warrior"), cleave) == ""
