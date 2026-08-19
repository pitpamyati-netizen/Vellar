# Deployment

How the bot is actually run: locally while working on it, and as a stack that a
hundred players can be left on.

## Three ways to run

| | `Start.bat local` | `Start.bat solo` | `Start.bat` |
| --- | --- | --- | --- |
| Processes | one, on the host | one, on the host | PostgreSQL, Redis, migrations, bot |
| Storage | in memory | PostgreSQL on this machine | PostgreSQL + Redis, on disk |
| Characters survive a restart | no | yes | yes |
| Screens and fights survive it | no | no | yes |
| Restarted automatically | no | no | yes |
| Needs | `uv`, a bot token | `uv`, PostgreSQL, a bot token | Docker Desktop, a bot token |
| For | trying a change | one machine, no Docker | players |

`local` forgets every character the moment the process exits. It is a development
convenience (`docs/adr/0005-in-memory-adapters.md`), never somewhere to leave
players.

`solo` is the middle: the world is in a real database and the session is not
(`docs/adr/0010-a-machine-without-containers.md`). It exists because Docker
Desktop is several gigabytes to keep a world that one installer already keeps.

## Solo: PostgreSQL without Docker

Once, when setting the machine up:

1. Install PostgreSQL from <https://www.postgresql.org/download/windows/>, taking
   the defaults. Remember the superuser password it asks for.
2. `Start.bat setup-db` - creates the role `vellar` with the password from
   `POSTGRES_PASSWORD` and a database of the same name owned by it. It asks for
   that superuser password, is idempotent, and touches nothing else.

Then, every time:

```
Start.bat solo
```

which brings the schema up to date (`alembic upgrade head`) and starts the bot in
this window. Ctrl+C stops it. `POSTGRES_DSN` in `.env` is what both of them
connect to, so pointing it at a database you made yourself works as well.

What a restart costs is the session, not the world: everyone is put back in the
main menu unhurt, and a fight in progress ends. That is done rather than hoped
for: the keyboard in a player's chat outlives the process, so the first press
after a restart arrives with no screen behind it and is answered by the main menu
plus one sentence saying the previous screen is gone (`handlers/creation.resume`).
Before that catch-all existed such a press reached no handler at all, and the
player got silence. Characters, gold, bags, contracts
and keeper edits are in PostgreSQL and are untouched. Updating is the same few
seconds of silence as an update to the stack - Ctrl+C, then `Start.bat solo`.

