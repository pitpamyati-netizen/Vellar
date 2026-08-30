"""Экран арены: ставка, счёт, таблица, одна кнопка.

Всё, что игроку нужно знать до того, как он поставит золото, стоит на экране
раньше любого нажатия: сколько стоит круг, сколько он платит, как дела шли до
сих пор и кто впереди в этом сезоне. Очереди, в которую надо встать, нет, и
ждать нечего.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

ARENA_FIGHT = label("Выйти на арену", "🥊")


def arena_screen(
    character: Character,
    table: Sequence[Character] = (),
    notice: str = "",
) -> Screen:
    stake = arena_rules.stake_for(character.level)
    payout = arena_rules.payout_for(character)
    held = arena_rules.held_for(character)
    refused = arena_rules.refusal(character)

    lines = [
        *head("Арена.", notice),
        "Здесь спор решают боем: Престолу дешевле принять исход боя, чем считать чужие долги.",
        f"Ставка: {stake} золота. Выигрыш: {payout} золота, проигрыш — ставка.",
        # Всё правило одной фразой, на том экране, где ставят золото: арена отдаёт долги
        # и никогда не выдумывает денег.
        f"Арена держит ваших {held} золота: сверху ставки она отдаёт только их, "
        "и отыграться на ней можно ровно на то, что на ней оставлено.",
        "Соперника ждать не нужно: против вас выставят снимок другого приключенца "
        "вашего уровня. Он об этом не узнает и ничего не потеряет.",
        f"Ваш счёт: побед {character.arena_wins}, поражений {character.arena_losses}.",
        f"У вас {character.gold} золота.",
    ]
    if refused:
        lines.append(refused)
    if table:
        lines.append("Таблица сезона:")
        lines.extend(
            f"{place}. {fighter.name}, побед {fighter.arena_wins}."
            for place, fighter in enumerate(table, start=1)
        )

    rows: tuple[tuple[Label, ...], ...] = () if refused else ((ARENA_FIGHT,),)
    return Screen(id=ScreenId.ARENA, lines=tuple(lines), rows=rows)


def round_line(result: arena_rules.Round) -> str:
    """Что стоит сказать об одном закрытом круге, одной фразой."""
    if result.won:
        over = result.payout - result.stake
        top_up = f"сверху {over}" if over else "сверху ничего: арена ваших денег не держит"
        return (
            f"Бой выигран. Ставка {result.stake} возвращена, {top_up}. "
            f"Побед: {result.character.arena_wins}. Арена держит ваших {result.held}."
        )
    return (
        f"Бой проигран. Ставка {result.stake} осталась на арене, и следующая победа "
        f"вернёт её. Поражений: {result.character.arena_losses}. "
        f"Арена держит ваших {result.held}."
    )
