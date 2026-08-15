"""The arena screen: the stake, the record, the table, one button.

Everything a player needs before they commit gold is on the screen before they
press anything: what a round costs, what it pays, how they have done so far, and
who is ahead this season. There is no queue to join and nothing to wait for.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules import arena as arena_rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

ARENA_FIGHT = label("Выйти в круг", "🥊")


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
        notice or "Долговой круг. Спор решают боем, Палата в него не вникает.",
        f"Ставка круга: {stake} золота. Выигрыш: {payout} золота, проигрыш — ставка.",
        # The whole rule in one sentence, on the screen where the gold is
        # committed: the Circle hands back debts and never invents money.
        f"Круг держит ваших {held} золота: сверху ставки он отдаёт только их, "
        "и отыграться в нём можно ровно на то, что в нём оставлено.",
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
    """What one settled round is worth saying, in one sentence."""
    if result.won:
        over = result.payout - result.stake
        top_up = f"сверху {over}" if over else "сверху ничего: круг ваших денег не держит"
        return (
            f"Круг выигран. Ставка {result.stake} возвращена, {top_up}. "
            f"Побед: {result.character.arena_wins}. Круг держит ваших {result.held}."
        )
    return (
        f"Круг проигран. Ставка {result.stake} осталась в круге, и следующая победа "
        f"вернёт её. Поражений: {result.character.arena_losses}. "
        f"Круг держит ваших {result.held}."
    )
