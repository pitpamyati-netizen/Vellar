# ADR 0003 - A location lives until it is cleared

Supersedes the six-hour world cycle, which shipped in 0.1 and did not survive
its first play test.

## Context

The world used to roll over on a shared clock: `cycle_index = unix_time // 21600`
fed every seed, so all fifteen cities regenerated four times a day, together.

Two things were wrong with it in play. A location a player was working through
could be five hours away from changing or five minutes, and there was no way to
tell which - the map was on a timer nobody could see. And a game about walking a
road felt like a game about waiting for one: the answer to "there is nothing to
do here" was "come back in four hours", which for a session-based player means
tomorrow.

Six hours also had to mean something in the fiction, so the world grew a "watch"
that players had to learn before they could understand why the shop was empty.

## Decision

A location has a **generation**, not a cycle:

```
location_seed = blake2b(world_seed, city_id, slot, generation)
```

The generation goes up when the location is **cleared out** - every node except
the two doors worked through - and at no other time. Until then the same map
stands, however long that takes.

The state is shared. Everyone in a location sees the same map, the same emptied
nodes and each other; it lives in Redis under `loc:{city}:{slot}` with a week's
time to live, so a place nobody has visited for a week is re-rolled, which is
indistinguishable from nobody having visited it.

Two things stay on a clock, and both are short:

- the **shop rotation**, half an hour (`SHOP_ROTATION_SECONDS`), because a shelf
  that never changes gives nobody a reason to come back;
- the **gathering cooldown**, a quarter of an hour (`GATHER_COOLDOWN_SECONDS`),
  personal to each character rather than shared, so nobody waits out somebody
  else's timer.

## Consequences

- The domain still does not know the time: the generation, the rotation and the
  moment all arrive as arguments, and tests pass them explicitly.
- Clearing a location is a real event with a visible result - the place changes -
  instead of a mark that expired at an hour the player could not see.
- A player who finishes the last node walks out into a new map. That is the
  intended reward, not a bug.
- Two players finishing the last node at the same instant roll the location over
  once: `rotate` is a compare-and-set on the generation they both saw.
- The word "стража" leaves the game entirely, fiction included.
