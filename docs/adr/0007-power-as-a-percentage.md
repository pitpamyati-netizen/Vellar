# ADR 0007 - Every magnitude is a percentage of something that grows

Status: accepted (2026-08-14)

## Context

Content stated a skill's power as an absolute number: Рассечение 14 at level 1,
Пробой строя 70 at level 60, Сингулярность 140 at level 100. The plain attack,
meanwhile, was `8 + 1.5 x level` in code.

The two curves crossed at about level 30. Past it, every skill in the game did
less than pressing "Атака", and the six-button panel the whole design is built
around became decoration: the correct play at level 100 was to press the same
button until something died. A simulation over all eight classes and eight levels
confirmed it - a "clever" player and a player who only attacked finished in the
same number of turns.

Three more faults were found in the same measurement, and they share the shape:

- **Armour** was softened by a constant 100 while armour itself grew linearly with
  level, so a level-300 blow landed for a quarter of itself.
- **Accuracy** was `accuracy - enemy.level x 0.5` - an absolute level, not a gap.
  At level 300 the hero missed three blows in five while enemies, whose accuracy
  *rose* with their absolute level, never missed at all.
- **Criticals** multiplied an uncapped chance by an uncapped multiplier, so a luck
  build hit three times as hard as anyone else - not a build, an exploit.

## Decision

Nothing in content is an absolute number. Every magnitude is a percentage of
something that already grows on its own.

- **Damage** is a percentage of the character's *standard blow*,
  `6 + 2.2 x level + 0.6 x scaling stat`, where the plain attack is 100. Level
  carries the curve and the stat carries the spread - if the stat carried it
  alone, a class with one key stat would pour every point into it and hit twice
  as hard as a class with two.
- **Healing and shields** are percentages of maximum health, which grows about
  five times faster than a blow does. `heal_percent` as a separate effect is gone;
  every heal is a percentage now.
- **Buffs and debuffs** were already percentages of the modifier they move.
- **Armour** is softened against the defender's level, so it eats a steady fifth
  to a quarter of a blow at every level.
- **Accuracy** answers the difference in levels. A fight at your own level is even.
- **Criticals** are capped at 50% chance and 250% damage.

Enemy health is set against the standard blow and enemy damage against the health
pool, so an ordinary fight is about three turns at level 3 and at level 300.

## Consequences

- Adding a skill still needs no code, and now it needs no re-tuning either: a
  number written once is correct across the whole 1-300 band.
- The 52 damage, healing and shield skills in `skills.toml` were rewritten onto
  the new scale in one pass. Their *relative* strengths are a deliberate table
  now (opener 130, cooldown blow 170-200, area 110-150, capstone 210-275), not an
  accident of when each was written.
- A skill button can finally state what it will do - "урон 34", "лечит 24" - which
  is what makes the panel legible without sight. That is not a side effect; it is
  half the reason for the change.
- Balance moved from "read the code and hope" to
  `tests/domain/test_combat_balance.py`, which simulates whole fights and fails if
  an ordinary one stops being about three turns. The constants will still move in
  the first days of open beta - but a move will be visible.
- Old saved characters are unaffected: nothing derived is stored, so every number
  is recomputed from the level and the allocated points (rule 7).
