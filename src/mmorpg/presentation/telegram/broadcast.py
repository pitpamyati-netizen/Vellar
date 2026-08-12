"""Posts to the game channel.

The channel carries **news about the game**: what changed, what opened, what is
planned, and service notices. It is not a feed of what players did - no level-ups,
no kills, no trades. Those belong in the group, where the people they concern are.
The rules that govern the wording live in ``Narrative.md`` ("Канал"); the ones that
can be enforced by code live here.

A changelog is a news item, not a commit log: it says what a player can now do,
never which module changed.

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
# A changelog earns more room than a notice, because cutting it would mean
# splitting one update across two posts.
CHANGELOG_LIMIT = 2000


class BroadcastKind(StrEnum):
    """What kind of news this is. The kind picks the emoji and the length limit."""

    NEWS = "news"
    CHANGELOG = "changelog"
    SERVICE = "service"


EMOJI: dict[BroadcastKind, str] = {
    BroadcastKind.NEWS: "📣",
    BroadcastKind.CHANGELOG: "🧾",
    BroadcastKind.SERVICE: "🔔",
}


def limit_for(kind: BroadcastKind) -> int:
    return CHANGELOG_LIMIT if kind is BroadcastKind.CHANGELOG else BROADCAST_LIMIT


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
    limit = limit_for(event.kind)
    if len(text) > limit:
        msg = f"broadcast is {len(text)} characters, limit is {limit}"
        raise ValueError(msg)
    return text


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


# --- what the channel actually posts ---------------------------------


def news(headline: str, *details: str) -> BroadcastEvent:
    """A game news item: something opened, changed or is coming."""
    return BroadcastEvent(kind=BroadcastKind.NEWS, headline=headline, details=details)


def service(headline: str, *details: str) -> BroadcastEvent:
    """A service notice: maintenance, downtime, a restart."""
    return BroadcastEvent(kind=BroadcastKind.SERVICE, headline=headline, details=details)


def changelog(
    version: str,
    *,
    added: tuple[str, ...] = (),
    changed: tuple[str, ...] = (),
    fixed: tuple[str, ...] = (),
) -> BroadcastEvent:
    """An update, written for players.

    Each entry is what a player can now do or will now see - never a module, a
    function or a commit. Empty sections are omitted rather than left as headings
    with nothing under them, because a screen reader reads the heading anyway.
    """
    details: list[str] = []
    for title, entries in (("Добавлено", added), ("Изменилось", changed), ("Исправлено", fixed)):
        if not entries:
            continue
        details.append(f"{title}:")
        details.extend(f"— {entry}" for entry in entries)
    if not details:
        msg = "a changelog with no entries is not an update"
        raise ValueError(msg)
    return BroadcastEvent(
        kind=BroadcastKind.CHANGELOG,
        headline=f"Обновление {version}.",
        details=tuple(details),
    )
