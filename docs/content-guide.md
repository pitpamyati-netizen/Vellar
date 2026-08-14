# Content guide

All game content lives in `content/*.toml`. **Adding a race, a class, a trait, an
item or a city requires no code changes.** The loader
(`src/mmorpg/infrastructure/content/loader.py`) parses these files once at startup,
validates them, and fails loudly with the full list of problems if anything is
wrong - the bot refuses to start on broken content.

| File | Contains |
| --- | --- |
| `world.toml` | 15 cities, 5 locations each, level bands, unlock conditions |
| `races.toml` | 16 peoples: stat bonuses, passive ability, racial active reference |
| `classes.toml` | 8 crafts: key stats, resource curve, health curve, progression meta |
| `traits.toml` | 60+ traits, the modifier vocabulary, categories |
| `skills.toml` | 8 active + 6 passive per class, 1 active per race, both edges of each |
| `items.toml` | equipment, consumables, materials, rarities, slots |

## Ground rules

1. **Traits and equipment never grant active skills.** They grant modifiers, and
   equipment may additionally boost a specific skill through `skill_modifiers`.
   This is what keeps the interface small enough to be played by ear
   (`docs/skills.md`).
2. **One modifier vocabulary.** Traits, passives and equipment all draw from
   `traits.toml [meta].modifier_keys`. A key that is not listed there is rejected.
   Add the key to that list first, then implement it in the rules layer.
3. **Panel size is fixed.** 6 active, 3 passive, 1 racial. New content changes what
   fills the slots, never how many there are.
4. **Player-visible text is Russian, everything else English.** Names, descriptions
   and `text` fields are what the player hears; ids, codes and comments are English.
5. **No pseudo-graphics in any text field** - screen readers read them character by
   character. Numbers as words: `"выше на 15 процентов"`.
6. **Names belong to Vellar.** A people carries a name of its own and says where
   it is from in the description; a class is named after the work it lives by.
   Nothing borrows from the fantasy shelf or the black list in `Narrative.md`,
   section 2. `tests/content/test_naming.py` reads every name and description in
   this directory and fails on either.

## Add a race

```toml
[[race]]
id = "seaborn"                       # snake_case, unique, never changes
name = "Сельдар"                     # the people's own name: one Cyrillic word, <= 12 letters
description = "Одна фраза: откуда народ и чем известен."
bonuses = { AGI = 2, WIS = 1, STR = -1 }
passive = { id = "tide_born", name = "Приливная выучка", text = "Одна фраза об эффекте." }
active = "race_seaborn_undertow"     # must exist in skills.toml
```

The id is frozen from the moment the first character picks it: characters point
at it in the database, so a rename is a `name`/`description` change and never a
change of key.

Budget rule, enforced by `tests/content/test_races_classes_skills.py`: positive
points may not exceed `3 + (sum of penalties)`, and the net total may not exceed
`+3`. `{ STR = 2, END = 2, INT = -2 }` is legal; `{ STR = 4 }` is not.

Then add the racial active to `skills.toml` with `owner = "race:seaborn"`, `kind =
"active"`, `level = 1` and exactly two edges. The passive stays in `races.toml`: it
is always on and occupies no slot.

Finally bump `EXPECTED_RACES` in the loader and the race count test - the count is
asserted deliberately, so growing the roster is a conscious decision.

## Add a class

```toml
[[class]]
id = "monk"
name = "Кулачник"                    # a craft: what the character lives by
role = "Ближний бой без оружия"
description = "Одна фраза."
key_stats = ["AGI", "WIS"]
bonuses = { AGI = 2, WIS = 1 }
resource = { id = "chi", name = "Ци", base = 55, per_level = 1.2, stat = "WIS", per_stat = 1.5, regen_per_turn = 8 }
health = { base = 95, per_level = 7.5, per_endurance = 6.0 }
```

A class needs exactly **8 active** skills at levels `[1, 4, 8, 14, 22, 35, 60,
100]` and exactly **6 passive** skills at levels `[2, 6, 12, 20, 30, 50]` (the lists
in `classes.toml [meta]`). The loader checks both the counts and the exact level
sequence.

## Add a skill

```toml
[[skill]]
code = "monk_palm_strike"      # unique across all skills
name = "Удар ладонью"          # unique within its owner - buttons route by text
owner = "class:monk"           # "class:<id>" or "race:<id>"
kind = "active"                # or "passive"
level = 1                      # must match the unlock schedule for its kind
cost = 10                      # active only
cooldown = 0                   # active only, in turns
target = "enemy"               # self | enemy | all_enemies
effect = "damage"              # must be listed in skills.toml [meta].active_effects
power = 135                    # a PERCENTAGE at rank 1 - see below
scaling = "AGI"                # which stat the standard blow is measured on
tag = "точность"               # optional: натиск | оборона | точность
text = "Одна фраза, что делает умение."
edges = [
    { name = "Первая грань", text = "Что меняется." },
    { name = "Вторая грань", text = "Что меняется иначе." },
]
```

A passive declares `effect` as a **modifier key** instead, and omits
`cost`/`cooldown`/`target`/`scaling`:

