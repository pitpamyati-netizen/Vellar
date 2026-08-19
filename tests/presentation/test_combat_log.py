"""Что игрок слышит после своего хода.

Ход в Vellar — это несколько событий подряд: разгон, брешь, удар игрока, ответ
врага. Экран боя показывал два последних, и удар игрока — он идёт первым —
регулярно выталкивался наружу. По слуху это неотличимо от «кнопка не сработала».
"""

from __future__ import annotations

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import ActionKind, CombatAction, CombatState
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
