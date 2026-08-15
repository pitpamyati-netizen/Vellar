# Procedural generation

Locations are **not stored anywhere**. A location is a pure function of a seed and
a generation, so the server rebuilds it on demand and throws it away after
rendering. The only thing kept is what the players *changed* - which nodes are
cleared and which generation is standing - and that is two integers in Redis with
a time to live.

## The seed chain

```
world_seed              constant, from configuration (WORLD_SEED)
generation              goes up when a location is cleared out; never on a clock
location_seed           = blake2b(world_seed, city_id, slot, generation)
node_seed(i)            = blake2b(location_seed, i)
enemy_seed(node, try)   = blake2b(node_seed, "enemy", try)
rotation                = unix_time // SHOP_ROTATION_SECONDS  # 1800, half an hour
shop_seed               = blake2b(world_seed, "shop", city_id, rotation)
```

Every part is separated by a `\x00` byte before hashing, so `("ab", "c")` and
`("a", "bc")` cannot collide.

**Rules that must never be broken:**

1. **No global randomness.** `random.random()`, `random.choice()` and friends are
   forbidden. Every generator receives an explicit `random.Random` built from its
   seed by `procgen.seeds.rng`. A test seeds the global generator, runs generation,
   and asserts the global stream is untouched.
2. **The domain does not know the time.** The generation and the rotation are
   always arguments. `mmorpg.domain.procgen` never calls `time.time()`.
3. **Same seed, same bytes.** A test compares 10 000 derivations of the same seed.

## Generations

A location keeps its map until it is **cleared out**: every node except the two
doors worked through. Then, and only then, its generation goes up and the place
is generated anew - different nodes, different paths, different enemies. See
`docs/adr/0003-location-generations.md` for why this replaced the six-hour world
cycle.

The state is shared by everybody standing in the location, so a node one player
emptied is empty for the next one who walks in, and the last node any of them
finishes changes the place for all of them.

A player already inside keeps the generation captured in their session until they
leave, so the map never shifts under their feet mid-visit. No teleports, no "you
have been moved".

The shop is the one thing left on a clock: `SHOP_ROTATION_SECONDS = 1800`, so the
shelf turns over every half hour and there is a reason to come back to a city.
Gathering has a personal cooldown, `GATHER_COOLDOWN_SECONDS = 900`, which belongs
to a character rather than to the world.

## Location structure

- 8 to 14 nodes.
- Node 0 is the entrance, the last node is the exit.
- Interior node kinds are weighted: battle 42, gather 16, event 14, cache 12, elite
  battle 9, shrine 7.
- The **deepest interior node is always the boss**, so every location has exactly
  one and always the same distance in. It is not a toll on the way out: the graph
  has shortcuts, so fighting it is a decision. Because that node is forced, a
  location always contains at least one fight.
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
health = (24 + 9.0 * level) * archetype.health * spread * rank.health * share
damage = (3.5 + 0.7 * level) * archetype.damage * spread * rank.damage * share
armor  = 1.15 * level        * archetype.armor          * rank.armor
```

`spread` is a deterministic +-12% wobble derived from the same seed, so two fights
against "серый волк" at level 12 are not carbon copies while staying reproducible.

Health is set against the player's *standard blow* and damage against their health
pool, both of which grow with level on their own - so the shape of a fight is the
same at level 3 and at level 300. What that shape is, `tests/domain/test_combat_balance.py`
pins down: an ordinary fight is about three turns.

**Three tiers** (`EnemyRank`), and they differ in one thing only - how long the
fight lasts:

| tier | health | damage | armour | gold | turns |
| --- | --- | --- | --- | --- | --- |
| обычный | 1.0 | 1.0 | 1.0 | 1.0 | ~3 |
| эпический | 2.6 | 1.25 | 1.2 | 3.0 | ~5 |
| босс | 5.2 | 1.25 | 1.35 | 7.0 | ~10 |

Damage deliberately lags health: a boss that lasted four times as long *and* hit
four times as hard would simply end the fight on turn three of ten.

Both long tiers are single opponents and wear a title from `[meta].elite_titles` -
adjectives only, since they are glued in front of the archetype name ("Матёрый
серый волк"). The title does not say which tier: the combat screen does that in
words, so the name stays a name.

`share` is the **pack tax**: an ordinary encounter rolls one to three enemies
(weighted 6:3:1) and they divide one fight's budget, `1 / (1 + 0.45 * (size - 1))`.
Three full-strength opponents made an "ordinary" fight nine turns long - three
fights in a row wearing one name.

## The shared state of a location

Which generation is standing and what has been cleared in it:

```
key    loc:{city}:{slot}          hash {generation, cleared}
ttl    a week, refreshed on every write
```

Who is standing in it, and where:

```
key    loc:{city}:{slot}:who      hash {character_id: {node, name, level, seen}}
ttl    ten minutes of silence and a player is no longer there
```

14 nodes fit in 14 bits, so the whole state of a location is two small integers.
PostgreSQL never sees any of it: losing Redis re-rolls a map and forgets who was
where, which costs a visit and never a character.

## What is generated versus stored

| Generated on demand | Stored |
| --- | --- |
| location layout, node kinds, node names, node levels | which nodes the player cleared (Redis, TTL) |
| enemies, their stats, their loot | items actually taken (PostgreSQL) |
| shop assortment | gold and purchases (PostgreSQL) |
| total character stats | raw stats, level, experience (PostgreSQL) |
