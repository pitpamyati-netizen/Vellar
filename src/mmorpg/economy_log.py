"""Where gold comes into the game and where it goes out of it.

Three of the numbers this game is balanced on cannot be decided at a desk: the
duty on a trade between players, the stake and the payout of the Debt Circle, and
what a fight is worth (Roadmap, "Риски"). All three were written down as guesses
to be corrected "по первым суткам ОБТ" - and there was nothing in the game that
would make those first twenty-four hours legible. A number nobody can measure is
not tuned, it is re-guessed.

So every movement of gold that is not one player handing another player a coin
writes one line:

    gold_flow flow=fight amount=124 character_id=17

``flow`` is what happened, ``amount`` is signed - positive is gold entering this
character's purse, negative is gold leaving it. Summed over a day by flow, that
is the whole economy: what the world pays out, what the cities take back, what
the duty removes, and whether the Circle is a hole or a fountain.

This is a log and nothing else. Nothing reads it back, no screen shows it, and no
rule depends on it - it exists so that the first correction to those constants is
made against numbers instead of against a feeling.
"""

from __future__ import annotations

from mmorpg.logging import get_logger

logger = get_logger(__name__)

# The flows worth telling apart. Anything not here is not measured, which is a
# decision to make on purpose rather than by forgetting.
FIGHT = "fight"  # what an opponent carried
SEARCH = "search"  # a cache, a shrine, a quiet node
DESCENT = "descent"  # the bottom of a dungeon run
QUEST = "quest"  # a contract paid out
DEFEAT = "defeat"  # a tenth of the purse, left where the fight was lost
DUEL = "duel"  # taken from another player, or lost to one
ARENA_STAKE = "arena_stake"  # into the Circle
ARENA_PAYOUT = "arena_payout"  # back out of it
TRADE_PRICE = "trade_price"  # what one player paid another
TRADE_DUTY = "trade_duty"  # what the duty took out of the game
TRADE_ROLLBACK = "trade_rollback"  # a settled trade a keeper undid
SHOP = "shop"  # bought from or sold to a city
SERVICE = "service"  # a bed, a teacher, anything a city charges for
KEEPER = "keeper"  # granted by a keeper, and therefore not economy at all


def record(flow: str, amount: int, *, character_id: int, detail: str = "") -> None:
    """Write down one movement of gold. Zero is not a movement."""
    if not amount:
        return
    logger.info(
        "gold_flow",
        flow=flow,
        amount=amount,
        character_id=character_id,
        detail=detail,
    )