`stop.bat` still works without Docker: it cannot stop a process in another window,
but it takes the same `pg_dump` into `backups\`. The dump is interchangeable with
the stack's, so a solo world can be carried into Docker later and back.

### Carrying a world out of Docker

A stack that has already been played on holds the world in a Docker volume, which
goes when Docker does. Move it first, while the stack still runs:

```
stop.bat                             # dumps into backups\
Start.bat setup-db                   # once, on the new PostgreSQL
psql "postgresql://vellar:vellar@localhost:5432/vellar" -f backups\vellar-<stamp>.sql
Start.bat solo
```

The dump is taken with `--clean --if-exists`, so it restores over an empty
database and over an existing one alike. Going back the other way is the same
file into the container's `psql`.

If PostgreSQL is not answering, its Windows service is usually stopped:
`net start postgresql-x64-17` from an administrator prompt, or set it to start
with Windows in `services.msc`.

## The Docker stack

```
docker compose up -d          # or Start.bat
docker compose logs -f bot
docker compose down           # or stop.bat
```

## Updating a game that is running

In solo mode: Ctrl+C in the bot's window, then `Start.bat` again. It brings the
schema up to date before it starts, and PostgreSQL is never stopped, so
characters, bags and contracts carry straight over. The session does not - the
same restart everyone else pays for. Take a dump first if the change touches the
schema: `stop.bat` writes one into `backups\` without stopping anything of yours.

In the stack: `Start.bat docker` on a stack that is already up rebuilds the image
and lets compose swap the bot; `migrate` runs to completion first, and PostgreSQL
and Redis are left alone, so the fight a player is mid-way through survives the
swap. A build that fails stops before anything is replaced and the old bot keeps
serving. The cost is a few seconds in which a press gets no answer; Telegram
holds it and the new process replies.

Going back is `git checkout` of the commit that worked and starting again - there
is no saved previous image. A migration that already ran stays applied either
way: the schema only moves forward, and going back past that means restoring a
dump.

Which build is running is not a matter of memory. `Start.bat` stamps it with the
commit (`-dirty` when anything is uncommitted or untracked) and the bot logs it
as `build ref=...`; `Start.bat status` prints tree and container side by side.

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

### What a hundred players actually cost

Measured rather than assumed, with `scripts/loadtest.py` against the real
repositories (no Telegram in the path):

| Run | p95 | Slowest |
| --- | --- | --- |
| 100 players, a press every ~3 s (`--pause 3`) | 5 ms | 78 ms |
| 100 players, all pressing in the same instant | 500 ms | 504 ms |

The first line is the game as it will be played and it sits well inside the 100 ms
budget. The second is the worst case that exists - a hundred simultaneous presses
against a pool of twenty connections - and it is a queue, not a failure: nothing
errored, everyone was served. If real traffic ever looks like the second line,
`POSTGRES_POOL_MAX` is the number to raise, and `UPDATE_CONCURRENCY_LIMIT` with it.

### Where the ceiling actually is

Telegram rate limits before this stack does: roughly 30 messages a second per bot.
At a hundred players that is comfortable, at a thousand it is the binding
constraint, and no amount of local capacity changes it. The queue that keeps the
bot inside that count is `middlewares/sending.py`.

The next things to change, in order:

1. Raise `UPDATE_CONCURRENCY_LIMIT` and `POSTGRES_POOL_MAX` together - the first
   without the second only moves the queue.
2. Switch to webhook mode, which allows more than one bot instance.
3. Give PostgreSQL more `shared_buffers` and a real disk.

## Staying up

Three different failures, three different mechanisms.

**The process dies.** `restart: unless-stopped` brings it back. State is in
PostgreSQL and Redis, so players lose nothing but the seconds it takes to restart.

**The process lives but stops working** - a wedged event loop, a socket that never
times out. Nothing outside the process can see this: the container is running,
the port is open, and every player waits in silence. So the loop proves it is alive
by touching a file every ten seconds (`src/mmorpg/health.py`), the image's
`HEALTHCHECK` reads the file's age (`scripts/healthcheck.py`), and three missed
beats mark the container unhealthy.

**A link breaks and the process lives.** PostgreSQL restarts, Redis is restarted
by an update, the network hiccups. The connection is replaced by the pool, and the
call that was in the air is made again: reads and Redis commands always, Telegram
requests that never left, and writes only while it is certain nothing was sent
(`docs/adr/0009-repeating-a-lost-query.md`). Nothing has to be restarted by hand.
In the log it reads `postgres_repeating` / `postgres_recovered` and
`telegram_repeating` / `telegram_recovered`; `postgres_call_lost` is the line that
means a player did lose an action. Startup is patient in the same way -
`waiting_for_service` is the bot waiting for a database that Docker started second.

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
there" into something you can carry elsewhere. With no stack running it takes the
same dump through the machine's own `pg_dump`, which is how a solo world is
backed up.

`stop.bat purge` and `docker compose down --volumes` delete every character in the
world. There is no undo, which is why the batch file asks first.

### Backups on a schedule, and proof that they restore

A world only stopped once a month is a world backed up once a month, so the copy
is taken on a clock rather than on a stop:

```bash
pwsh -File scripts/backup.ps1 -Schedule 04:00
```

That registers a Windows scheduled task ("Vellar backup"); `-Unschedule` removes
it. Each run dumps, prunes to the newest twenty (`-Keep N`), and then **restores
the dump into a database of its own**, counts the characters in it, compares that
with the living database, and drops it again. A file nobody ever unpacked is not
a backup, and a run that cannot unpack it exits non-zero and says so. The role
needs `CREATEDB` for that, which `scripts/setup-db.sql` now grants; an older
installation is one statement behind:

```bash
psql -U postgres -c "ALTER ROLE vellar CREATEDB;"
```

### Being told the game stopped

The heartbeat is read by Docker's probe in the stack. Without containers nothing
reads it: the window is open, the process looks alive, and the players are the
alarm. `scripts/watchdog.py` is that reader - it lives outside the game on
purpose, checks the age of the beat, writes to every id in `ADMIN_IDS` when it has
gone stale, and exits 1 so any scheduler can act on it. Hang it next to the
backup, every five minutes:

```bash
pwsh -Command "Register-ScheduledTask -TaskName 'Vellar watchdog' -Action (New-ScheduledTaskAction -Execute 'uv' -Argument 'run python scripts/watchdog.py' -WorkingDirectory (Get-Location)) -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)) -Force"
```

### The journal on disk

A solo run writes two files under `logs/` beside stdout, and keeps writing them
across restarts (`src/mmorpg/logging.py`):

| File | Holds | Kept |
| --- | --- | --- |
| `logs/vellar.log` | every served update: who pressed what, the outcome, the milliseconds | `LOG_RETENTION_DAYS`, 7 by default |
| `logs/important.log` | warnings, errors and tracebacks, every `gold_flow` line, every action that failed or was turned away, every start and stop | `LOG_IMPORTANT_RETENTION_DAYS`, `0` = forever |

Both roll over at midnight; the cleanup deletes rollovers past their term and
runs by itself, once at startup and again on every rollover. It only ever touches
the file it is allowed to, which is the point of the split: a week of chatter is
worth deleting, a failure is worth reading a year later.

```bash
Get-Content logsellar.log -Tail 50 -Wait     # follow the game
Select-String result=failed logs\important.log  # what broke, ever
```

The Docker stack sets `LOG_DIR=""` instead: there the log belongs to the daemon
collecting stdout, and the bot owns nothing under `/app`.

### What the numbers say while it runs

One line a minute, in the same log as everything else:

```text
metrics updates=412 failures=0 p50=0.01 p95=0.05 slowest=0.22
```

`p95` above `SLOW_CALLBACK_SECONDS` is the game missing its promise; `failures`
above zero is a player who got an apology instead of a screen. `METRICS_SECONDS`
changes how often it is written. Gold is counted separately and read afterwards:

```bash
uv run python scripts/economy.py logs/important.log --hours 24
```

That sums every `gold_flow` line by kind - what the world paid out, what the
cities took back, what the duty removed - which is the whole reason those lines
exist (`src/mmorpg/economy_log.py`).

### The rate Telegram counts

Telegram accepts about thirty sends a second from one bot, for the bot as a
whole. The queue that keeps the game inside that count sits below the retry
middleware (`middlewares/sending.py`); `telegram_send_queued` in the log means it
is actually holding messages back, which at a hundred players it should not be.
`TELEGRAM_SENDS_PER_SECOND` is the knob, and lowering it is safer than raising it.

## Before exposing this beyond your own machine

- Change `POSTGRES_PASSWORD` in `.env`. The default is `vellar`, which is fine
  while PostgreSQL is bound to loopback and not otherwise.
- Keep `BOT_TOKEN` out of the repository. `.env` is gitignored; if a token ever
  reaches a commit or a chat log, revoke it with `@BotFather` and issue a new one.
- Set `WEBHOOK_SECRET` to a long random string. It is what stops anyone who learns
  your webhook URL from posting updates to it.
- Leave `SLOW_CALLBACK_DETECTOR` alone. Unset, it is on for `APP_ENV=local` and
  off everywhere players are, because that is where it would have been forgotten;
  it needs asyncio debug mode, which timestamps every callback - useful while
  developing, wasteful under load.
- Keep `ADMIN_IDS` short and true. Every id on that list hands itself gold and
  levels from inside the game, and hands the keeper right to anybody else
  (`docs/keeper.md`); an id left there by accident is a keeper nobody remembers
  appointing, with keepers of its own.
