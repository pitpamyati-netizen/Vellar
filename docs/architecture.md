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
it needs - what is left in a location's nodes, random seeds, the clock - is
passed in as an
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
beside it. No I/O, no aiogram, no clock - what stands in the nodes and the goods
arrive as arguments.

That is what makes the whole interface testable without a bot token: the tests
press buttons by sending their exact text and assert on the resulting state and
screen. A handler is then only four lines: load state, call `advance`, render,
send one message.

Anything that writes to a database is *recorded as an intent* on the state - one
`PendingWrite` holding the new character, the new settings and the changes to the
bag - and executed by the handler, so the flow stays pure and every write in the
game goes through a single function (`handlers/play.py::_apply`). The keeper panel
uses the same intent for the things only it can do - an edit to the world, another
player's character, a sweep - and those are carried out beside it, in `_serve`.

## Content that can change while the game runs

`GameContent` is still read once from `content/` and still immutable. What changed
is that handlers no longer receive it as a value captured at startup: they receive
whatever `ContentRegistry.current` holds right now
(`application/services/content.py`). The registry keeps two builds - the one parsed
from TOML, which never changes, and that same one with the keeper's edits applied
on top (`domain/rules/overlay.py`). An edit is therefore visible on the next press
without a restart, and dropping it rebuilds the world from the untouched original
rather than undoing anything. See `docs/keeper.md`.

A fight is the one thing the play flow hands over rather than resolves: pressing
"Вступить в бой" sets `PlayState.fight`, and the fight handler builds the sides,
generates the opponents from the node seed and pays out the result
(`handlers/combat.py`). It is registered **before** the play router, because the
play router filters on the whole `Play` state group.

The fight itself is **one record shared by everybody in it**
(`application/services/battle.py`), not a copy in each player's FSM data: a duel
between two people cannot have two truths about whose health is whose
(ADR 0021). FSM data holds only the battle's id; the record holds the sides, the
queue and every fighter. The same record carries "this character is busy", which
is what keeps anyone from being pulled into a second fight.

