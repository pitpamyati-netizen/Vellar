# Vellar

An accessible text MMORPG for Telegram. The game is designed **screen-reader first**:
reply keyboards only, no inline buttons, no message edits, every message readable on
its own. See [docs/accessibility.md](docs/accessibility.md) - those rules are a
specification, not a preference.

- 15 cities x 5 locations, character levels 1-300
- Procedurally generated locations, regenerated every 6-hour world cycle
- 16 races, 8 classes, 5 crafts, 60+ traits, all defined in TOML under [`content/`](content/)
- Fixed skill panel: 6 active + 3 passive + 1 racial slot, at every level

## Run it

Put your bot token in `.env` first - copy `.env.example` if the file is not there
yet, and get a token from [@BotFather](https://t.me/BotFather).

On Windows, that is the only step:

```
Start.bat
```

This builds the image from the working tree as it stands, starts PostgreSQL,
Redis, the migrations and the bot, waits until the bot reports healthy, and reads
the build stamp back out of the running container so "am I on my latest change"
is answered rather than assumed. The bot keeps running after you close the window.

| | |
| --- | --- |
| `Start.bat` | build this tree and start everything |
| `Update.bat` | apply this tree to a game already running: back up, build, migrate, swap the bot only - PostgreSQL and Redis stay up, so characters and fights in progress are untouched |
| `Update.bat rollback` | put the previous image back |
| `stop.bat` | flush Redis, dump the database to `backups\`, then stop; nothing is deleted |
| `stop.bat purge` | stop and delete every character. Asks first |

`Start.bat local` runs a single in-memory process instead, with no Docker and
nothing saved - good for trying a change, never for players.

Elsewhere, or by hand:

```bash
docker compose up -d && docker compose logs -f bot
```

Without Docker you need [uv](https://docs.astral.sh/uv/) and Python 3.14. With
`APP_ENV=local` the bot uses in-memory adapters, so no PostgreSQL and no Redis are
required and a token is the only prerequisite:

```bash
uv sync && uv run python -m mmorpg.main
```

`docs/deployment.md` covers the whole picture: what the stack is sized for, webhook
mode, and how it stays up.

## Quality gate

```bash
pwsh -File scripts/ci.ps1
```

(or `./scripts/ci.sh` on Linux and macOS). This runs `ruff check`, `ruff format
--check`, `mypy --strict` and `pytest` with coverage. The `domain/` package is held
at 90% coverage or better.

The same gate runs on every commit, and fixes itself where it can:
`.githooks/pre-commit` applies `ruff format` and `ruff check --fix` to the staged
files, stages the result, and only then runs `mypy` and the tests. `Start.bat`
installs it; `pwsh -File scripts/install-hooks.ps1` (or `./scripts/install-hooks.sh`)
does it on its own. `VELLAR_SKIP_TESTS=1` skips the slow part, and
`git commit --no-verify` skips the hook entirely.

## Layout

```
src/mmorpg/
  domain/          pure game logic - no aiogram, asyncpg or redis imports
  application/     use cases orchestrating the domain
  infrastructure/  asyncpg repositories, Redis cache, TOML content loader
  presentation/    aiogram routers, reply keyboards, screen renderers, FSM
content/           world, races, classes, crafts, traits, skills, items (TOML)
docs/              architecture, accessibility, procgen, content guide, ADRs
tests/
```

## What is playable

| Working | Stubbed (a real screen with a working "Назад", never silence) |
| --- | --- |
| Character creation: name, race, class, two traits, free points, confirmation | Dungeons, tavern, mentor, bank |
| Main menu, world list, city hub, five locations, node-by-node movement | Cities 2-15 (they unlock by level; content is generated for all of them) |
| Turn-based combat with the fixed 6+3+1 panel, one to three enemies | Skill loadout editing, PvP, guilds |
| Inventory and city shop on the shared paginated list | |
| Crafts: gathering once a watch, recipes, batch quality | Craft contracts, gathering tied to a biome |
| Accessibility settings: emoji, verbose descriptions, repeat | |

## Documentation

| Document | Contents |
| --- | --- |
| [Claude.md](Claude.md) | File map and the development rules (Russian) |
| [Roadmap.md](Roadmap.md) | The three days to open beta, with status flags (Russian) |
| [Narrative.md](Narrative.md) | World, naming, dialogue and broadcast tone (Russian) |
| [docs/architecture.md](docs/architecture.md) | Layers, dependency rule, flows, data schema, latency budget |
| [docs/accessibility.md](docs/accessibility.md) | Screen-reader rules and the review checklist |
| [docs/procgen.md](docs/procgen.md) | Seeds, cycles, generation invariants |
| [docs/content-guide.md](docs/content-guide.md) | Adding a race, class, craft, trait or city without touching code |
| [docs/skills.md](docs/skills.md) | Skill panel, ranks, edges, anti-bloat rules |
| [docs/crafts.md](docs/crafts.md) | Crafts: ranks, gathering, recipes and batch quality |
| [docs/release-checklist.md](docs/release-checklist.md) | What to verify before shipping |
| [docs/adr/](docs/adr/) | One architecture decision per file |

## Content at a glance

16 races · 8 classes · 64 traits · 128 skills (112 class + 16 racial, each with two
rank-3 edges) · 5 crafts with 9 recipes · 24 enemy archetypes · 15 cities × 5
locations covering levels 1-300.
All of it lives in [`content/`](content/) as TOML and is validated at startup - the
bot refuses to boot on broken content and reports every problem at once.
