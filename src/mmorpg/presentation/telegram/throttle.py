"""Per-player rate limiting for the group.

A group is public, so anything the bot says there is said to everyone. That makes
flooding the cheapest attack available: a handful of "профиль" replies a second
and the chat is unreadable, which hurts a screen reader user far more than a
sighted one scrolling past.

The limit is a sliding window per Telegram account, not per chat: the point is to
slow one person down, never to make the group quiet for everybody else.

A player who trips the limit is told **once**. The alternative - answering every
blocked command - would turn a flood into two floods, and silence with no
explanation at all would look like the bot broke.

The clock is a parameter, so the tests move time instead of waiting.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

# Six commands a minute is far more than a conversation needs and far less than
# a flood: a player answering an offer plus checking two profiles stays inside it.
RATE_LIMIT = 6
RATE_WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class RateLimiter:
    """Sliding-window limiter with a one-shot warning per window."""

    limit: int = RATE_LIMIT
    window: float = RATE_WINDOW_SECONDS
    clock: Callable[[], float] = time.monotonic
    _hits: dict[int, deque[float]] = field(default_factory=dict)
    _warned: set[int] = field(default_factory=set)
    _swept_at: float = 0.0

    def allow(self, user_id: int) -> bool:
        """Record one command. ``False`` means it must not be acted on."""
        now = self.clock()
        self._sweep(now)
        hits = self._hits.setdefault(user_id, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            return False

        hits.append(now)
        self._warned.discard(user_id)
        return True

    def should_warn(self, user_id: int) -> bool:
        """Whether this refusal is the first of the window, and so worth saying."""
        if user_id in self._warned:
            return False
        self._warned.add(user_id)
        return True

    def forget(self, user_id: int) -> None:
        """Drop a player's history. Used by the tests and by moderation."""
        self._hits.pop(user_id, None)
        self._warned.discard(user_id)

    def _sweep(self, now: float) -> None:
        """Forget everyone whose window has passed.

        Without this the bot would keep one deque per account it ever saw, and a
        group that runs for months would pay for every visitor who said one word.
        """
        if now - self._swept_at < self.window:
            return
        self._swept_at = now
        stale = [
            user_id
            for user_id, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window
        ]
        for user_id in stale:
            self.forget(user_id)
