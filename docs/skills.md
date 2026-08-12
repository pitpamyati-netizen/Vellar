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
Бой. Ход 3. Волчий вожак: здоровье 68 из 140.
Вы: здоровье 91 из 120, отвага 40 из 60.
Ваш ход.

[Атака]
[1. Рассечение — готово, 10 отваги]
[2. Вихрь клинков — откат 2 хода]
[3. Провокация — готово, 15 отваги]
[4. Пустой слот]
[5. Пустой слот]
[6. Пустой слот]
[Второе дыхание — расовое, готово]
[Сумка] [Бежать]
[Назад] [Осмотреться] [Главное меню]
```

Availability is stated in words inside the button text - never by colour, icon, or
by removing the button.

## Combat rules

- Strictly turn-based, no real-time timers anywhere. The state waits for the player
  indefinitely (accessibility rule 13). *Test: the engine source contains no clock.*
- One to three enemies per fight; area skills and the "second target" edges are the
  reason the engine is written for groups from the start.
- Every roll comes from a seed passed in by the caller, so a fight replays exactly.
- Resolution order per turn: player action, every living enemy, then upkeep
  (cooldowns tick, effects tick and expire, health and resource regenerate).
- Cooldowns are set to `cooldown + 1` when a skill fires, because the same turn's
  upkeep ticks them down once - so "откат 2 хода" really means two more turns.
- Pressing an empty slot, a skill on cooldown, or a skill you cannot afford always
  produces an event to say so. The game never stays silent and never raises.
