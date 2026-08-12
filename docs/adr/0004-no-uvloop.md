# ADR 0004 - Standard asyncio.Runner, no uvloop

Status: accepted (2026-08-12)

## Context

The specification allows uvloop only if it is built for Python 3.14. Checked on
2026-08-12:

- uvloop 0.22.1 declares support up to Python 3.13; there is no 3.14 classifier and
  no 3.14 wheel.
- uvloop has never supported Windows, and the project is developed on Windows.

The workload is also not one uvloop helps much with: handlers are dominated by
PostgreSQL and Redis round trips and Telegram API calls, not by event loop
scheduling overhead.

## Decision

Use the stdlib `asyncio.Runner` on every platform. Do not add uvloop as a
dependency, optional or otherwise.

## Consequences

- One event loop implementation across development, CI and production; no
  platform-specific behaviour differences to debug.
- Revisit when a 3.14 uvloop wheel exists **and** profiling shows loop overhead
  inside the latency budget. Until then the latency work is in pooling, caching and
  keeping the loop unblocked (see `docs/architecture.md`, "Latency budget").
