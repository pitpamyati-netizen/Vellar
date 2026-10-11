"""Игрок видит цену ранга и подтверждает талант, а экипировка читается отдельно."""

import re
from dataclasses import replace

import pytest

from mmorpg.domain.entities.character import Character, Equipment, SkillLoadout
from mmorpg.domain.entities.dice import Dice
from mmorpg.domain.rules import talents
from mmorpg.domain.rules.combat import hero_combatant, skill_price
from mmorpg.presentation.telegram.flows.play import advance, begin, render
from mmorpg.presentation.telegram.flows.state import PlayState
from mmorpg.presentation.telegram.screens import skills as skill_screens
from mmorpg.presentation.telegram.screens import talents as talent_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.combat import skill_effect, skill_label
from mmorpg.presentation.telegram.screens.paginated import PageState
from mmorpg.presentation.telegram.screens.shop import OwnedItem, inventory_screen, owned_from_button

SEED = "progression-test"


def hero(level=150):
    return Character(
        1,
        101,
        "Проба",
        "human",
        "warrior",
        level=level,
        equipment=Equipment({"weapon": "sword@1#common!101"}),
        loadout=SkillLoadout(actives=("warrior_rassechenie", None, None, None, None, None)),
    )


def step(content, character, state, text):
    return advance(content, character, state, text, world_seed=SEED)


def test_equipment_is_only_inside_its_screen_and_back_works(content):
    character = hero()
    sheet = step(content, character, begin(character), "Персонаж")
    text = render(content, character, sheet, world_seed=SEED).text()
    assert content.item("sword@1#common").name not in text
    assert "Экземпляр" not in text and "Оружие:" not in text
    gear = step(content, character, sheet, "Экипировка")
    shown = render(content, character, gear, world_seed=SEED)
    assert shown.id is ScreenId.EQUIPMENT and content.item("sword@1#common").name in shown.text()
    assert "Экземпляр" not in shown.text() and "101" not in shown.text()
    assert step(content, character, gear, "Назад").screen is ScreenId.CHARACTER
    removed = step(content, character, gear, "Снять: Оружие")
    assert removed.pending.items == (("sword@1#common!101", 1),)


def test_talent_preview_confirmation_and_replacement(content):
    character = hero()
    screen = step(content, character, begin(character), "/таланты")
    assert screen.screen is ScreenId.TALENTS
    preview = step(content, character, screen, "/талант 1")
    assert preview.pending.empty and preview.screen is ScreenId.TALENT
    assert "основное действие" in render(content, character, preview, world_seed=SEED).text()
    chosen = step(content, character, preview, "/выбратьталант")
    selected = chosen.pending.character
    assert selected.loadout.talents == ("warrior_1_1",)
    assert chosen.screen is ScreenId.TALENTS
    preview = step(content, selected, chosen, "/талант 2")
    assert preview.pending.empty
    assert "Будет заменён" in render(content, selected, preview, world_seed=SEED).text()
    changed = step(content, selected, preview, "Принять талант")
    assert changed.pending.character.loadout.talents == ("warrior_1_2",)
    repeated = step(content, changed.pending.character, preview, "Принять талант")
    assert repeated.pending.empty
    assert changed.pending.character.unspent_skill_points == character.unspent_skill_points


def test_locked_missing_and_old_saved_talent_do_not_crash(content):
    character = hero(level=9)
    preview = step(content, character, begin(character), "/талант 1")
    assert "откроется на уровне 10" in render(content, character, preview, world_seed=SEED).text()
    assert step(content, character, preview, "/выбратьталант").pending.empty
    assert step(content, character, preview, "/талант 999").pending.empty
    stale = replace(preview, talent_code="gone")
    assert "больше нет" in render(content, character, stale, world_seed=SEED).text()
    restored = PlayState.deserialise(stale.serialise())
    assert restored.talent_code == "gone"


@pytest.mark.parametrize(
    "class_id", ["warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid"]
)
def test_all_talent_pages_and_choices_fit_and_are_distinct(content, class_id):
    character = replace(hero(), class_id=class_id)
    for page in range(1, 4):
        shown = talent_screens.talents_screen(content, character, PageState(page=page))
        assert len(shown.text()) <= 4096
        for row in shown.rows:
            for button in row:
                assert len(button.text) <= 128
    for one in talents.pool(content, character):
        shown = talent_screens.talent_screen(content, character, one.code)
        assert one.name in shown.text() and "Принять талант" in [
            b.text for r in shown.rows for b in r
        ]


def test_current_and_next_rank_use_same_price_and_effect_as_combat(content):
    character = talents.choose(content, hero(), "warrior_1_2")
    skill = content.skill("warrior_rassechenie")
    actor = hero_combatant(content, character, combatant_id=1, side=0)
    offer = skill_screens.rank_offer(content, character, skill)
    assert skill_effect(content, character, actor, skill) in offer
    assert f"расход {skill_price(content, character, skill, actor)}" in offer
    assert "Следующий ранг" in offer
    assert (
        f"стоит {skill_price(content, character, skill, actor)}"
        in skill_label(content, character, actor, 0).text
    )


def test_long_duplicate_choices_keep_their_marks_and_stale_choice_never_moves(content):
    item = max((one for one in content.items if one.is_equipment), key=lambda one: len(one.name))
    refs = (item.id + "!9223372036854775806", item.id + "!9223372036854775807")
    owned = tuple(OwnedItem(ref, 1) for ref in refs)
    shown = inventory_screen(content, owned, PageState(), gold=0)
    buttons = [b for r in shown.rows for b in r if "метка" in b.text]
    assert len(buttons) == 2 and buttons[0].text != buttons[1].text
    for button, ref in zip(buttons, refs, strict=True):
        assert not any(mark in button.text for mark in ("Экземпляр", "922337"))
        assert owned_from_button(content, button.text, owned).id == ref
    assert owned_from_button(content, buttons[0].text, owned[1:]) is None


def test_preview_applies_damage_talent_to_the_skills_own_dice_too(content):
    character = hero()
    stronger = talents.choose(content, character, "warrior_1_1")
    # Собственный урон велик, чтобы ошибку нельзя было спрятать округлением оружия.
    skill = replace(content.skill("warrior_rassechenie"), dice=Dice(1, 1, 10000))
    actor = hero_combatant(content, character, combatant_id=1, side=0)
    plain = re.search(r"от (\d+) до (\d+)", skill_effect(content, character, actor, skill))
    increased = re.search(r"от (\d+) до (\d+)", skill_effect(content, stronger, actor, skill))
    assert int(increased[1]) - int(plain[1]) >= 1000
    assert int(increased[2]) - int(plain[2]) >= 1000
