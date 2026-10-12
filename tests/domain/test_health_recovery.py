"""Отдых, дробные очки, выносливость и совместимость старого героя."""

import json
from dataclasses import replace

import pytest

from mmorpg.application.battle_snapshots import content_snapshot, decode, encode, restore_content
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import ClassResource
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules.health_recovery import recover, restart
from mmorpg.domain.rules.stats import derived_stats, primary_stats


@pytest.fixture
def wounded():
    return Character(1, 42, "Аргус", "human", "warrior", health=1, health_recovered_at=1000)


def test_offline_minute_and_same_time_do_not_heal_twice(content, wounded):
    healed = recover(content, wounded, now=1060)
    assert healed.health == 1 + int(derived_stats(content, wounded).health_recovery_per_minute)
    assert recover(content, healed, now=1060) == healed


def test_second_by_second_equals_one_offline_interval(content, wounded):
    frequent = wounded
    for now in range(1001, 1601):
        frequent = recover(content, frequent, now=now)
    assert frequent == recover(content, wounded, now=1600)


def test_fraction_survives_a_snapshot_restart(content, wounded):
    fraction = recover(content, wounded, now=1001)
    assert fraction.health_recovery_remainder > 0
    restored = decode(encode(fraction))
    assert recover(content, restored, now=1060) == recover(content, wounded, now=1060)


def test_long_absence_caps_health_and_cannot_bank_healing(content, wounded):
    full = recover(content, wounded, now=10**12)
    assert full.health == derived_stats(content, wounded).max_health
    assert full.health_recovery_remainder == 0
    full = recover(content, full, now=10**12 + 3600)
    assert full.health_recovered_at is None
    damaged = replace(full, health=1)
    assert recover(content, damaged, now=10**12 + 3601).health == 1


def test_old_wounds_start_now_without_inventing_old_elapsed_time(content, wounded):
    old = replace(wounded, health_recovered_at=None)
    initialised = recover(content, old, now=1000)
    assert initialised.health == old.health
    assert initialised.health_recovered_at == 1000
    assert recover(content, initialised, now=1060).health > old.health


def test_old_snapshot_omits_new_fields_safely(wounded):
    old = encode(wounded)
    old["fields"].pop("health_recovered_at")
    old["fields"].pop("health_recovery_remainder")
    assert decode(old).health_recovered_at is None


def test_old_battle_catalog_has_new_resource_labels_and_default_recovery(content):
    _, raw = content_snapshot(content)
    old = json.loads(raw)
    for klass in old["data"]["classes"]["sequence"]:
        if klass["fields"]["id"] == "paladin":
            klass["fields"]["resource"]["fields"].update(id="grace", name="Благодать")
    rules = old["data"]["rules"]["fields"]
    for name in list(rules):
        if name.startswith("health_recovery_"):
            rules.pop(name)
    restored = restore_content(json.dumps(old))
    assert restored.character_class("paladin").resource.name == "Выносливость"
    assert restored.rules.health_recovery_base_percent == 1


@pytest.mark.parametrize("now", [900, 1000])
def test_clock_backwards_or_no_elapsed_time_changes_nothing(content, wounded, now):
    assert recover(content, wounded, now=now) == wounded


def test_full_new_character_does_not_change_revision_or_health_sentinel(content, wounded):
    full = replace(wounded, health=0, health_recovered_at=None)
    assert recover(content, full, now=1200) is full


def test_battle_exit_discards_waiting_and_fraction(content, wounded):
    waiting = replace(wounded, health_recovery_remainder=59_000_000)
    left = restart(waiting, now=10000)
    assert left.health == wounded.health
    assert left.health_recovery_remainder == 0
    assert recover(content, left, now=10000) == left
    assert recover(content, left, now=10060).health > left.health


@pytest.mark.parametrize(
    "klass", ["warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid"]
)
def test_every_class_recovers_faster_with_effective_endurance(content, wounded, klass):
    hero = replace(wounded, class_id=klass)
    tougher = replace(hero, allocated=StatBlock(END=100))
    for character in (hero, tougher):
        stats = derived_stats(content, character)
        end = primary_stats(content, character)[StatCode.END]
        expected = stats.max_health * (1 + 4 * end / (end + 100)) / 100
        assert stats.health_recovery_per_minute == pytest.approx(expected, abs=0.000001)
    assert (
        derived_stats(content, tougher).health_recovery_per_minute
        > derived_stats(content, hero).health_recovery_per_minute
    )


def test_zero_recovery_rule_is_supported(content, wounded):
    rules = replace(
        content.rules, health_recovery_base_percent=0, health_recovery_endurance_percent=0
    )
    unchanged = recover(replace(content, rules=rules), wounded, now=2000)
    assert unchanged.health == wounded.health


@pytest.mark.parametrize(
    "old_id,old_name,kind,name",
    [
        ("valor", "Отвага", "stamina", "Выносливость"),
        ("rage", "Ярость", "stamina", "Выносливость"),
        ("grace", "Благодать", "stamina", "Выносливость"),
        ("focus", "Фокус", "stamina", "Выносливость"),
        ("energy", "Прыть", "stamina", "Выносливость"),
        ("mana", "Чары", "mana", "Мана"),
        ("faith", "Вера", "mana", "Мана"),
        ("communion", "Единение", "mana", "Мана"),
    ],
)
def test_old_class_resources_keep_numbers_and_normalise_labels(old_id, old_name, kind, name):
    old = ClassResource(old_id, old_name, 45, 2.4, 5)
    assert (old.id, old.name) == (kind, name)
    assert (old.base, old.per_level, old.regen_per_turn) == (45, 2.4, 5)
