# Skills: depth without buttons

The problem this design solves: more skills means more buttons, and past a certain
count a keyboard stops being navigable by anyone - by ear it is unreadable, by eye
it is a wall. The answer is to **grow the content and freeze the interface**.

## The panel is fixed forever

A character always has exactly:

- **6 active skill slots**
- **1 separate racial slot** (the racial active never competes with class skills)

This never grows with level. The combat screen at level 1 and at level 300 has the
same numbers in the same order - only their contents change.

**Passive skills take no slot at all.** A passive has no button, no turn and no
target: "putting it in a slot" only ever meant that three of six learned passives
were switched off, and the points spent on them counted in no fight. A learned
passive works (`modifiers.passive_modifiers`). What a passive costs is the skill
point, and that is price enough.

## Where the content comes from

| Source | Actives | Passives |
| --- | --- | --- |
| Class | 8, unlocked at levels 1, 4, 8, 14, 22, 35, 60, 100 | 6, unlocked at 2, 6, 12, 20, 30, 50 |
| Race | 1 (racial slot) | 1, always on, occupies no slot |

Total content: 8 classes x 14 + 16 races x 2 = **144 abilities**. On screen at any
moment: **at most 7 action buttons**, and only the ones that do something.

The player equips **6 of 8** actives. That gap is the build - the interest is in
choosing, not in accumulating. Passives are not part of that choice: every one
learned is on.

Race passives live in `races.toml` rather than the panel: they are inherent, always
active, and take no slot - the same rule class passives now follow.

## Depth: ranks and edges

Every skill has **ranks 1 to 5**, bought with skill points (one per level). Each
rank raises the skill's power by `rank_step` (15% by default) - `power_at_rank`.

At **rank 3** the skill gains an **edge**: the player picks one of two
modifications that change how it behaves, without adding a button.

```
Рассечение, ранг 3. Выберите грань:
— Кровопускание: цель теряет здоровье ещё 3 хода.
— Размах: удар задевает вторую цель на 60 процентов урона.
```

128 skills x 2 edges = 256 behaviours behind 7 buttons (a race passive has none:
it is not in the panel and never bought with a point). Edges are re-picked at a
city Mentor for gold, so a build is a decision, not a life sentence.

Rank 3 is the default, not a constant: a **Seal of the Chamber** opens the edge
one rank earlier for each Turning its owner has made, down to rank 1
(`edge_rank_for`, `docs/endgame.md`). An edge pledged into a Turning is gone for
good and cannot be picked again - that is what keeps a free choice from minting
Seals.

**What an edge does mechanically** is declared by the edge itself, in
`skills.toml`, using the vocabulary in `domain/rules/edges.py`: a percentage or a
number of turns per key (`power`, `cost`, `cooldown`, `duration`, `dot_turns`,
`stun_turns`, `hits`, `splash`, `aoe`, `pierce`, `crit`, `lifesteal`, `cleanse`,
`heal`, `shield`, `self_modifiers`, `target_modifiers`). `edges.applied` lays the
declaration over the skill's effect; the loader refuses an edge that declares
nothing at all.

It used to be uniform - the first edge hit 20% harder, the second cost 35% less -
while content described a distinct behaviour for each of the 256. Every one of
those descriptions was false, and for the 48 passive skills the chosen edge did
nothing whatsoever: `passive_modifiers` never looked at it. An edge's text now
says what its numbers do, because it is generated from them.

An edge of a passive skill can only raise that passive's own modifier (`power`)
and add modifiers of its own (`self_modifiers`) - a passive has no turn and no
target for anything else to mean something.

## Spending a point, in the interface

Three screens, and no more (`presentation/telegram/screens/skills.py`):

- **Умения** - every skill of the class unlocked by level, with its rank and what
  one point would buy. Pressing one learns it, or raises it a rank; at rank 3 the
  press opens the edge screen instead, and nothing else happens until it is
  chosen - which is why the button says «сначала выберите грань» there rather
  than promising a rank it will not buy.
- **Слоты умений** - the six battle positions and the racial one, each button
  carrying its number and its contents. A skill sits in exactly one slot: putting
  it in a second one empties the first. The screen also reads out the passives
  that are working, so "изучено - значит работает" is visible and not just true.
- **Грань** - the two-way fork, once per skill.

A skill point is only ever handed back by the Mentor, who charges gold for it and
takes the skill out of the panel along with its edge (`skills.forget`). He deals
in class skills only: the racial one was never bought with a point and cannot be
given up for one, and offering it was a button that took payment and changed
nothing (`skills.forgettable`).

## Anti-bloat rules (enforced, not just intended)

1. **Equipment never grants an active skill.** It grants modifiers, and may boost a
   skill the character already has via `skill_modifiers` ("+20% to Рассечение").
   *Test: every `skill_modifiers` key must reference an existing skill.*
