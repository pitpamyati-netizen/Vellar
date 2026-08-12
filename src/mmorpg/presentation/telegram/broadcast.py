"""Broadcasts to the game channel.

The channel is a log, not a feed: every post states a fact that already happened,
in the order it happened, in one or two dry lines. The rules that govern the
wording live in ``Narrative.md`` ("Бродкасты"); the ones that can be enforced by
code live here.

Enforced here:

- the headline is the whole message for a reader who stops after one line;
- plain text only, ``parse_mode=None`` - the channel is read by screen readers too,
  and Markdown is spoken aloud (accessibility rule 14);
- no pseudo-graphics, no bars, numbers spelled as ``X из Y`` by the caller (rule 5);
- one emoji at most, at the head of the line, chosen by meaning - never decoration
  (rule 6: the text is unambiguous with every emoji stripped);
- a hard length limit, because a channel post is not paginated.

A broadcast never blocks or breaks gameplay: a failed send is logged and dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mmorpg.logging import get_logger

logger = get_logger(__name__)

BROADCAST_LIMIT = 700

# Levels worth a post. Anything more often turns the channel into noise nobody
# reads, which is the same as having no channel.
NOTABLE_LEVELS: frozenset[int] = frozenset({1, 10, 25, 50, 75, 100, 150, 200, 250, 300})


class BroadcastKind(StrEnum):
    """What happened. The kind picks the emoji and nothing else."""

    ARENA = "arena"
    LEVEL = "level"
    CITY = "city"
    TRADE = "trade"
    BOSS = "boss"
    CYCLE = "cycle"
    SERVICE = "service"


EMOJI: dict[BroadcastKind, str] = {
    BroadcastKind.ARENA: "⚔️",
    BroadcastKind.LEVEL: "📈",
    BroadcastKind.CITY: "🏰",
    BroadcastKind.TRADE: "🛒",
    BroadcastKind.BOSS: "🐉",
    BroadcastKind.CYCLE: "🌘",
    BroadcastKind.SERVICE: "🔔",
}


@dataclass(frozen=True, slots=True)
class BroadcastEvent:
    """One channel post: a headline and, optionally, the detail behind it."""

    kind: BroadcastKind
    headline: str
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.headline.strip():
            msg = "a broadcast without a headline says nothing"
            raise ValueError(msg)


def render_broadcast(event: BroadcastEvent, *, emoji: bool = True) -> str:
    """Render the post. The first line stands on its own; details follow."""
    head = event.headline.strip()
    if emoji:
        head = f"{EMOJI[event.kind]} {head}"
    lines = [head, *(detail.strip() for detail in event.details if detail.strip())]
    text = "\n".join(lines)
    if len(text) > BROADCAST_LIMIT:
        msg = f"broadcast is {len(text)} characters, limit is {BROADCAST_LIMIT}"
        raise ValueError(msg)
    return text


def is_notable_level(level: int) -> bool:
    """Whether reaching this level is worth a post."""
    return level in NOTABLE_LEVELS


class ChannelSink(Protocol):
    """The one Telegram call a broadcaster makes. ``aiogram.Bot`` satisfies it."""

    async def send_message(self, chat_id: int | str, text: str) -> object: ...


def chat_id_of(raw: str) -> int | str:
    """``-1001234567890`` becomes an int, ``@vellar`` stays a string."""
    value = raw.strip()
    try:
        return int(value)
    except ValueError:
        return value


@dataclass(slots=True)
class ChannelBroadcaster:
    """Posts events to the game channel, or to nowhere when it is not configured.

    An unconfigured channel is the normal state of a local run, so this is a
    no-op rather than an error: the game must be playable without a channel.
    """

    sink: ChannelSink | None
    chat_id: str = ""
    emoji: bool = True

    @property
    def enabled(self) -> bool:
        return self.sink is not None and bool(self.chat_id.strip())

    async def announce(self, event: BroadcastEvent) -> bool:
        """Post one event. Returns whether it actually reached Telegram."""
        text = render_broadcast(event, emoji=self.emoji)
        if not self.enabled or self.sink is None:
            logger.debug("broadcast_skipped", kind=event.kind.value, reason="no_channel")
            return False
        try:
            await self.sink.send_message(chat_id_of(self.chat_id), text)
        # Broad on purpose: a dead channel, a revoked admin right or a network
        # blip must never take down the turn that produced the event.
        except Exception as error:
            logger.warning("broadcast_failed", kind=event.kind.value, error=str(error))
            return False
        logger.info("broadcast_sent", kind=event.kind.value)
        return True


# --- the events the game actually posts ------------------------------


def level_reached(name: str, level: int) -> BroadcastEvent:
    return BroadcastEvent(
        kind=BroadcastKind.LEVEL,
        headline=f"{name} достигает {level} уровня.",
    )


def city_unlocked(name: str, city: str, level: int) -> BroadcastEvent:
    return BroadcastEvent(
        kind=BroadcastKind.CITY,
        headline=f"{city}: первым дошёл {name}, уровень {level}.",
    )


def arena_result(winner: str, loser: str, rounds: int, *, timeout: bool = False) -> BroadcastEvent:
    tail = " Бой закончен по истечении времени хода." if timeout else ""
    return BroadcastEvent(
        kind=BroadcastKind.ARENA,
        headline=f"Соляной Круг: {winner} побеждает {loser} за {rounds} круга.{tail}".strip(),
    )


def trade_completed(seller: str, buyer: str, item: str, price: int) -> BroadcastEvent:
    return BroadcastEvent(
        kind=BroadcastKind.TRADE,
        headline=f"Сделка закрыта: {item}, {price} золотых.",
        details=(f"Продавец {seller}, покупатель {buyer}.",),
    )


def boss_defeated(name: str, boss: str, place: str) -> BroadcastEvent:
    return BroadcastEvent(
        kind=BroadcastKind.BOSS,
        headline=f"{boss} побеждён. {place}.",
        details=(f"Отряд: {name}.",),
    )


def cycle_turned(cycle: int) -> BroadcastEvent:
    return BroadcastEvent(
        kind=BroadcastKind.CYCLE,
        headline=f"Отлив {cycle}. Земли переписаны, зачистка узлов сброшена.",
        details=("Ассортимент лавок обновлён.",),
    )
