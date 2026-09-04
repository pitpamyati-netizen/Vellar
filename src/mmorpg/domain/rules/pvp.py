"""Поединок: игрок против игрока, и оба ходят сами.

Здесь дерутся живые. Раньше на этом месте стоял слепок: бой шёл против копии
чужих чисел, а сам противник узнавал о нападении из сообщения постфактум - так
было потому, что второго игрока боевому движку было некуда положить. Теперь
есть куда, и поединок стал тем, чем назывался: у обоих открывается панель боя,
и ход ждёт того, чья очередь (ADR 0021).

Что осталось прежним - границы. В локации, помеченной ``pvp``, напасть может
всякий, кто стоит на вашем узле, и защищает от этого не согласие, а забор:
нижний уровень, узкое окно уровней и ставка, которая берётся из кармана, а не
из банка.

Ставка нарочно мала. Проигрыш стоит десятой доли золота на руках и ран этого
боя; он никогда не стоит уровня, вещи или задания.

Ждать никого не нужно, и это не оговорка: очередь стоит столько, сколько
стоит, а тот, кто ждать больше не хочет, отдаёт бой кнопкой «Сдаться».
Таймера, наказывающего того, кто отошёл, в игре по-прежнему нет.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.rules.party import split

# Ниже этого уровня никто не нападает и никого не трогают. Персонаж, у которого
# ещё не собрана панель, защищаться нечем, а потерянная первая сотня золота -
# это то, после чего игрок уходит насовсем.
SAFE_LEVEL = 10
# Насколько далеко могут разойтись уровни. Достаточно широко, чтобы приятели
# разных уровней могли размяться, достаточно узко, чтобы никто не пас низ своей
# же полосы.
LEVEL_WINDOW = 3
# Что забирает победивший: десятую долю золота на руках проигравшего. Банк
# неприкосновенен, и ради этого банк и заведён.
SPOILS_PERCENT = 10


@dataclass(frozen=True, slots=True)
class Spoils:
    """Что один законченный поединок передвинул между кошельками."""

    gold: int = 0
    experience: int = 0


def refusal(
    attacker: Character,
    *,
    defender_name: str,
    defender_level: int,
    location_allows: bool,
    defender_busy: bool = False,
    defender_away: bool = False,
) -> str:
    """Пусто, когда нападать можно, иначе - причина, по которой нельзя.

    Причина - целая фраза: отказ, которого игрок не может прочитать, - это баг,
    выглядящий как правило.
    """
    if not location_allows:
        return "Здесь не дерутся друг с другом. Поединки разрешены не везде."
    if attacker.level < SAFE_LEVEL:
        return f"До {SAFE_LEVEL} уровня в поединки не вступают. Ваш уровень: {attacker.level}."
    if defender_level < SAFE_LEVEL:
        return f"{defender_name} ещё под защитой: до {SAFE_LEVEL} уровня на дороге не трогают."
    if abs(attacker.level - defender_level) > LEVEL_WINDOW:
        return (
            f"Разница уровней больше {LEVEL_WINDOW}: "
            f"ваш {attacker.level}, у {defender_name} {defender_level}."
        )
    if defender_busy:
        # Драться сразу в двух боях нельзя: ходы пришли бы на два экрана сразу,
        # и оба слышались бы как один.
        return f"{defender_name} уже в бою. Дождитесь, чем это кончится."
    if defender_away:
        return f"{defender_name} только что ушёл отсюда."
    return ""


def spoils_from(loser_gold: int) -> int:
    """Десятая доля того, что на руках, и ничего вовсе из пустого кармана."""
    return max(0, loser_gold) * SPOILS_PERCENT // 100


def settle(
    winner: Character, loser: Character, *, experience: int = 0
) -> tuple[Character, Character, Spoils]:
    """Передвинуть ставку. Возвращает обоих и то, что перешло из рук в руки.

    Здоровья это не касается: раны уже записал бой, а проигравший теряет всё,
    кроме монет в кармане.
    """
    gold = spoils_from(loser.gold)
    return (
        winner.with_gold(gold),
        loser.with_gold(-gold),
        Spoils(gold=gold, experience=experience),
    )


def settle_sides(
    winners: tuple[Character, ...], losers: tuple[Character, ...]
) -> tuple[tuple[Character, ...], tuple[Character, ...], Spoils]:
    """То же самое, когда сторон больше одной с каждой стороны.

    С каждого проигравшего берётся его десятая доля, и всё вместе делится между
    победившими поровну: отряд, пошедший на отряд, платит и получает как отряд, а
    не как четыре отдельных поединка (``domain/rules/party.split``).
    """
    if not winners or not losers:
        return winners, losers, Spoils()

    taken = 0
    lightened: list[Character] = []
    for one in losers:
        gold = spoils_from(one.gold)
        taken += gold
        lightened.append(one.with_gold(-gold))

    shares = split(taken, len(winners))
    paid = tuple(one.with_gold(share) for one, share in zip(winners, shares, strict=True))
    return paid, tuple(lightened), Spoils(gold=taken)
