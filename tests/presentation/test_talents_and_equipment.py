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


def test_talent_preview_confirmation_ranks_and_reset(content):
    character = hero()
    screen = step(content, character, begin(character), "/таланты")
    assert screen.screen is ScreenId.TALENTS
    preview = step(content, character, screen, "/талант 1")
    assert preview.pending.empty and preview.screen is ScreenId.TALENT
    assert "основное действие" in render(content, character, preview, world_seed=SEED).text()
    chosen = step(content, character, preview, "/выбратьталант")
    selected = chosen.pending.character
    assert selected.loadout.talent_ranks == {"warrior_path_1_1_a": 1}
    assert chosen.screen is ScreenId.TALENTS
    assert step(content, selected, preview, "/выбратьталант").pending.empty
    preview = step(content, selected, chosen, "/талант 1")
    assert preview.pending.empty
    shown = render(content, selected, preview, world_seed=SEED).text()
    assert "Текущий ранг: 1 из 2" in shown and "на 4 процентов" in shown
    changed = step(content, selected, preview, "Принять талант")
    assert changed.pending.character.loadout.talent_ranks == {"warrior_path_1_1_a": 2}
    repeated = step(content, changed.pending.character, preview, "Принять талант")
    assert repeated.pending.empty
    assert changed.pending.character.unspent_skill_points == character.unspent_skill_points
    character = changed.pending.character
    reset = step(content, character, changed, "/сброситьталанты")
    assert reset.pending.empty and reset.screen is ScreenId.TALENT_RESET
    assert step(content, character, reset, "Назад").screen is ScreenId.TALENTS
    cleared = step(content, character, reset, "/подтвердитьсбросталантов")
    assert cleared.pending.character.loadout.talent_ranks == {}
    assert cleared.screen is ScreenId.TALENTS


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
    assert restored.talent_rank == preview.talent_rank
    assert step(content, character, preview, "/веткаталантов 999").pending.empty


@pytest.mark.parametrize(
    "class_id", ["warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid"]
)
def test_all_talent_pages_and_choices_fit_and_are_distinct(content, class_id):
    character = replace(hero(), class_id=class_id)
    root = talent_screens.talents_screen(content, character, PageState())
    assert len(root.text()) <= 4096
    for branch, _ in talent_screens.branches(content, character):
        seen = set()
        for page in range(1, 16):
            shown = talent_screens.branch_screen(content, character, branch, PageState(page=page))
            assert len(shown.text()) <= 2000
            for row in shown.rows:
                for button in row:
                    assert len(button.text) <= 128
                    if re.match(r"\d+\. ", button.text):
                        seen.add(button.text)
        assert len(seen) == 15
    for one in talents.pool(content, character):
        shown = talent_screens.talent_screen(content, character, one.code)
        assert one.name in shown.text()
        assert ("Принять талант" in [b.text for r in shown.rows for b in r]) == (
            not talents.blocked(content, character, one)
        )
        assert "Путь:" in shown.text() or one.row == 1


def test_current_and_next_rank_use_same_price_and_effect_as_combat(content):
    character = replace(hero(), loadout=replace(hero().loadout, talents=("warrior_1_2",)))
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
    stronger = replace(character, loadout=replace(character.loadout, talents=("warrior_1_1",)))
    # Собственный урон велик, чтобы ошибку нельзя было спрятать округлением оружия.
    skill = replace(content.skill("warrior_rassechenie"), dice=Dice(1, 1, 10000))
    actor = hero_combatant(content, character, combatant_id=1, side=0)
    plain = re.search(r"от (\d+) до (\d+)", skill_effect(content, character, actor, skill))
    increased = re.search(r"от (\d+) до (\d+)", skill_effect(content, stronger, actor, skill))
    assert int(increased[1]) - int(plain[1]) >= 1000
    assert int(increased[2]) - int(plain[2]) >= 1000


def test_branch_button_back_text_command_and_reset_confirmation_match(content):
    character = hero()
    root = step(content, character, begin(character), "/таланты")
    shown = render(content, character, root, world_seed=SEED)
    branch = step(content, character, root, shown.rows[0][0].text)
    assert branch.screen is ScreenId.TALENT_BRANCH
    assert branch.talent_branch == "warrior_branch_1"
    assert step(content, character, root, "/веткаталантов 1").talent_branch == branch.talent_branch
    shown = render(content, character, branch, world_seed=SEED)
    preview = step(content, character, branch, shown.rows[0][0].text)
    assert preview.screen is ScreenId.TALENT
    chosen = step(content, character, preview, "/выбратьталант")
    assert chosen.screen is ScreenId.TALENT_BRANCH
    assert step(content, character, branch, "Назад").screen is ScreenId.TALENTS
    reset = step(content, character, root, "/сброситьталанты")
    newer = chosen.pending.character
    assert step(content, newer, reset, "/подтвердитьсбросталантов").pending.empty
    restored = PlayState.deserialise(reset.serialise())
    assert restored.talent_confirmation == reset.talent_confirmation


def test_old_build_transition_needs_confirmation_and_can_be_cancelled(content):
    character = replace(hero(), loadout=replace(hero().loadout, talents=("warrior_1_1",)))
    root = step(content, character, begin(character), "/таланты")
    text = render(content, character, root, world_seed=SEED).text()
    assert "Прежний набор продолжает действовать" in text and "Верный замах" in text
    preview = step(content, character, root, "/талант 1")
    assert step(content, character, preview, "/выбратьталант").pending.empty
    reset = step(content, character, root, "/сброситьталанты")
    assert (
        reset.pending.empty
        and "Прежние выбранные таланты перестанут"
        in render(content, character, reset, world_seed=SEED).text()
    )
    assert step(content, character, reset, "Назад").pending.empty
    new = step(content, character, reset, "/подтвердитьсбросталантов")
    assert new.pending.character.loadout.talents == ()
    preview = step(content, new.pending.character, new, "/талант 1")
    assert (
        step(content, new.pending.character, preview, "/выбратьталант").pending.character
        is not None
    )
