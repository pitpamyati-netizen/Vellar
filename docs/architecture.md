# Architecture

## Layers

Hexagonal, three layers, one direction of dependency:

```
presentation  ->  application  ->  domain
        \             |              ^
         \            v              |
          `------> infrastructure ---'   (implements domain ports)
```

| Layer | Package | May import |
| --- | --- | --- |
| Domain | `mmorpg.domain` | stdlib only |
| Application | `mmorpg.application` | domain, stdlib |
| Infrastructure | `mmorpg.infrastructure` | domain, application, asyncpg, redis, stdlib |
| Presentation | `mmorpg.presentation` | application, domain, aiogram, stdlib |

**The domain is synchronous and side-effect free.** No `async def`, no I/O, no
imports of `aiogram`, `asyncpg`, `redis`, `pydantic` or `datetime.now`. Everything
it needs - the current cycle index, random seeds, the clock - is passed in as an
argument. That makes it testable without a database, a bot token or a network.

*Enforced by:* `tests/domain/test_layering.py`, which walks the AST of every module
under `src/mmorpg/domain/` and fails on a forbidden import.

Anything reaching the outside world goes through a port: a `typing.Protocol` in
`mmorpg/domain/ports/`, implemented in `mmorpg/infrastructure/`.

**No business logic in handlers.** A handler does exactly three things: parse the
incoming button text, call an application service, render the resulting screen.

## Packages

```
src/mmorpg/
  domain/
    entities/   Character, Stats, Item, Location, Enemy, Skill
    rules/      damage, progression, economy formulas
    procgen/    deterministic generators
    ports/      repository protocols
  application/
    services/   CreateCharacter, EnterLocation, ResolveCombatTurn
    dto/        boundary data objects
  infrastructure/
    persistence/  asyncpg repositories + explicit SQL, in-memory adapters
    cache/        Redis, in-memory adapter
    content/      TOML -> frozen dataclasses loader
  presentation/
    telegram/
      handlers/     aiogram routers, one per screen family
      flows/        pure state machines: advance(state, message) -> state
      keyboards/    ReplyKeyboardMarkup builders only
      screens/      screen text renderers
      states/       FSM states and the navigation stack
      middlewares/  idempotency, dependency injection, error handling
      routing.py    button text and typed commands -> intents
  main.py       composition root
