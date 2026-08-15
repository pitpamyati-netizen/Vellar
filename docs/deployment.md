# Deployment

How the bot is actually run: locally while working on it, and as a stack that a
hundred players can be left on.

## Two ways to run

| | `Start.bat local` | `Start.bat` |
| --- | --- | --- |
| Processes | one, on the host | PostgreSQL, Redis, migrations, bot |
| Storage | in memory | PostgreSQL + Redis, on disk |
| Survives a restart | no | yes |
| Restarted automatically | no | yes |
| Needs | `uv`, a bot token | Docker Desktop, a bot token |
| For | trying a change | players |

`local` forgets every character the moment the process exits. It is a development
convenience (`docs/adr/0005-in-memory-adapters.md`), never somewhere to leave
players.

## The Docker stack

```
docker compose up -d          # or Start.bat
docker compose logs -f bot
docker compose down           # or stop.bat
```

## Updating a game that is running

`Update.bat` replaces the bot without stopping the world: dump into `backups\`,
tag the serving image `previous` (before the build moves `latest` and the old
image is collected), build, `alembic upgrade head` on its own, then
`up -d --no-deps --wait bot`. Each step fails where it can be understood: a build
error leaves the old bot serving, a bad migration leaves both alone.

PostgreSQL and Redis are never touched, so characters, bags and the fight a
player is mid-way through survive the swap - the new process reads them back on
its first update. The cost is a few seconds in which a press gets no answer;
Telegram holds it and the new process replies.

`Update.bat rollback` retags `previous` as `latest` and swaps back. A migration
that already ran stays applied: the schema only moves forward, and going back
past that means restoring a dump.

Which build is running is not a matter of memory. Both scripts stamp the image
with the commit (`-dirty` when anything is uncommitted or untracked), the bot
logs it as `build ref=...`, and they read it back out of the container
afterwards. `Start.bat status` prints tree and container side by side.

Four services:

- **postgres** - durable state. Tuned in `docker-compose.yml`, published on
  loopback only.
- **redis** - FSM state, active fights, location deltas. Persisted with an append-only
  file, because losing it drops players out of a fight mid-turn.
- **migrate** - `alembic upgrade head`, runs to completion before the bot starts and
  is a no-op on an up-to-date database.
- **bot** - long polling. One replica, always.

The bot reads `.env`, except for `APP_ENV`, `POSTGRES_DSN` and `REDIS_DSN`, which
compose overrides so the container can never quietly fall back to the in-memory
adapters.

### Why polling, and why one replica

Telegram hands `getUpdates` to a single consumer. A second replica would spend its
life losing races with the first, so the bot never scales out horizontally in this
mode. That is not a limit worth engineering around at this size: one process
handles a hundred players with the latency budget intact (below).

Webhook mode is the answer when it stops being enough, since several instances can
sit behind one URL. It needs a public HTTPS endpoint:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

with `WEBHOOK_BASE_URL` and `WEBHOOK_SECRET` set in `.env`, and a reverse proxy
holding the certificate in front of port 8080.

## Sizing for a hundred players

The numbers in `docker-compose.yml` and `.env.example` come from this chain:

| Assumption | Value |
| --- | --- |
| Players online | 100 |
| Actions per player per minute | ~6, a button press every 10 seconds |
| Updates per second | ~10, with bursts several times that |
| Work per update | one or two indexed row lookups, then pure rendering |
| Budget per update | p95 under 100 ms (`architecture.md`) |

Ten updates a second against a 100 ms budget needs about one update in flight at a
time on average. The settings are sized for the bursts, not the average:

- **`UPDATE_CONCURRENCY_LIMIT=100`** caps updates handled at once. Without it a
  burst queues unbounded work, every task waits on the connection pool, and the
  latency for everyone degrades together. With it the excess waits at the door
  instead, and the players already being served stay fast.
- **`POSTGRES_POOL_MAX=20`** is the ceiling on concurrent queries. Queries here are
  primary-key lookups; twenty of them at once is far more than ten updates a second
  can produce.
- **`max_connections=100`** on PostgreSQL leaves the pool five times its headroom
  for migrations, `psql` and backups.
- **Redis `maxmemory 512mb`** with `volatile-lru`. FSM keys carry no TTL, so this
  policy can never evict a player's position - only the cache entries the game
  marked as expendable.
- **Memory limits** of 512 MB for the bot and 1 GB for PostgreSQL. Measured idle
  use is well under those; the gap is burst headroom, and the limit is there so a
  leak takes one container down instead of the host.

### Where the ceiling actually is

Telegram rate limits before this stack does: roughly 30 messages a second per bot.
At a hundred players that is comfortable, at a thousand it is the binding
constraint, and no amount of local capacity changes it.

The next things to change, in order:

1. Raise `UPDATE_CONCURRENCY_LIMIT` and `POSTGRES_POOL_MAX` together - the first
   without the second only moves the queue.
2. Switch to webhook mode, which allows more than one bot instance.
3. Give PostgreSQL more `shared_buffers` and a real disk.

## Staying up

Two different failures, two different mechanisms.

**The process dies.** `restart: unless-stopped` brings it back. State is in
PostgreSQL and Redis, so players lose nothing but the seconds it takes to restart.

**The process lives but stops working** - a wedged event loop, a socket that never
times out. Nothing outside the process can see this: the container is running,
the port is open, and every player waits in silence. So the loop proves it is alive
by touching a file every ten seconds (`src/mmorpg/health.py`), the image's
`HEALTHCHECK` reads the file's age (`scripts/healthcheck.py`), and three missed
beats mark the container unhealthy.

Shutdown is graceful in both transports: `docker stop` sends `SIGTERM`, aiogram
drains the updates in flight during polling, the webhook runner stops on the same
signal, and the exit stack closes both pools before the process leaves.

Logs are capped at 5 files of 10 MB per service. Uncapped JSON logs fill the disk,
and a full disk takes PostgreSQL down with it.

## Operating it

```bash
docker compose ps                       # what is running and whether it is healthy
docker compose logs -f bot              # follow the bot
docker compose restart bot              # restart just the bot
docker inspect -f "{{.State.Health.Status}}" vellar-bot
```

Back up the database before anything irreversible:

```bash
docker compose exec postgres pg_dump -U vellar vellar > backup.sql
```

A plain `stop.bat` saves first: `redis-cli SAVE` writes the temporary state -
screens, fights, offers - on top of the append-only log, and `pg_dump` puts
everything permanent in `backups\`, newest twenty kept. Neither is what makes a
stop safe (no volume is touched), but a dump is what turns "the world is still
there" into something you can carry elsewhere.

`stop.bat purge` and `docker compose down --volumes` delete every character in the
world. There is no undo, which is why the batch file asks first.

## Before exposing this beyond your own machine

- Change `POSTGRES_PASSWORD` in `.env`. The default is `vellar`, which is fine
  while PostgreSQL is bound to loopback and not otherwise.
- Keep `BOT_TOKEN` out of the repository. `.env` is gitignored; if a token ever
  reaches a commit or a chat log, revoke it with `@BotFather` and issue a new one.
- Set `WEBHOOK_SECRET` to a long random string. It is what stops anyone who learns
  your webhook URL from posting updates to it.
- Leave `SLOW_CALLBACK_DETECTOR=false` in the stack. It needs asyncio debug mode,
  which timestamps every callback - useful while developing, wasteful under load.
- Keep `ADMIN_IDS` short and true. Every id on that list hands itself gold and
  levels from inside the game, and hands the keeper right to anybody else
  (`docs/keeper.md`); an id left there by accident is a keeper nobody remembers
  appointing, with keepers of its own.
