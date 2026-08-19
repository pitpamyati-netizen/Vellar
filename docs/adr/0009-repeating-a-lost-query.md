# ADR 0009 - Repeating a call that lost its connection

Status: accepted (2026-08-16)

## Context

PostgreSQL restarts, Redis is restarted by an update, a network hiccup drops a
socket. The pools handle the *connection* part on their own: asyncpg throws away a
dead connection on the next `acquire` and opens a fresh one, redis-py does the
same. What neither does is run the call that was in flight when the link went
down, so a database that was unreachable for a second and a half cost every player
who pressed a button in that second an action, and gave them «Что-то пошло не
так» about a game that is already healthy again.

Repeating a call is not free of consequences. A statement can die in two places,
and they are not the same place:

- before it was sent - PostgreSQL never heard of it;
- after it was sent - PostgreSQL may have committed it and lost only the answer
  on the way back.

The second case is invisible from the client: asyncpg raises the same
`ConnectionDoesNotExistError` either way.

## Decision

Every call goes through a wrapper that repeats it, and the line between "repeat"
and "report" is drawn by what is *certain*, not by what is likely.

**PostgreSQL** (`infrastructure/persistence/reconnect.py`). The wrapper acquires
the connection itself, which splits the two cases apart:

- the connection was never obtained - nothing was sent, so anything is repeated,
  a write included;
- the connection was obtained and the statement failed on it - a `SELECT` is
  repeated, because reading twice reads the same thing; anything else is not.

A lost `UPDATE ... WHERE gold >= $2` is therefore reported and never re-run. The
player is told the action failed, which is recoverable, instead of silently
paying twice, which is not.

**Redis.** redis-py reconnects and re-sends the command itself; it is configured
to (`create_redis_client`) rather than left on its defaults. Repeating is safe for
everything the game keeps there - a screen, a fight, a location, a shop roll are
all written whole.

**Telegram** (`presentation/telegram/middlewares/retry.py`). A request that died
on a broken socket is made again; anything Telegram *answered* - a bad request, a
player who blocked the bot - is not. `getUpdates` is left alone: aiogram's polling
loop already has an endless backoff around it.

**Startup** waits for all three (`startup_wait_seconds`) instead of exiting: a
stack that comes up together does not come up in order.

## Consequences

- A restart of PostgreSQL or Redis under a running game is invisible to players
  who are reading, and costs at most one action to players who were writing at
  that exact moment.
- The asymmetry has to be remembered when writing SQL: a statement that both
  reads and writes (`UPDATE ... RETURNING`) is a write here, because it is one.
- A Telegram request that reached the servers and lost only its reply is sent
  twice, so a player can see one screen twice. For a screen reader user a
  repeated screen is noise and a missing screen is a dead end, so this trade is
  taken deliberately.
- A transaction is not repeated at all: `acquire()` is handed out bare, because
  the wrapper cannot know where the transaction began.
- Held by `tests/infrastructure/test_reconnect.py`,
  `tests/presentation/test_retry_middleware.py` and `tests/test_retry.py`; the
  integration suite runs its SQL through the same wrapper.