```

## Flows: why the interface is testable

Each screen family has a **flow**: a pure function
`advance(content, state, message) -> state`, with `render(content, state) -> Screen`
beside it. No I/O, no aiogram, no clock - the world cycle and the player's goods
arrive as arguments.

That is what makes the whole interface testable without a bot token: the tests
press buttons by sending their exact text and assert on the resulting state and
screen. A handler is then only four lines: load state, call `advance`, render,
send one message.

Anything that writes to a database is *recorded as an intent* on the state - one
`PendingWrite` holding the new character, the new settings and the changes to the
bag - and executed by the handler, so the flow stays pure and every write in the
game goes through a single function (`handlers/play.py::_apply`).

A fight is the one thing the play flow hands over rather than resolves: pressing
"Вступить в бой" sets `PlayState.fight`, and the fight handler generates the
opponents from the node seed, keeps the fight in FSM data, and pays out the
result (`handlers/combat.py`). It is registered **before** the play router,
because the play router filters on the whole `Play` state group.

## Storage split

| Where | What |
| --- | --- |
| PostgreSQL | users, characters (raw stats, level, experience, gold, vault gold), inventory, equipment, skill loadout with ranks and edges, chosen traits, city, quest and craft progress, accessibility settings, world seed, trades (pending escrow and the settled journal), privacy (profile visibility on the user row, black lists in `blocks`) |
| Redis (with TTL) | FSM state, current screen, active combat, location deltas for the current cycle, update deduplication, shop assortment cache |
| Nowhere - recomputed | location layout, nodes, enemies, loot, total character stats, shop assortment (all pure functions of seed and cycle) |

Redis keys:

| Key | Value | TTL |
| --- | --- | --- |
| `loc:{city}:{slot}:{cycle}:{user}` | cleared-node bitmask | until end of cycle |
| `upd:{update_id}` | idempotency marker | 300 s |
| `shop:{city}:{cycle}` | rolled assortment | until end of cycle |
| `fsm:*` | aiogram `RedisStorage` | 7 days |

`APP_ENV=local` substitutes in-memory implementations of every port, so the bot
runs with no external services at all. See `docs/adr/0005-in-memory-adapters.md`.

A pending offer is the one short-lived thing that is *not* in Redis. Publishing
one takes the author's item or gold into escrow, so the row now holds real value:
a store that expires by itself would swallow it. `trades` is closed by a single
`UPDATE ... WHERE status = 'pending' RETURNING`, which is what makes two taps on
"Принять" settle exactly once, and a partial unique index on `(scope, number)`
keeps two live offers from sharing a number. Stakes of offers nobody answered are
returned by a sweep that runs at the start of the next group command - there is
no background timer.

## Latency budget

Target: p95 update handling under 100 ms, p99 under 250 ms.

- Nothing blocks the event loop. `time.sleep`, synchronous HTTP clients and runtime
  file I/O are forbidden; `asyncio` debug mode plus `loop.slow_callback_duration`
  logs any violation. That detector costs a timestamp per callback, so it is a
  switch (`SLOW_CALLBACK_DETECTOR`) that the Docker stack turns off - see
  `docs/deployment.md`.
- All static content is loaded once at startup into `@dataclass(frozen=True,
  slots=True)` objects held in memory and indexed by dict for O(1) access.
- Keyboards are cached with `functools.lru_cache` keyed by screen plus state, so
  markup is not rebuilt per update.
- Connection pools (asyncpg min 5 / max 20, Redis pool) are created at startup, not
  per request.
- Heavy work runs in background tasks via `asyncio.TaskGroup`; the player gets an
  answer immediately.
- One player action produces exactly one new message.
- An idempotency middleware drops duplicate `update_id` values, so a redelivered
  update never applies an effect twice.

## Capacity

Sized for a hundred players online: about ten updates a second, with bursts several
times that. Two limits keep a burst from becoming a queue everyone waits in.

- `UPDATE_CONCURRENCY_LIMIT` caps updates handled at the same time. Excess updates
  wait at the door rather than all contending for the connection pool at once, so
  the players already being served keep their latency.
- `POSTGRES_POOL_MAX` caps concurrent queries. Raising the first without the second
  only moves the queue.

Telegram's own rate limit - roughly 30 messages a second per bot - binds before
this stack does. `docs/deployment.md` has the full sizing argument and what to
change first when the player count grows.

## Runtime

| Mode | Transport | Storage |
| --- | --- | --- |
| `APP_ENV=local` | long polling | in-memory |
| `APP_ENV=dev` | long polling | PostgreSQL + Redis |
| `APP_ENV=prod` | aiohttp webhook | PostgreSQL + Redis |

The event loop is the stdlib `asyncio.Runner`; uvloop is not used
(`docs/adr/0004-no-uvloop.md`).

Polling runs in exactly one process: Telegram gives `getUpdates` to a single
consumer, so a second replica would only lose races with the first. Webhook mode is
what allows more than one instance.

The loop touches a heartbeat file every ten seconds (`mmorpg.health`). A process
that dies is caught by the restart policy; a process whose loop is wedged is caught
only by that file going stale, which is what the container healthcheck reads.
Shutdown is graceful on `SIGTERM` in both transports.

## Character maths

Nothing derived is ever stored. The character record holds raw values only -
allocated stat points, level, experience, chosen traits, the skill loadout and
equipment - and `mmorpg.domain.rules.stats` rebuilds the rest on demand:

```
total = base + race + class + allocated + traits + equipped passives + equipment + active effects
```

Percentages from every source are summed first and applied once, so ordering
cannot change the result. Active effects are keyed by id in an `EffectStack`:
re-applying an effect refreshes its duration and never adds its modifiers twice
(`tests/domain/test_effects.py`).

Derived values: max health, max resource, armour, accuracy, dodge, crit chance,
crit damage, initiative, resource regeneration and health regeneration. Dodge and
crit chance are capped at 75 percent so no build turns combat into a coin that
never lands.

The experience curve is precomputed once at import for levels 1-300, so finding a
level from an experience total is a binary search, not a loop.