2. **Traits never grant an active skill.** Modifiers only.
   *Test: a `Trait` has no field that could hold one.*
3. **Consumables live in the combat Bag tab**, never in a skill slot.
   *Test: every consumable has `slot = "none"`.*
4. **The loadout changes only outside combat** - in a city or at a shrine. Inside a
   fight the layout is frozen, so the buttons a player learned stay put.
5. **A new skill is never auto-equipped.** The game announces it and leaves the
   choice to the player:
   > Доступно новое умение: Вихрь клинков. Меню - Умения - Набор.
6. **A number belongs to a skill, an empty slot gets no button.** The third skill
   is button "3." whether or not slots one and two are filled, so a panel learned
   once stays learned - but "5. Пустой слот" is not drawn, because a button whose
   whole answer is "there is nothing here" is a button that wasted a press to say
   so. It used to waste a **turn**: the press resolved as a turn in which the
   player did nothing and every enemy answered.
7. **A weapon requirement narrows a skill, never widens the panel.** A skill may
   name `weapons` (ids from `items.toml [meta].weapon_types`): a shot asks for a
   bow, a backstab for a dagger. Without one the skill does not fire and costs
   nothing - and the button says so *before* it is pressed, because a button that
   promises what it will not do is a bug. The list may only be narrower than what
   the class wields; the loader refuses a wider one (ADR 0014).
   *Test: every `weapons` entry is a kind its owning class actually holds.*

## One scale for every number

A skill's `power` in content is a **percentage**, never an absolute:

| category | percentage of | 100 means |
| --- | --- | --- |
| damage | one roll of the weapon in hand | one plain "Атака" |
| healing, shields | maximum health | a full bar |
| buffs, debuffs | the modifier itself | +100% to that stat |

A damage skill may add dice of its own on top of the weapon roll:

```toml
weapons = ["dagger"]
dice = "2d8"                   # rolled and added, and grows with rank
```

The blow is `weapon dice + 0.6 x scaling stat` - the **weapon** carries the curve
of the whole 1-300 band, the stat carries the spread between characters of the
same level (ADR 0015). So a skill written as 135 is worth a third again as much
as an attack at level 1 *and* at level 300, and content never has to be re-tuned
as the band grows - what the player upgrades is the thing in their hand.

That is also why a damage skill is read out as a **range** - "урон от 34 до 96" -
and never as one number: one number would promise a precision the dice do not
have. Half of that range is not rolled at all: every weapon kind carries a flat
part alongside its dice (`1d10+3`), so the range stays a range a player can plan
against instead of a lottery (ADR 0017).

Before this, `power` was an absolute number while the plain attack grew with
level; by level 30 every skill in the game was weaker than pressing "Атака", and
the whole panel was decoration. See ADR 0007.

## The engine contract

`content/skills.toml` declares an `effect` string per active skill.
`mmorpg/domain/rules/skill_effects.py` maps each one to an `EffectSpec` - damage
scale, area, pierce, stun, modifiers applied and for how long, and so on. The
combat engine executes specs; it has no per-skill code.

Two tests keep the two halves honest:

- every effect used by content has a spec (otherwise a skill would silently do
  nothing);
- every spec is used by content (otherwise dead code drifts out of sync).

Adding a skill needs **no code**. Adding a new *kind* of behaviour needs exactly
one table entry.

### A modifier the engine does not read is not a bonus

A passive skill and an edge both state their bonus as a key from the shared
vocabulary. That vocabulary (`traits.toml [meta].modifier_keys`) is deliberately
wider than what the engine computes - it holds keys for mechanics that do not
exist yet. **Skills may only use keys that are computed**, listed in
`domain/rules/modifiers.py::EFFECTIVE_KEYS` and pinned by two tests in
`tests/content`. Fifteen class passives and four edges pointed at uncomputed keys
for half a year: the player spent a skill point and got a line of text (ADR 0018).

What the engine reads that is easy to miss:

- **situational damage** - by the target's kind (beast, undead, humanoid), by its
  tier, by how wounded it is, by your own low health, by the first turn, by one
  target or all, and by whether the blow is a spell or a hand (a skill is a spell
  when its effect carries an element tag);
- **tempo** - initiative decides how often an opponent fails to answer at all
  (`combat.outpace_chance`), which is what every "инициатива ниже на N процентов"
  in content buys;
- **shields expire** (`EffectSpec.shield_turns`), healing over time arrives once a
  turn rather than all at once, and a counter, an undying stand and an immunity to
  stuns are self-modifiers under private keys the combat engine reads by name.

## Combat screen shape

