# ADR 0013 - A location is a place; its contents come in waves

Supersedes ADR 0003, which made the whole map roll over once the last node fell.

## Context

Under 0003 a location kept its map until it was cleared out, and then it was
regenerated whole: different nodes, different names, different paths. Two things
went wrong once players were actually walking it.

A node was a **switch**. One press and it read "этот узел вы уже прошли" for
ever - for everybody, including the player who had just walked ten minutes to
reach it. A location with fourteen nodes was fourteen presses of content, and
then a dead place until somebody finished the last one.

And when it did roll over, the place a player had learned by ear stopped
existing. For a blind player a map is a memorised list of turns: "вход, узел 3
налево, за ним заросли". Re-rolling the graph threw that away every time. The
reward for finishing a location was losing the only thing about it that was
worth knowing.

## Decision

Split the two things that used to be one.

**The map is permanent.** `location_seed = blake2b(world_seed, city_id, slot)` -
no generation, no clock. Луга у Заставы have the same nodes, the same names and
the same paths today, tomorrow and after a restart. A player can learn a location
and keep knowing it.

**The contents come in waves.** Every node holds a wave of several things - two
to four packs in a засада, three to five handfuls in a жила руды, one to three
bundles in a тайник (`WAVE_SIZE` in `domain/rules/nodes.py`). Each action takes
**one** of them, not the node. When the last one goes the node is empty, and
`RESPAWN_SECONDS = 180` later it fills up with the next wave - seeded by
`blake2b(location_seed, "wave", node, wave)`, so it is new opponents and new
finds in the same place.

The shared state is per node, not per location:

```
key    loc:{city}:{slot}     hash {node: "wave:taken:emptied_at"}
ttl    a week, refreshed on every write
```

A press that names a wave the node has already left changes nothing, which is
what makes two players killing the last pack together kill it once.

## Consequences

- The "кто-то здесь уже прошёл" flag is gone from the game. What a screen says
  now is a count: "Противников: 2 из 3", or how many minutes until the node
  fills up.
- A location is worth staying in. Three minutes is short enough that walking a
  circle of four nodes brings you back to a full one.
- Two players in one location share the drain but not the walk: what one of them
  took is gone, what neither touched is still there.
- `LocationSession` shrank to where the player is standing. Nothing about the
  map is captured on entry, because nothing about it can change.
- The domain still does not know the time: `now` arrives as an argument to
  `refreshed`, `taken_one` and `seconds_until_refill`, and the caches take it as
  a parameter too.
- The world seed still moves everything: a new `WORLD_SEED` is a new world.