```toml
effect = "armor_percent"
power = 6
```

**`power` is never an absolute number.** It is a percentage of something that
already grows with the character, so a skill written once is correct at level 1
and at level 300:

| effect kind | percentage of | 100 means |
| --- | --- | --- |
| damage | the standard blow | one plain "Атака" |
| healing, shields | maximum health | a full bar |
| buffs, debuffs | the modifier itself | +100% to that stat |

Rough scale for damage: an opener with no cooldown 130, a blow on a cooldown
170-200, an area skill 110-150 *per target*, a capstone 210-275. Writing an
absolute number here is the one mistake that cannot be caught by a test - it will
simply be a skill that stops mattering. See ADR 0007.

`tag` is optional and only needed when the effect would leave the wrong trace, or
when a class would otherwise never reach all three tags - and every class must,
or a перелом is impossible for it.

Every skill declares exactly two edges; their codes are derived as `<code>_a` and
`<code>_b`. Introducing a new `effect` value means adding it to
`[meta].active_effects` **and** implementing it in the combat engine - the engine
test fails on an effect it does not handle.

## Add a trait

```toml
[[trait]]
id = "stone_patience"
name = "Каменное терпение"
category = "defense"                 # one of traits.toml [meta].categories
tags = ["броня", "выносливость"]     # free-form, shown in filters
modifiers = { armor_percent = 8, initiative_percent = -4 }
text = "Броня выше на 8 процентов, инициатива ниже на 4."
```

Traits in the `dark` category must contain both an upside and a real penalty; the
test uses `[meta].lower_is_better` to decide which direction is which (for
`shop_price_percent` a negative value is the bonus).

## Add an item

```toml
[[item]]
id = "sea_glass_blade"
name = "Клинок морского стекла"
kind = "equipment"                   # equipment | consumable | material
slot = "weapon"                      # weapon/head/body/hands/feet/trinket, or "none"
rarity = "rare"                      # from items.toml [meta].rarities
level = 18
price = 760
modifiers = { physical_damage_percent = 16 }
skill_modifiers = { monk_palm_strike = 20 }   # +20% to that skill, never a new button
text = "Одна фраза."
```

Consumables must declare `stack` and an `effect` table, and always use `slot =
"none"` - they live in the combat Bag tab.

## Add or rebalance a city

```toml
[[city]]
id = "seaward"
order = 16
name = "Приморье"
description = "Одна фраза."
level_min = 290
level_max = 320
unlock_level = 290
unlock_requires = ["city:last_beacon"]
services = ["shop", "locations", "dungeons", "tavern", "mentor", "bank"]

[[city.location]]
id = "shell_shore"
slot = 1
name = "Ракушечный берег"
biome = "берег"
level_min = 290
level_max = 294
```

Invariants checked by `tests/content/test_world.py`:

- exactly 15 cities (change `EXPECTED_CITIES` deliberately if you extend the world);
- exactly 5 locations per city, slots `1..5` in order;
- inside a city, both bounds increase from location to location and there is no gap
  (`next.level_min <= previous.level_max`);
- the first location starts at the city's `level_min`, the last ends at its
  `level_max`;
- city bands overlap: the next city starts inside the previous one's band, so a
  player always has both a place to push and a place to farm;
- every level from 1 to `max_character_level` is covered by at least one location.

Location level ranges drive enemy level, loot quality and rarity, experience and
event difficulty. Location layout itself is generated, never stored - see
`docs/procgen.md`.

## Add a contract

`content/quests.toml`. A contract is a paid job: the giver names the price in the
first two sentences, and refusing is always a button (`Narrative.md`, section 4).

```toml
[[quest]]
id = "farhold_tallies"     # frozen key: it lives in the character's ledger
city = "farhold"           # who hands it out, and where it is handed in
level = 1                  # not offered below this level
follows = ""               # stays off the board until that contract is paid out
name = "Столбы на Тракте"
giver = "Довен, писарь заставы"
intro = "Стоит у столба со сводкой."          # up to 140 characters
terms = "Обойдите три места на Лугах, плачу 40."  # up to 200
objective = "search"       # kill | elite | search
target_count = 3
target_kind = ""           # kill: an enemy kind; search: gather/cache/shrine/event
reward_gold = 40
reward_experience = 60
reward_item = "small_healing_potion"   # optional
```

What each objective counts (`domain/rules/quests.py`): `kill` counts defeated
opponents, narrowed by enemy kind; `elite` counts only the strong ones; `search`
counts nodes worked through without a fight. A counter never runs past its target,
and only moves for a contract the character has actually taken.

Checked by `tests/content/test_quests.py`: the city exists, the reward item
exists, the chain never loops, the level sits inside the city's band, a contract
never pays less than the one it follows, and none of it speaks the black-listed
vocabulary from `Narrative.md`.

## Checking your changes

```bash
uv run pytest tests/content
```

The loader reports every problem at once:

```
content validation failed (2 problems):
  - races.toml: seaborn spends 5 positive points but its budget is 4
  - skills.toml: class monk has 7 actives, expected 8
```