```
Бой. Ход 3.
Волчий вожак (эпический): здоровье 68 из 140, это 49 процентов. Намерение: натиск, брешь — оборона.
Вы: здоровье 91 из 120, это 76 процентов, отвага 40 из 60.
След: натиск. Дальше: повтор даст разгон 25 процентов; точность даст перелом.
Ваш ход.

[Атака — натиск]
[1. Рассечение — натиск, урон 34, стоит 10, готово]
[2. Вихрь клинков — натиск, урон 33 по всем, ещё 2 хода]
[3. Провокация — оборона, цели урон минус 20 процентов на 2 хода, стоит 15, откат 2 хода, готово]
[Второе дыхание — расовое, оборона, лечит 24, откат 5 ходов, готово]
[Сумка] [Бежать]
[Назад] [Главное меню]
```

Every button answers three questions without being pressed: **what it does**
(a number, not a category), **what it costs**, and **when it comes back**. The
cooldown is stated twice on purpose, because it is two different questions: while
the skill is ready, "откат 3 хода" is the price of using it; while it is spent,
"ещё 2 хода" is what is left. Availability is always words inside the button text -
never colour, never an icon, never a missing button.

## Tempo: intent, trace, breach

Depth without buttons applies to the fight itself. Every action carries one of
three tags - **натиск**, **оборона**, **точность** - in a closed circle: a guard
stops a press, precision finds the gap in a guard, a press is inside the reach
before precision picks its spot (`counter_to`).

- **Намерение.** Each enemy announces the tag of its next move *before* the
  player acts. The announcement is a pure function of the enemy and the turn
  (`enemy_intent`) - no roll, no state, so the screen and the engine always name
  the same one, and the promise cannot be taken back mid-turn. A press hits for
  1.4x and leaves the enemy open (armour x0.75); a guard hits for half and
  doubles its armour; precision hits normally and is not dodged. Below a quarter
  of its health an enemy always guards.
- **След.** The player's own tags are remembered, three deep. Repeating a tag is
  **разгон**: +25% damage *per repeat*, so a third identical tag is worth +50%.
  Three *different* tags in a row are a **перелом**: the enemies do not answer
  that turn, and the trace is spent - so cycling the same three forever does not
  work.
- **Брешь.** A tag that counters the announced intent takes that enemy's armour
  out of the count **and halves the blow it answers with**. Both halves matter:
  the counter to a press is a guard, and a guard deals no damage, so an
  armour-only reward was worth nothing against a third of all intents.

A skill's tag is read off its effect (`tag_of`): a blow presses, a blow that is
aimed - at armour, at a weak spot, at a mark - or that leaves the target hindered
is precision, everything that mends or shields is a guard. Content may name it
outright with `tag = "точность"` in `skills.toml`.

**Every class can reach all three tags.** It has to: a перелом needs three
different tags in a row, so a class with only two would be locked out of the rule
arithmetically. The plain attack is always a press, so each class needs a guard
and a precision of its own, and has both by level 14 - that is what the `tag`
overrides in content buy.

An action that never happened - an empty slot, a cooldown, a cost the player
cannot pay - leaves no trace, and neither does fleeing or a skipped turn.

## Combat rules

- Strictly turn-based, no real-time timers anywhere. The state waits for the player
  indefinitely (accessibility rule 13). *Test: the engine source contains no clock.*
- One to three enemies per fight; area skills and the "second target" edges are the
  reason the engine is written for groups from the start. A pack divides one
  fight's budget between its members, so three opponents are one fight and not
  three (`procgen/enemies.py`, `group_scale`).
- **An ordinary fight is about three turns**, an epic one roughly twice that, a
  boss twice again - and those are the only long fights in the game.
  `tests/domain/test_combat_balance.py` measures it rather than trusting it, and
  also checks that a player who reads the intent finishes sooner than one who only
  presses "Атака".
- Accuracy answers the **difference** in levels, never the absolute one: a fight at
  your own level is even, and being out of your depth is what costs you.
- Every roll comes from a seed passed in by the caller, so a fight replays exactly.
- Resolution order per turn: intents and the player's tag are settled first, then
  the player's action, then every living enemy - unless a перелом silenced them -
  then upkeep (cooldowns tick, effects tick and expire, health and resource
  regenerate).
- Cooldowns are set to `cooldown + 1` when a skill fires, because the same turn's
  upkeep ticks them down once - so "откат 2 хода" really means two more turns.
- Pressing an empty slot, a skill on cooldown, a skill you cannot afford, or one
  your hands cannot use always produces an event to say so, and **costs nothing**:
  the turn counter stays put, the trace is untouched, cooldowns do not tick and no
  enemy answers (`combat._refusal`). A refusal is the game declining to act, not
  the player spending a turn on nothing. The game never stays silent and never
  raises.
- **A miss leaves nothing behind.** Bleeding, a hindrance, a stun and a splash all
  follow the blow landing, not the button being pressed - otherwise missing paid
  better than hitting. Skills that carry no attack roll at all (a plain hindrance,
  a plain buff) are unaffected: there is no miss to have.
- The last turn of a fight is read out on the screen that ends it. "Победа." with
  nothing before it does not say who struck last or for how much.
