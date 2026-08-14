# Procedural generation

Locations are **not stored anywhere**. A location is a pure function of a seed and
a cycle index, so the server rebuilds it on demand and throws it away after
rendering. The only thing persisted is what the player *changed* - cleared nodes,
looted caches - and that is a single integer in Redis with a TTL.

## The seed chain

```
world_seed              constant, from configuration (WORLD_SEED)
cycle_index             = unix_time // CYCLE_SECONDS         # CYCLE_SECONDS = 21600
location_seed           = blake2b(world_seed, city_id, slot, cycle_index)
node_seed(i)            = blake2b(location_seed, i)
enemy_seed(node, try)   = blake2b(node_seed, "enemy", try)
shop_seed               = blake2b(world_seed, "shop", city_id, cycle_index)
```

Every part is separated by a `\x00` byte before hashing, so `("ab", "c")` and
`("a", "bc")` cannot collide.

**Rules that must never be broken:**

1. **No global randomness.** `random.random()`, `random.choice()` and friends are
   forbidden. Every generator receives an explicit `random.Random` built from its
   seed by `procgen.seeds.rng`. A test seeds the global generator, runs generation,
   and asserts the global stream is untouched.
2. **The domain does not know the time.** `cycle_index` is always an argument.
   `mmorpg.domain.procgen` never calls `time.time()`.
3. **Same seed, same bytes.** A test compares 10 000 derivations of the same seed.

## Cycles

`CYCLE_SECONDS = 21600` - six hours, four cycles a day. See
`docs/adr/0003-six-hour-world-cycle.md` for why. In fiction a cycle is a
**стража**, a quarter of the day: at its end the road posts send a fresh dispatch,
and by the middle of the next watch the land has already moved on - paths drift,
beasts follow the water, caravans come and go (`Narrative.md`, section 1).

When the cycle rolls over the world regenerates. Players are told in-fiction:

> Сменилась стража. Тропы Ольшаника легли иначе.

A player already inside a location keeps the cycle index captured in their session
until they leave, so the map never shifts under their feet. No teleports, no
"you have been moved".

`seconds_left_in_cycle(now)` is used directly as the Redis TTL, so delta keys
expire exactly when they stop being meaningful and the database never grows.

## Location structure

- 8 to 14 nodes.
- Node 0 is the entrance, the last node is the exit.
- Interior node kinds are weighted: battle 42, gather 16, event 14, cache 12, elite
  battle 9, shrine 7. If the roll produces no fight at all, one node is forced to
  be a battle - a location is never a corridor.
- Node level rises with depth from the location's `level_min` to its `level_max`.

**Connectivity is structural, not checked-and-retried.** Node `i` is always linked
to some node `j < i` before any extra edges are added, which makes the graph a
spanning tree rooted at the entrance; a few random shortcuts are added on top.
Every node - including the exit - is therefore reachable from the entrance by
construction. Links are symmetric: the graph is undirected.

Property tests (`tests/domain/test_procgen.py`) assert, over hundreds of generated
seeds and cities:

- node count within bounds;
- graph connected, exit reachable from the entrance;
- no isolated nodes, no self-links, links symmetric;
- node levels inside the location's band and non-decreasing with depth;
- at least one combat node.

## Enemies

Enemies come from archetypes in `content/enemies.toml`, filtered by the location
biome (with a wildcard pool as fallback so an unknown biome degrades instead of
crashing), then scaled to the node level:

```
health = (28 + 11.5 * level) * archetype.health * spread
damage = (5 + 2.1 * level)  * archetype.damage * spread
armor  = 1.15 * level       * archetype.armor
```

`spread` is a deterministic +-12% wobble derived from the same seed, so two fights
against "серый волк" at level 12 are not carbon copies while staying reproducible.

Elites are single opponents with 2.3x health, 1.45x damage and 3x gold, and get a
title from `[meta].elite_titles` ("Матёрый серый волк"). Ordinary encounters roll
one to three enemies, weighted 6:3:1.

## The delta log

What the player changed inside the current cycle is a bitmask:

```
key    loc:{city}:{slot}:{cycle}:{user}
value  bitmask of cleared node indexes
ttl    seconds_left_in_cycle(now)
```

14 nodes fit in 14 bits, so the whole state of a location for one player is one
small integer that disappears when the cycle ends. PostgreSQL never sees it.

## What is generated versus stored

| Generated on demand | Stored |
| --- | --- |
| location layout, node kinds, node names, node levels | which nodes the player cleared (Redis, TTL) |
| enemies, their stats, their loot | items actually taken (PostgreSQL) |
| shop assortment | gold and purchases (PostgreSQL) |
| total character stats | raw stats, level, experience (PostgreSQL) |
