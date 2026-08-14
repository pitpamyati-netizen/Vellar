# Skills: depth without buttons

The problem this design solves: more skills means more buttons, and more buttons
means a game a blind player cannot navigate and a sighted player cannot enjoy. The
answer is to **grow the content and freeze the interface**.

## The panel is fixed forever

A character always has exactly:

- **6 active skill slots**
- **3 passive skill slots**
- **1 separate racial slot** (the racial active never competes with class skills)

This never grows with level. The combat screen at level 1 and at level 300 has the
same number of buttons in the same positions - only their contents change.

## Where the content comes from

| Source | Actives | Passives |
| --- | --- | --- |
| Class | 8, unlocked at levels 1, 4, 8, 14, 22, 35, 60, 100 | 6, unlocked at 2, 6, 12, 20, 30, 50 |
| Race | 1 (racial slot) | 1, always on, occupies no slot |

Total content: 8 classes x 14 + 16 races x 2 = **144 abilities**. On screen at any
moment: **7 action buttons**.

The player equips **6 of 8** actives and **3 of 6** passives. That gap is the
build - the interest is in choosing, not in accumulating.

Race passives live in `races.toml` rather than the panel: they are inherent, always
active, and take no slot.

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

144 skills x 2 edges = 288 behaviours behind 7 buttons. Edges are re-picked at a
city Mentor for gold, so a build is a decision, not a life sentence.

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
6. **Empty slots are still rendered.** "5. Пустой слот" keeps every other button in
   its position (accessibility rule 7).

## One scale for every number

A skill's `power` in content is a **percentage**, never an absolute:

| category | percentage of | 100 means |
| --- | --- | --- |
| damage | the character's standard blow | one plain "Атака" |
| healing, shields | maximum health | a full bar |
| buffs, debuffs | the modifier itself | +100% to that stat |

The standard blow is `6 + 2.2 x level + 0.6 x scaling stat` - level carries the
curve, the stat carries the spread. So a level-1 skill written as 135 is worth a
third again as much as an attack at level 1 *and* at level 300, and content never
has to be re-tuned as the band grows.

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
[4. Пустой слот]
[5. Пустой слот]
[6. Пустой слот]
[Второе дыхание — расовое, оборона, лечит 24, откат 5 ходов, готово]
[Сумка] [Бежать]
[Назад] [Осмотреться] [Главное меню]
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
- Pressing an empty slot, a skill on cooldown, or a skill you cannot afford always
  produces an event to say so. The game never stays silent and never raises.
