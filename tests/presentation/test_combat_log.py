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
    BattleAction,
    BattleState,
    Verdict,
)
from mmorpg.domain.rules.combat import act
from mmorpg.presentation.telegram.screens import combat as combat_screens

#: Номер героя в фикстурах боя: он всегда собирается первым.
HERO = 1


def hero_of(state: BattleState) -> object:
    one = state.by_id(HERO)
    assert one is not None
    return one


def test_the_player_blow_is_always_read_out(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    resolved = act(
        content,
        {HERO: fighter},
        sample_fight,
        BattleAction(kind=ActionKind.ATTACK),
        b"turn-seed",
    )
    text = combat_screens.battle_screen(content, fighter, resolved, HERO).text()

    said = [combat_screens.describe_event(event, HERO) for event in resolved.events]
    assert said, "ход без единого события — это уже другая ошибка"
    for line in said:
        if line:
            assert line in text, "экран боя молчит о том, что случилось в этот ход"


def test_a_turn_with_momentum_still_names_the_blow(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    """Разгон, брешь и ответ врага раньше вытесняли собственный удар игрока."""
    state = sample_fight
    for _ in range(3):
        state = act(
            content,
            {HERO: fighter},
            state,
            BattleAction(kind=ActionKind.ATTACK),
            b"same-tag-again",
        )
        if state.is_over:
            return
        text = combat_screens.battle_screen(content, fighter, state, HERO).text()
        mine = [
            line
            for event in state.events
            if (line := combat_screens.describe_event(event, HERO)) and event.actor_id == HERO
        ]
        for line in mine:
            assert line in text


def test_a_button_says_what_weapon_it_needs(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    """Кнопка обещает ровно то, что сделает: «Вихрь клинков» без клинка — не сделает."""
    barehanded = replace(
        fighter,
        level=20,
        loadout=replace(
            fighter.loadout, actives=("warrior_vikhr_klinkov", None, None, None, None, None)
        ),
    )
    viewer = sample_fight.by_id(HERO)
    assert viewer is not None
    text = combat_screens.skill_label(content, barehanded, viewer, 0).text
    assert "нужно оружие" in text
    assert content.weapon_type("sword").name.lower() in text

    armed = replace(barehanded, equipment=barehanded.equipment.equip("weapon", "sword@1#common"))
    assert "нужно оружие" not in combat_screens.skill_label(content, armed, viewer, 0).text


def test_the_refusal_is_read_out_when_the_button_is_pressed_anyway(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    """Отказ — это событие боя, а не молчание: ход при этом не тратится впустую."""
    barehanded = replace(
        fighter,
        level=20,
        loadout=replace(
            fighter.loadout, actives=("warrior_vikhr_klinkov", None, None, None, None, None)
        ),
    )
    resolved = act(
        content,
        {HERO: barehanded},
        sample_fight,
        BattleAction(kind=ActionKind.SKILL, slot=0),
        b"turn-seed",
    )
    said = [combat_screens.describe_event(event, HERO) for event in resolved.events]
    assert any("просит другое оружие" in line for line in said)


# --- ход, которым бой кончился, - тоже ход ----------------------------


def _fight_to_the_end(content: GameContent, fighter: Character, state: BattleState) -> BattleState:
    """Бьёт, пока бой не кончится. Возвращает последнее состояние."""
    for turn in range(60):
        state = act(
            content,
            {HERO: fighter},
            state,
            BattleAction(kind=ActionKind.ATTACK),
            turn.to_bytes(16, "big"),
        )
        if state.is_over:
            return state
    raise AssertionError("бой не кончился за шестьдесят ходов")


def test_the_winning_turn_is_read_out(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    """Победа - это тоже ход, и его надо услышать."""
    won = _fight_to_the_end(content, fighter, sample_fight)
    assert won.verdict_for(HERO) is Verdict.VICTORY
    text = combat_screens.victory_screen(won, HERO, experience=40, gold=14).text()
    said = [combat_screens.describe_event(event, HERO) for event in won.events]
    assert said
    for line in said:
        if line:
            assert line in text, "экран победы молчит о последнем ходе"


def test_the_losing_turn_is_read_out(
    content: GameContent, fighter: Character, sample_fight: BattleState
) -> None:
    """И поражение тоже: «Поражение.» без последнего хода ничего не объясняет."""
    hero = sample_fight.by_id(HERO)
    assert hero is not None
    doomed = sample_fight.replace_combatant(replace(hero, health=1))
    lost = _fight_to_the_end(content, fighter, doomed)
    assert lost.verdict_for(HERO) is Verdict.DEFEAT
    text = combat_screens.defeat_screen(lost, HERO, gold_lost=10).text()
    said = [combat_screens.describe_event(event, HERO) for event in lost.events]
    assert said
    for line in said:
        if line:
            assert line in text, "экран поражения молчит о последнем ходе"


def test_the_waiting_screen_says_whose_turn_it_is(
    content: GameContent, duel_fight: BattleState
) -> None:
    """Ожидание чужого хода - это экран, а не тишина (ADR 0021)."""
    current = duel_fight.active
    assert current is not None
    watcher = next(one for one in duel_fight.combatants if one.id != current.id)
    screen = combat_screens.waiting_screen(content, duel_fight, watcher.id)
    text = screen.text()
    assert current.name in text
    assert "Ждём" in text
    pressed = [label for row in screen.button_texts() for label in row]
    assert any("Сдаться" in label for label in pressed)
