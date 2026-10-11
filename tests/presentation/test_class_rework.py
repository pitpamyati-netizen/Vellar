"""Новые цели доступны кнопками и командами и не требуют быстрого ввода."""

from dataclasses import replace

from mmorpg.application.services.battle import BattleSession
from mmorpg.domain.entities.combat import ActionKind
from mmorpg.domain.entities.content import OwnerKind
from mmorpg.presentation.telegram.flows.combat import action_for
from mmorpg.presentation.telegram.screens.combat import (
    ally_target_label,
    battle_screen,
    skill_effect,
)
from mmorpg.presentation.telegram.screens.creation import class_details_screen
from mmorpg.presentation.telegram.screens.skills import skill_detail
from tests.domain.test_class_rework import group


def test_ally_button_and_commands_resolve_to_same_action(content):
    state, roster = group(content)
    session = BattleSession(id="roles", state=state, seed=b"roles")
    hero = roster[1]
    chosen = action_for(content, hero, session, 1, "/союзник 2")
    button = action_for(content, hero, session, 1, ally_target_label(state.by_id(2)).text)
    assert chosen == button
    assert chosen.kind is ActionKind.AID_FOCUS
    assert chosen.target == 2
    direct = action_for(content, hero, session, 1, "/умение 1 2")
    assert direct.kind is ActionKind.SKILL and direct.slot == 0 and direct.target == 2
    assert action_for(content, hero, session, 1, "/умение 7 2") is None


def test_screen_names_selected_support_and_keeps_skill_numbers(content):
    state, roster = group(content)
    screen = battle_screen(content, roster[1], state, 1)
    assert "Помощь: 1. Лада." in screen.text()
    assert "/союзник N" in screen.text()
    assert "Не дать угаснуть" in screen.text()
    assert any(row[0].text.startswith("1. Слово исцеления") for row in screen.rows)
    assert any(row[0] == ally_target_label(state.by_id(2)) for row in screen.rows)
    assert "процентов максимального здоровья союзника" in skill_effect(
        content, roster[1], state.by_id(1), content.skill("cleric_slovo_istseleniya")
    )


def test_class_and_skill_describe_the_same_rules(content):
    _, roster = group(content)
    text = class_details_screen(content, "cleric").text()
    detail = skill_detail(content, content.skill("cleric_slovo_istseleniya"), roster[1])
    assert "25 процентов" in text and "40 процентов" in text
    assert "25 процентов" in detail and "40 процентов" in detail
    assert "выбранный союзник" in detail
    racial = replace(content.skill("cleric_slovo_istseleniya"), owner_kind=OwnerKind.RACE)
    assert "Не дать угаснуть" not in skill_detail(content, racial, roster[1])
