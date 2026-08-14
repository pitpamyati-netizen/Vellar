# ADR 0003 - Six-hour world cycle

Status: accepted (2026-08-12)

## Context

Locations are generated, not stored. The generator needs a time component so the
world changes, but the period has to balance three forces: a player must be able to
finish a location inside one cycle, the world must feel different when they come
back the same day, and the Redis delta log (cleared nodes, looted caches) must not
accumulate for long.

## Decision

`CYCLE_SECONDS = 21600` (6 hours), configurable. `cycle_index = unix_time //
CYCLE_SECONDS`. Four cycles per day - roughly one per waking session.

The cycle index is never read inside the domain: `procgen` receives it as an
argument, which keeps generation pure and testable.

A player already inside a location keeps the cycle index captured in their session
until they leave, so the map never changes under their feet. On leaving they are
told: `Сменилась стража. Тропы Сумеречной Рощи легли иначе.`

## Consequences

- Redis delta keys carry a TTL to the end of their cycle; storage stays bounded.
- Shop assortments roll on the same clock, giving players a reason to return.
- Tests must pass an explicit `cycle_index`; there is no hidden clock to freeze.
