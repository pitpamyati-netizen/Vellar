"""Личная добыча, редкость, старые вещи и повторяемая награда задания."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.procgen import items
from mmorpg.domain.rules import party, quests, salvage
from mmorpg.presentation.telegram.screens import quests as quest_screens


def _hero(class_id: str) -> Character:
    return Character(id=17, user_id=31, name="Проверка", race_id="human", class_id=class_id)


def test_new_stamp_round_trip_and_bad_stamps(content: GameContent) -> None:
    roll = items.new_roll(
        content, random.Random(17), content.gear_archetype("staff"), class_id="mage"
    )
    item_id = items.gear_id("staff", 76, "rare", roll)
    assert "~v2-" in item_id
    assert items.parse_gear_id(item_id) == ("staff", 76, "rare", roll)
    assert content.has_item(item_id + "!123")
    assert not content.has_item("staff@76#rare~v2-0-3")
    for bad in ("v2-x-4", "v2-5--4", "v2-5-1000000", "v2-65536-4", "9000"):
        assert not content.has_item(f"staff@76#rare~{bad}")


def test_class_profiles_do_not_collide(content: GameContent) -> None:
    stamps = {
        items.new_roll(content, random.Random(1), content.gear_archetype("ring"), class_id=klass.id)
        for klass in content.classes
    }
    assert len(stamps) == len(content.classes)


@pytest.mark.parametrize("level", [1, 46, 140])
def test_rarity_has_exact_prefix_and_suffix_budget(content: GameContent, level: int) -> None:
    for klass in content.classes:
        for rarity in content.rarities:
            for seed in range(12):
                roll = items.new_roll(
                    content, random.Random(seed), content.gear_archetype("ring"), class_id=klass.id
                )
                made = content.item(items.gear_id("ring", level, rarity.id, roll))
                assert len(made.prefixes) == rarity.prefixes
                assert len(made.suffixes) == rarity.suffixes + int(rarity.special)
                assert len(made.stat_bonuses) + len(made.modifiers) == rarity.affixes + int(
                    rarity.special
                )
                if rarity.affixes:
                    assert klass.key_stats[0].value in made.stat_bonuses
                    first = content.loot_rules.prefix_words[klass.key_stats[0].value]["n"]
                    assert made.name.startswith(first)
                else:
                    assert not made.prefixes and not made.suffixes


@pytest.mark.parametrize("level", [1, 76, 150])
def test_all_classes_receive_useful_items_in_every_slot(content: GameContent, level: int) -> None:
    for klass in content.classes:
        source = random.Random(20261015)
        seen: Counter[str] = Counter()
        own = 0
        for _ in range(600):
            found = items.roll_drop(
                content, source, level=level, rank=EnemyRank.BOSS, class_ids=(klass.id,)
            )
            assert found is not None
            made = content.item(found)
            kind = content.gear_archetype(items.parse_gear_id(found)[0])  # type: ignore[index]
            if not items.suits(content, kind, (klass.id,)):
                continue
            own += 1
            seen[made.slot] += 1
            if made.prefixes:
                assert klass.key_stats[0].value in made.stat_bonuses
            if made.class_affix_id:
                assert (
                    next(
                        one for one in content.class_affixes if one.id == made.class_affix_id
                    ).class_id
                    == klass.id
                )
        expected = {
            one.slot for one in content.gear_archetypes if items.suits(content, one, (klass.id,))
        }
        assert set(seen) == expected
        assert own / 600 == pytest.approx(content.loot_rules.class_share, abs=0.06)
        for count in seen.values():
            assert count / own == pytest.approx(1 / len(expected), abs=0.07)


def test_mage_items_do_not_roll_physical_damage_or_foreign_named_affixes(
    content: GameContent,
) -> None:
    for kind in ("staff", "cloth_body", "ring"):
        for seed in range(60):
            roll = items.new_roll(
                content, random.Random(seed), content.gear_archetype(kind), class_id="mage"
            )
            made = content.item(items.gear_id(kind, 76, "legendary", roll))
            assert "INT" in made.stat_bonuses
            assert "physical_damage_percent" not in made.modifiers
            if made.class_affix_id:
                affix = next(one for one in content.class_affixes if one.id == made.class_affix_id)
                assert affix.class_id == "mage"


def test_reforging_preserves_the_profile_and_physical_number(content: GameContent) -> None:
    roll = items.new_roll(
        content, random.Random(1), content.gear_archetype("ring"), class_id="mage"
    )
    before = items.gear_id("ring", 76, "rare", roll) + "!123"
    after = salvage.reforged(content, before, source=random.Random(4))
    assert after.endswith("!123") and before != after
    assert "INT" in content.item(after).stat_bonuses


def test_party_assigns_mage_gear_to_mage_and_conserves_every_item(content: GameContent) -> None:
    mage = _hero("mage")
    warrior = replace(_hero("warrior"), id=18)
    roster = {mage.id: mage, warrior.id: warrior}
    ring = items.reward_gear_id(content, "ring@76#legendary", class_id="mage", seed="party")
    staff = items.reward_gear_id(content, "staff@76#rare", class_id="mage", seed="party-staff")
    loot = (ring, "wolf_pelt", staff, "sword@76#rare", "cloth_body@76#rare", "wolf_pelt")
    for seed in range(12):
        shares = party.distribute(
            loot, (mage.id, warrior.id), random.Random(seed), content=content, roster=roster
        )
        assert ring in shares[mage.id] and staff in shares[mage.id]
        assert "cloth_body@76#rare" in shares[mage.id]
        assert "sword@76#rare" in shares[warrior.id]
        assert Counter(one for share in shares.values() for one in share) == Counter(loot)
        assert shares == party.distribute(
            loot, (mage.id, warrior.id), random.Random(seed), content=content, roster=roster
        )


def test_party_foreign_or_unknown_gear_still_has_one_owner(content: GameContent) -> None:
    mage = _hero("mage")
    loot = ("missing@76#rare", "heavy_body@76#rare", "sword@76#rare")
    shares = party.distribute(
        loot, (mage.id,), random.Random(1), content=content, roster={mage.id: mage}
    )
    assert shares == {mage.id: loot}
    assert party.distribute(loot, (), random.Random(1), content=content, roster={}) == {}


def test_old_relic_and_new_relic_keep_numbers_and_identity_on_every_hero(
    content: GameContent,
) -> None:
    for reference in ("staff@76#relic~4!123", "ring@1#relic!124"):
        made = content.item(reference)
        for level in (1, 76, 150):
            assert items.worn(content, made, level) is made
            assert made.id == reference


def test_legacy_relic_durability_does_not_shrink_around_saved_wear(content: GameContent) -> None:
    old = content.item("staff@1#relic!123")
    former_maximum = round(
        (items.DURABILITY_BASE + items.DURABILITY_PER_LEVEL * content.rules.max_character_level)
        * content.rarity("relic").toughness
    )
    assert old.durability == former_maximum
    fresh_id = items.reward_gear_id(content, "staff@1#relic", class_id="mage", seed="new")
    fresh = content.item(fresh_id)
    assert fresh.durability < old.durability
    assert fresh.level == old.level == 1


def test_quest_preview_and_payment_match_and_completed_quest_cannot_pay_again(
    content: GameContent,
) -> None:
    quest = replace(content.quests[0], reward_item="sword@76#relic")
    mage = _hero("mage")
    ready = replace(mage, quests=QuestLog(taken={quest.id: quest.target_count}))
    promised = quests.reward_item_id(content, ready, quest)
    paid = quests.hand_in(content, ready, quest)
    assert paid is not None
    assert paid.item_id == promised
    made = content.item(promised)
    assert (
        made.level == 76
        and made.rarity == "relic"
        and made.weapon_type in content.character_class("mage").weapon_types
    )
    assert "INT" in made.stat_bonuses
    assert made.name in quest_screens.reward_line(content, quest, mage)
    assert quests.hand_in(content, paid.character, quest) is None
    assert quests.reward_item_id(content, mage, quest) == promised
    assert content.has_item("sword@76#relic")


@pytest.mark.parametrize(
    "reference", ["healing_potion", "wolf_pelt", "", "sword@not#rare", "sword@77#rare"]
)
def test_non_gear_and_invalid_rewards_are_not_silently_changed(
    content: GameContent, reference: str
) -> None:
    assert items.reward_gear_id(content, reference, class_id="mage", seed="quest") == reference


def test_extreme_penalties_and_no_drop_do_not_break_generation(content: GameContent) -> None:
    assert (
        items.roll_drop(content, random.Random(1), level=76, rank=EnemyRank.BOSS, drop_bonus=-100)
        is None
    )
    assert (
        items.roll_drop(
            content, random.Random(1), level=76, rank=EnemyRank.BOSS, rarity_bonus=-10000
        )
        is not None
    )
