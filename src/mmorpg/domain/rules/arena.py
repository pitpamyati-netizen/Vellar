"""Арена: бой, которого не надо ждать.

Арена когда-то была очередью с шестидесятисекундным таймером хода, а значит,
обоим игрокам надо было оказаться у телефона в одну минуту, и тот, кто
медленнее, проигрывал часам. Играть в это не мог никто, а таймер — ровно то,
чего эта игра обещает не иметь (``docs/accessibility.md``, правило 13).

Поэтому круг арены дерётся против персонажа другого игрока, **которым играет
движок**: тот же боец, какого обычный боевой движок собирает кому угодно, только
его нажатия никто не ждёт (ADR 0021). Дерётся он своим оружием и своими
умениями; его не вызывают, не торопят, и теряет он ничего — ставка стоит между
каждым бойцом и собственным кошельком арены, а не между этими двумя.

- ставка растёт с уровнем, поэтому на 200-м она стоит того же, что на 20-м;
- победа возвращает ставку и столько же сверху из того, что арена уже держит с
  тебя;
- таблица сезона считает победы, и больше она не считает ничего.

**Арена не печатает золото.** Печатала: постоянная двойная выплата означала, что
всякий, кто выигрывает больше половины кругов, вносит в игру золото из ниоткуда,
и это был единственный приток, не зависящий от победы над чем-либо, что мир
поставил перед игроком. Теперь арена платит из долга, который держит, — из
ставок, взятых с этого персонажа и ещё не возвращённых, — поэтому за всю жизнь
никто не заберёт из неё больше, чем в неё положил. Выигрывают там счёт, а не
доход.

Новичку первый круг выдают в долг (``WELCOME_ROUNDS``), потому что первая победа,
платящая ровно ставку, читается ошибкой, а не правилом.
"""

from __future__ import annotations

from dataclasses import dataclass

from mmorpg.domain.entities.character import Character

# Сколько стоит круг, по уровням. Примерно десятая часть того, что платит час обычной
# работы, чтобы полоса поражений раздражала, а не разоряла.
STAKE_BASE = 20
STAKE_PER_LEVEL = 12
# Лучшее, что арена платит вообще: ставка обратно и столько же сверху.
PAYOUT_FACTOR = 2
# На сколько кругов вперёд арена выдаёт ставку тому, кто в ней не дрался. На один: ровно
# столько, чтобы первая победа заплатила по вывеске.
WELCOME_ROUNDS = 1
# На арену не выходят раньше, чем появится панель, которой на ней драться.
MIN_LEVEL = 5
# Насколько далеко могут стоять два уровня, чтобы арена сочла это парой.
LEVEL_WINDOW = 3


def stake_for(level: int) -> int:
    return STAKE_BASE + STAKE_PER_LEVEL * max(0, level - 1)


def held_for(character: Character) -> int:
    """Что арена держит с этого персонажа до ставки нынешнего круга.

    Тот, кто не дрался ни разу, держит вместо этого приветствие: арена выдаёт ему
    одну ставку вперёд, чтобы первая победа заплатила столько, сколько обещает
    вывеска.
    """
    if not character.arena_wins and not character.arena_losses:
        return WELCOME_ROUNDS * stake_for(character.level)
    return character.arena_credit


def payout_of(stake: int, held: int) -> int:
    """Чем платит победа: ставка обратно плюс столько от залога, сколько её удваивает.

    ``held`` - то, что арена держала с персонажа *до* круга. Добавка ограничена
    ставкой, поэтому выплата никогда не больше двойной, и ограничена залогом, поэтому
    арена никогда не выплачивает того, чего не приняла.
    """
    return stake + min(stake, max(0, held))


def payout_for(character: Character) -> int:
    """Чем заплатила бы победа этому персонажу, дерись он прямо сейчас."""
    return payout_of(stake_for(character.level), held_for(character))


@dataclass(frozen=True, slots=True)
class Round:
    """Что один закрытый круг сделал с тем, кто его дрался."""

    character: Character
    stake: int
    payout: int = 0
    won: bool = False
    #: Что арена держит с них, когда этот круг закрыт.
    held: int = 0


def refusal(character: Character) -> str:
    """Пусто, когда круг драться можно, иначе - причина, по которой нельзя."""
    if character.level < MIN_LEVEL:
        return (
            f"На арену выходят с {MIN_LEVEL} уровня. Ваш уровень: {character.level}. "
            "До этого дерутся на дороге."
        )
    stake = stake_for(character.level)
    if character.gold < stake:
        return f"Ставка арены — {stake} золота, у вас {character.gold}."
    return ""


def place_stake(character: Character) -> tuple[Character, int]:
    """Взять ставку до боя: круг, за который никто не заплатил, кругом не считается.

    Ставка уходит в залог арены на этом персонаже, и как раз из него потом платится
    победа.
    """
    stake = stake_for(character.level)
    staked = character.with_gold(-stake).with_arena_credit(held_for(character) + stake)
    return staked, stake


def settle(character: Character, *, won: bool) -> Round:
    """Расплатиться за законченный круг. Ставка уже в залоге арены."""
    stake = stake_for(character.level)
    counted = character.with_arena_result(won=won)
    # Залог уже включает ставку этого круга: её положил туда ``place_stake``.
    held = max(0, counted.arena_credit - stake)
    if not won:
        # Ставка остаётся где лежит: арена продолжает её держать, и как раз из неё
        # платится будущая победа.
        return Round(character=counted, stake=stake, payout=0, won=False, held=counted.arena_credit)

    payout = payout_of(stake, held)
    paid = counted.with_gold(payout).with_arena_credit(held + stake - payout)
    return Round(character=paid, stake=stake, payout=payout, won=True, held=paid.arena_credit)
