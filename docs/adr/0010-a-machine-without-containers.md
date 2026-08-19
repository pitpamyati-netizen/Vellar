# ADR 0010 - A machine without containers: PostgreSQL, no Redis

Status: accepted (2026-08-19)

## Context

Running the game with anything kept meant Docker Desktop, which is several
gigabytes on a machine that is otherwise only asked for Python and a database.
The two modes that existed were the far ends of a range: `local` keeps nothing
and needs nothing, the stack keeps everything and needs Docker. What was missing
is the case that is actually being asked for - one person, one machine, a world
that is still there tomorrow.

PostgreSQL installs on Windows from one installer. Redis does not: it has no
supported Windows build, so it comes as WSL or as a third-party port, which is
the whole problem again in a smaller box.

## Decision

A fourth `APP_ENV`, `solo`: long polling against PostgreSQL, with everything that
was in Redis held by the process instead.

The choice is no longer one flag. `Settings.uses_postgres` and
`Settings.uses_redis` are decided separately, and `main._build_session_state`
picks the session half on its own - `RedisStorage` and the Redis caches for `dev`
and `prod`, `MemoryStorage` and the in-memory caches for `solo`.

The split follows what the two stores actually hold:

| PostgreSQL | Redis |
| --- | --- |
| who a character is, and everything they own | where they are standing |
| gold, and every movement of it | the fight they are in the middle of |
| contracts, craft work, keeper edits | the map of a location, the shop shelf |
| | which updates have already been handled |

The left column is the world and must survive. The right column is a session,
and every entry in it is already written to be lost safely (`Claude.md`, rule 8):
a screen that no longer exists returns the player to a live one, a location
without a map is generated again from its seed, a shop shelf is derived from the
seed and the clock.

## Consequences

- `Start.bat solo` needs PostgreSQL and `uv`, and nothing else. `Start.bat
  setup-db` creates the role and the database once, `scripts/setup-db.sql` being
  idempotent; the schema is then the same `alembic upgrade head` the stack runs.
- A restart costs more than in the stack, and it is stated plainly rather than
  discovered: a fight in progress ends and everyone is put back in the main menu,
  unhurt. Nothing they own is touched.
- Deduplication of updates is per process, so a restart could let a repeated
  update through. One update, in the second either side of a restart that also
  dropped the fight - a small cost next to the service it removes.
- In-memory session state is only correct because polling is one process
  (`run_polling`). `prod` may sit behind several, and keeps Redis: two processes
  sharing nothing would each think they know where a player is standing.
- The dump is the same file in both directions: `stop.bat` takes it through the
  container when the stack is up and through the machine's own `pg_dump` when it
  is not, so a world can be carried from a solo run to a stack and back.
- Redis stays in the stack, in `docker-compose.yml` and in the sizing in
  `docs/deployment.md`. This ADR does not make it optional there; it makes it
  absent from one mode that never had a second process to share it with.
