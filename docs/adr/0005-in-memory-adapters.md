# ADR 0005 - In-memory adapters for local development and tests

Status: accepted (2026-08-12)

## Context

Running the bot required PostgreSQL and Redis, which means a working Docker daemon
before a single message can be exchanged. That is a poor first-run experience and it
also makes the fast test suite depend on external services.

## Decision

Every port in `mmorpg/domain/ports/` gets two implementations:

- `infrastructure/persistence/postgres/` and `infrastructure/cache/redis_*` - the
  production path, used when `APP_ENV` is `dev` or `prod`;
- `infrastructure/persistence/memory/` and `infrastructure/cache/memory_*` - dicts
  behind the same protocol, used when `APP_ENV=local` and in the test suite.

aiogram's FSM storage follows the same rule: `RedisStorage` for dev and prod,
`MemoryStorage` for local.

## Consequences

- `uv sync && uv run python -m mmorpg.main` plays end to end with only a bot token.
- The port protocols are exercised by two implementations, so leaking a
  PostgreSQL-specific concept into a port fails fast.
- In-memory state is lost on restart; it is a development convenience, never a
  deployment target. The startup log warns about this explicitly.
- Repository tests that assert real SQL behaviour are marked `integration` and are
  skipped unless PostgreSQL and Redis are reachable.
