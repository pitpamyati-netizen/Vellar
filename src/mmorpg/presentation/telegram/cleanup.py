"""Deleting the bot's own messages in the group after five minutes.

The group belongs to the people talking in it. A bot that leaves every answer in
place turns a conversation into a log, and for someone reading by ear that log is
worse than clutter: scrolling back through it costs real time. So a group message
is temporary - it is delivered, read, and removed (``Narrative.md``, section 9).

**Only in the group.** Nothing is ever deleted in a private chat, where the
message *is* the screen and the player re-reads it as long as they need
(``docs/accessibility.md``, rule 3).

The deletion is a background task with no result anyone waits on. It must never
touch the update that produced it: a message deleted by a moderator first, a lost
admin right, a network blip - all of them are dropped quietly, exactly like a
failed broadcast.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mmorpg.logging import get_logger

logger = get_logger(__name__)

# The same five minutes an offer lives, and for the same reason: it is how long a
# message stays worth reading.
GROUP_MESSAGE_TTL_SECONDS = 300.0

Deleter = Callable[[int, int], Awaitable[object]]


@dataclass(slots=True)
class MessageReaper:
    """Schedules deletions and owns the tasks that perform them."""

    delay: float = GROUP_MESSAGE_TTL_SECONDS
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def schedule(self, deleter: Deleter, chat_id: int, message_id: int) -> None:
        """Delete one message once the delay has passed."""
        task = asyncio.create_task(self._reap(deleter, chat_id, message_id))
        # asyncio keeps only a weak reference to a running task, so a set that
        # outlives the call is the difference between a deletion and a lost one.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reap(self, deleter: Deleter, chat_id: int, message_id: int) -> None:
        try:
            await asyncio.sleep(self.delay)
            await deleter(chat_id, message_id)
        except asyncio.CancelledError:
            raise
        # Broad on purpose: an undeletable message is untidy, never fatal.
        except Exception as error:
            logger.debug("group_message_not_deleted", message_id=message_id, error=str(error))

    async def aclose(self) -> None:
        """Cancel everything still waiting. Called from the shutdown stack."""
        pending = tuple(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
