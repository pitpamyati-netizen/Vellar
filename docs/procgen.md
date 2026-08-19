# Procedural generation

Locations are **not stored anywhere**. A map is a pure function of its seed, so
the server rebuilds it on demand and throws it away after rendering. The only
thing kept is what the players *took out of it* - how much of each node's wave is
gone - and that is a small hash in Redis with a time to live.

## The seed chain

```
world_seed              constant, from configuration (WORLD_SEED)
location_seed           = blake2b(world_seed, city_id, slot)      # permanent
node_seed(i)            = blake2b(location_seed, i)
wave_seed(i, wave)      = blake2b(location_seed, "wave", i, wave)
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
2. **The domain does not know the time.** The moment and the rotation are always
   arguments. `mmorpg.domain.procgen` never calls `time.time()`.
3. **Same seed, same bytes.** A test compares 10 000 derivations of the same seed.

## The map is permanent, the contents come in waves

A location never rolls over. Its nodes, their names, their levels and the paths
between them are the same for ever, so a player can learn a place by ear and keep
knowing it (`docs/adr/0013-permanent-locations-and-waves.md`).

What changes is what stands **in** the nodes. Every node holds a wave of several
things, sized by kind (`WAVE_SIZE` in `domain/rules/nodes.py`):

| node | wave |
| --- | --- |
| стычка | 2-4 packs |
| сильный противник | 1-2 |
| хозяин логова | 1 |
| заросли, жила руды | 3-5 handfuls |
| тайник | 1-3 |
| событие, святилище | 1-2 |

One action takes **one** thing out of the wave, not the node. When the last one
goes the node is empty, and `RESPAWN_SECONDS = 180` later the next wave stands
there - seeded by `wave_seed`, so it is different opponents and different finds
in the same place. There is no "этот узел кто-то прошёл" flag anywhere in the
game any more; what a screen says is a count.

The state is shared by everybody standing in the location, so a pack one player
killed is gone for the next one who walks in, and what neither of them touched
is still waiting.

The shop is the one thing left on a wall clock: `SHOP_ROTATION_SECONDS = 1800`,
so the shelf turns over every half hour and there is a reason to come back to a
city. Gathering has a personal cooldown, `GATHER_COOLDOWN_SECONDS = 900`, which
belongs to a character rather than to the world.

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

What is left in each of its nodes - the wave standing there, how much of it is
gone, and the moment it was emptied:

```
key    loc:{city}:{slot}          hash {node: "wave:taken:emptied_at"}
ttl    a week, refreshed on every write
```

A write names the wave the player saw: a press that arrives after the node has
already refilled belongs to a wave that is gone and changes nothing, which is
what makes two players killing the last pack together kill it once.

Who is standing in it, and where:

```
key    loc:{city}:{slot}:who      hash {character_id: {node, name, level, seen}}
ttl    ten minutes of silence and a player is no longer there
```

PostgreSQL never sees any of it: losing Redis refills every node and forgets who
was where, which costs a walk and never a character.

## What is generated versus stored

| Generated on demand | Stored |
| --- | --- |
| location layout, node kinds, node names, node levels | how much of each node's wave is gone (Redis, TTL) |
| enemies, their stats, their loot | items actually taken (PostgreSQL) |
| shop assortment | gold and purchases (PostgreSQL) |
| total character stats | raw stats, level, experience (PostgreSQL) |