One engine runs all of it (`domain/rules/combat.py`): a fight is two sides and a
queue ordered by initiative, and the difference between a wolf, a live player and
a character the engine plays for (the arena's opponent) is two flags on
`Combatant`. One call to `act` resolves the turn of whoever is next and then
plays out everyone the engine speaks for, stopping when the queue reaches a live
player. That player is waited on indefinitely - there are no timers anywhere -
and the way out of a fight nobody answers is the «Сдаться» button, which hands
the fight over.

## Storage split

| Where | What |
| --- | --- |
| PostgreSQL | users, characters (raw stats, level, experience, gold, vault gold, what the arena holds), inventory, equipment, skill loadout with ranks and edges, chosen traits, city, quest and craft progress, accessibility settings, world seed, trades (pending escrow and the settled journal), privacy (profile visibility on the user row, black lists in `blocks`) |
| Redis | FSM state (no TTL, deliberately), and with a TTL: current screen, the active fight and who is in it, parties and their calls, the shared state of every location and who is standing in it, update deduplication, shop assortment cache |
| Nowhere - recomputed | location layout, nodes, enemies, loot, total character stats, shop assortment (all pure functions of seed, wave and rotation) |

Redis keys:

| Key | Value | TTL |
| --- | --- | --- |
| `loc:{city}:{slot}` | what is left of each node's wave | a week |
| `loc:{city}:{slot}:who` | who stands on which node | ten minutes |
| `upd:{update_id}` | idempotency marker | 300 s |
| `shop:{city}:{rotation}` | rolled assortment | until the shelf turns over |
| `battle:{id}` | one fight, shared by every participant | an hour |
| `battle-of:{character}` | which fight this character stands in | an hour |
| `party:{leader}` / `party-of:{character}` | who walks together | two hours |
| `party-call:{character}` | an unanswered call into a party | five minutes |
| `fsm:*` | aiogram `RedisStorage` | none - see below |

The FSM keys are the exception to "every key expires", and the eviction policy is
why: Redis runs `volatile-lru`, which may only evict keys that carry a TTL. A
player's position is what must never be forgotten under memory pressure, so it is
stored without one and `RedisStorage` is left at its defaults.

`APP_ENV=local` substitutes in-memory implementations of every port, so the bot
runs with no external services at all. See `docs/adr/0005-in-memory-adapters.md`.
`APP_ENV=solo` substitutes only the right-hand column: PostgreSQL keeps the
world, and everything with a TTL above is held by the one process serving it,
which is one service fewer to install and a session lost on restart
(`docs/adr/0010-a-machine-without-containers.md`).

A pending offer is the one short-lived thing that is *not* in Redis. Publishing
one takes the author's item or gold into escrow, so the row now holds real value:
a store that expires by itself would swallow it. `trades` is closed by a single
`UPDATE ... WHERE status = 'pending' RETURNING`, which is what makes two taps on
"Принять" settle exactly once, and a partial unique index on `(scope, number)`
keeps two live offers from sharing a number. Stakes of offers nobody answered are
returned by a sweep that runs at the start of the next group command and once
when the game starts - there is no background timer. The sweep is not limited to
the group it was triggered from: the number of an offer belongs to a group, the
five minutes it lives do not, and a group that fell silent must not hold the item
of somebody who is playing elsewhere.

Gold moves the same way, and for the same reason. `save` writes back a character
that was read several `await`s ago, so a purse checked in one step and written in
another can swallow whatever its owner did in between - and the owner of a purse
in a group trade is a player who may be buying a bed in their private chat at
that exact moment. Every purse the group touches moves by `spend_gold` (one
conditional `UPDATE ... WHERE gold >= $2 RETURNING`) or `grant_gold` (one
increment). Every movement of gold that is not one player handing another a coin
also writes a `gold_flow` line (`mmorpg.economy_log`): the duty, the stake of the
arena and what a fight pays are all numbers to be corrected against a day of
real play, and a number nobody can measure is only ever re-guessed.

## Latency budget

Target: p95 update handling under 100 ms, p99 under 250 ms.

- Nothing blocks the event loop. `time.sleep`, synchronous HTTP clients and runtime
  file I/O are forbidden; `asyncio` debug mode plus `loop.slow_callback_duration`
  logs any violation. That detector costs a timestamp per callback, so it is a
  switch (`SLOW_CALLBACK_DETECTOR`) that is off by default wherever players are
  connected - see `docs/deployment.md`.
- Whether the budget is actually met is written down rather than assumed: one
  `metrics` line a minute carries how many updates were served, how many failed,
  and the median and 95th percentile of how long they took (`mmorpg.metrics`).
  The percentiles are bucket edges, not exact numbers - a hundred players for a
  day is millions of samples, and the question asked of them is always "which
  bucket".
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
- Outgoing messages pass a queue holding the bot inside Telegram's count of about
  thirty sends a second (`middlewares/sending.py`). A few milliseconds of waiting
  is cheaper than a `429`, which for a player listening to a screen reader is an
  answer that never arrived.

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

## A link that breaks while the game is running

PostgreSQL, Redis and Telegram all break the same way: the connection is replaced
in a moment, and the call that was in the air when it dropped is lost. The pools
do the first half by themselves; the second half is `mmorpg/retry.py` plus one
wrapper per link.

| Link | Reconnects | Repeats the call |
| --- | --- | --- |
| PostgreSQL | asyncpg pool | `ReconnectingPool` - reads always, writes only while nothing was sent |
| Redis | redis-py | redis-py, configured in `create_redis_client` |
| Telegram | aiohttp session | `RetryRequestMiddleware` on the bot session |

A write that may already have landed is **never** repeated: PostgreSQL can commit
a statement and lose only the answer on the way back, and running
`UPDATE ... WHERE gold >= $2` again would take the gold twice. The full argument
is `docs/adr/0009-repeating-a-lost-query.md`.

Startup is patient for the same reason: `STARTUP_WAIT_SECONDS` is how long the
bot waits for PostgreSQL, Redis and Telegram to answer before giving up, because
a stack that comes up together does not come up in order. `RECONNECT_ATTEMPTS`
and the two delays govern the repeats under a running game, where a player is
waiting on the other end and a minute of silence is not an answer.

## Runtime

| Mode | Transport | Storage |
| --- | --- | --- |
| `APP_ENV=local` | long polling | in-memory |
| `APP_ENV=solo` | long polling | PostgreSQL, session in-memory |
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

## The journal

Everything is written to stdout, and - unless `LOG_DIR` is empty - to two files
beside it (`src/mmorpg/logging.py`). One line per served update says who did what
and how it ended:

```text
action who=4242 chat=private did=Атака result=ok ms=14
```

`result` is `ok`, `failed`, `duplicate`, `banned` or `ignored`. The outcome is not
decided by the middleware that writes the line: it opens a note on the update
(`middlewares/audit.py`) and whoever cuts the path short - the duplicate filter,
the ban gate, the error boundary - marks it there, so one press is one line
instead of three. In a group only `failed` and `banned` are written down: the bot
is silent on anything not addressed to it, and what players say to each other is
not the business of the game.

`vellar.log` holds all of it and is swept after `LOG_RETENTION_DAYS` days.
`important.log` holds what the sweep must never take - warnings, errors and
tracebacks, every `gold_flow` line, every failed or turned-away action, every
start and stop - and `LOG_IMPORTANT_RETENTION_DAYS=0`, the default, means it is
never deleted. Deciding importance once, at the sink, is what lets the cleanup be
automatic: a sweep that cannot tell chatter from evidence eventually erases the
evidence.

## Character maths

Nothing derived is ever stored. The character record holds raw values only -
allocated stat points, level, experience, chosen traits, the skill loadout and
equipment - and `mmorpg.domain.rules.stats` rebuilds the rest on demand:

```
total = base + race + class + allocated + traits + learned passives + equipment + active effects
```

Percentages from every source are summed first and applied once, so ordering
cannot change the result. Active effects are keyed by id in an `EffectStack`:
re-applying an effect refreshes its duration and never adds its modifiers twice
(`tests/domain/test_effects.py`).

Derived values: max health, max resource, armour, accuracy, dodge, crit chance,
crit damage, initiative, resource regeneration and health regeneration. Dodge is
capped at 75 percent, crit chance at 50 and crit damage at 250, so no build turns
combat into a coin that never lands - uncapped, luck multiplied chance by damage
and hit three times as hard as anything else.

The experience curve is precomputed once at import for levels 1-300, so finding a
level from an experience total is a binary search, not a loop.
