"""Что игрок слышит после своего хода.

Ход в Vellar — это несколько событий подряд: разгон, брешь, удар игрока, ответ
врага. Экран боя показывал два последних, и удар игрока — он идёт первым —
регулярно выталкивался наружу. По слуху это неотличимо от «кнопка не сработала».
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import (
    ActionKind,
    CombatAction,
    CombatOutcome,
    CombatState,
)
from mmorpg.domain.rules.combat import resolve_turn
from mmorpg.presentation.telegram.screens import combat as combat_screens


def test_the_player_blow_is_always_read_out(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    resolved = resolve_turn(
        content,
        fighter,
        sample_fight,
        CombatAction(kind=ActionKind.ATTACK),
        seed=b"turn-seed",
    )
    text = combat_screens.combat_screen(content, fighter, resolved).text()

    said = [combat_screens.describe_event(event, resolved.player.name) for event in resolved.events]
    assert said, "ход без единого события — это уже другая ошибка"
    for line in said:
        if line:
            assert line in text, "экран боя молчит о том, что случилось в этот ход"


def test_a_turn_with_momentum_still_names_the_blow(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    """Разгон, брешь и ответ врага раньше вытесняли собственный удар игрока."""
    state = sample_fight
    for _ in range(3):
        state = resolve_turn(
            content,
            fighter,
            state,
            CombatAction(kind=ActionKind.ATTACK),
            seed=b"same-tag-again",
        )
        if state.is_over:
            return
        text = combat_screens.combat_screen(content, fighter, state).text()
        mine = [
            line
            for event in state.events
            if (line := combat_screens.describe_event(event, state.player.name))
            and event.actor == state.player.name
        ]
        for line in mine:
            assert line in text


def test_a_button_says_what_weapon_it_needs(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    """Кнопка обещает ровно то, что сделает: «Вихрь клинков» без клинка — не сделает.

    Прежде такая кнопка стояла готовой, нажималась и тратила ход на отказ; для
    того, кто играет на слух, это неотличимо от «кнопка не сработала».
    """
    barehanded = replace(
        fighter,
        level=20,
        loadout=replace(
            fighter.loadout, actives=("warrior_blade_whirl", None, None, None, None, None)
        ),
    )
    text = combat_screens.skill_label(content, barehanded, sample_fight, 0).text
    assert "нужно оружие" in text
    assert content.weapon_type("sword").name.lower() in text

    armed = replace(barehanded, equipment=barehanded.equipment.equip("weapon", "sword@1#common"))
    assert "нужно оружие" not in combat_screens.skill_label(content, armed, sample_fight, 0).text


def test_the_refusal_is_read_out_when_the_button_is_pressed_anyway(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    """Отказ — это событие боя, а не молчание: ход при этом не тратится впустую."""
    barehanded = replace(
        fighter,
        level=20,
        loadout=replace(
            fighter.loadout, actives=("warrior_blade_whirl", None, None, None, None, None)
        ),
    )
    resolved = resolve_turn(
        content,
        barehanded,
        sample_fight,
        CombatAction(kind=ActionKind.SKILL, slot=0),
        seed=b"turn-seed",
    )
    said = [combat_screens.describe_event(event, resolved.player.name) for event in resolved.events]
    assert any("просит другое оружие" in line for line in said)


# --- the turn that ended the fight is a turn too ---------------------


def _fight_to_the_end(content: GameContent, fighter: Character, state: CombatState) -> CombatState:
    """Бьёт, пока бой не кончится. Возвращает последнее состояние."""
    for turn in range(60):
        state = resolve_turn(
            content,
            fighter,
            state,
            CombatAction(kind=ActionKind.ATTACK),
            seed=turn.to_bytes(16, "big"),
        )
        if state.is_over:
            return state
    raise AssertionError("бой не кончился за шестьдесят ходов")


def test_the_winning_turn_is_read_out(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    """Победа - это тоже ход, и его надо услышать.

    Экран победы начинался с «Опыт, золото» и молчал о том, чем всё кончилось:
    последний удар, добивание, ответ врага - ничего этого игрок не слышал.
    """
    won = _fight_to_the_end(content, fighter, sample_fight)
    assert won.outcome is CombatOutcome.VICTORY
    text = combat_screens.victory_screen(won).text()
    said = [combat_screens.describe_event(event, won.player.name) for event in won.events]
    assert said
    for line in said:
        if line:
            assert line in text, "экран победы молчит о последнем ходе"


def test_the_losing_turn_is_read_out(
    content: GameContent, fighter: Character, sample_fight: CombatState
) -> None:
    """И поражение тоже: «Поражение.» без последнего хода ничего не объясняет."""
    doomed = replace(sample_fight, player=replace(sample_fight.player, health=1))
    lost = _fight_to_the_end(content, fighter, doomed)
    assert lost.outcome is CombatOutcome.DEFEAT
    text = combat_screens.defeat_screen(lost, gold_lost=10).text()
    said = [combat_screens.describe_event(event, lost.player.name) for event in lost.events]
    assert said
    for line in said:
        if line:
            assert line in text, "экран поражения молчит о последнем ходе"
