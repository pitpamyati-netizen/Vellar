# Vellar

An accessible text MMORPG for Telegram. The game is designed **screen-reader first**:
reply keyboards only, no inline buttons, no message edits, every message readable on
its own. See [docs/accessibility.md](docs/accessibility.md) - those rules are a
specification, not a preference.

- 15 cities x 5 locations, character levels 1-300
- Procedurally generated locations, regenerated every 6-hour world cycle
- 16 races, 8 classes, 60+ traits, all defined in TOML under [`content/`](content/)
- Fixed skill panel: 6 active + 3 passive + 1 racial slot, at every level

## Run it in five minutes

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
git clone <this repo> && cd Vellar
uv sync
```

Copy the environment template and put your bot token in it (get one from
[@BotFather](https://t.me/BotFather)):

```bash
cp .env.example .env
```

`APP_ENV=local` is the default. In this mode the bot uses in-memory adapters, so
**no PostgreSQL and no Redis are required** - you can play immediately:

```bash
uv run python -m mmorpg.main
```

With `APP_ENV=dev` the bot long-polls against real PostgreSQL and Redis. Start them
with Docker:

```bash
docker compose up -d postgres redis
```

`APP_ENV=prod` switches to webhook mode served by aiohttp. The full stack, bot
included, runs with:

```bash
docker compose up -d
```

## Quality gate

```bash
pwsh -File scripts/ci.ps1
```

(or `./scripts/ci.sh` on Linux and macOS). This runs `ruff check`, `ruff format
--check`, `mypy --strict` and `pytest` with coverage. The `domain/` package is held
at 90% coverage or better.

## Layout

```
src/mmorpg/
  domain/          pure game logic - no aiogram, asyncpg or redis imports
  application/     use cases orchestrating the domain
  infrastructure/  asyncpg repositories, Redis cache, TOML content loader
  presentation/    aiogram routers, reply keyboards, screen renderers, FSM
content/           world, races, classes, traits, skills, items (TOML)
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
| Accessibility settings: emoji, verbose descriptions, repeat | |

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Layers, dependency rule, flows, data schema, latency budget |
| [docs/accessibility.md](docs/accessibility.md) | Screen-reader rules and the review checklist |
| [docs/procgen.md](docs/procgen.md) | Seeds, cycles, generation invariants |
| [docs/content-guide.md](docs/content-guide.md) | Adding a race, class, trait or city without touching code |
| [docs/skills.md](docs/skills.md) | Skill panel, ranks, edges, anti-bloat rules |
| [docs/release-checklist.md](docs/release-checklist.md) | What to verify before shipping |
| [docs/adr/](docs/adr/) | One architecture decision per file |

## Content at a glance

16 races · 8 classes · 64 traits · 128 skills (112 class + 16 racial, each with two
rank-3 edges) · 24 enemy archetypes · 15 cities × 5 locations covering levels 1-300.
All of it lives in [`content/`](content/) as TOML and is validated at startup - the
bot refuses to boot on broken content and reports every problem at once.
