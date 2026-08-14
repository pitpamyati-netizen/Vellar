# ADR 0006 - Depth in tags, not in timers or buttons

Status: accepted (2026-08-14)

## Context

A turn-based fight with no clock and a frozen seven-button panel
(`docs/skills.md`) risks one failure mode above all others: the best play is to
press the strongest button until something dies. Every usual cure is closed to us.

- **Reaction timers** are banned in PvE (`docs/accessibility.md`, rule 13). A
  player using a screen reader hears the state after the sighted player has
  already read it; a clock turns that into a permanent handicap.
- **More buttons** contradict the whole skills design: the panel is fixed at
  level 1 and at level 300, and positional memory is what makes it navigable.
- **Positioning, facing, cover** need a spatial model the player cannot see.

What is left is *information*: a fight can be a decision if the player knows
something before choosing, and if the choice has a consequence they can hear.

## Decision

Three tags - натиск, оборона, точность - in a closed circle of counters, carried
by every action and by every enemy move.

- Enemies **announce** the tag of their next move before the player acts, and the
  announcement is a pure function of the enemy and the turn (`enemy_intent`) - no
  roll and no stored field. The screen and the engine therefore always name the
  same intent, and the whole turn is settled up front (`TurnTempo`) so a promise
  made before the player's blow is still kept after it.
- The player's own tags are remembered three deep (`Trace`). A repeat is momentum;
  three different tags in a row are a breakthrough and the enemies skip their
  answer.
- A tag countering the announced intent takes that enemy's armour out of the
  count for the turn.

Every tag is a **word inside a label the player already has**, and the trace is
one spoken line. No button was added.

## Consequences

- The fight has two lines of play - hammer one tag for damage, or cycle three for
  tempo - and they exclude each other, which is the choice the panel could not
  express before.
- A breakthrough **spends** the trace. Without that, cycling press-guard-precision
  forever would break every turn from the fourth on, and the enemies would never
  act again. This is the single least obvious rule in the engine.
- Deterministic intents mean a player can learn an enemy's rhythm. That is a
  feature here, not a leak: learnable beats memorable-by-sight, and the wounded
  enemy override keeps a fight from ending the way it started.
- Balance now lives in six constants (`INTENT_ARMOR`, `INTENT_DAMAGE`,
  `MOMENTUM_*`, `WOUNDED_RATIO`). Tests pin the thresholds so moving one is a
  deliberate act; the first days of open beta will move them.
- The arena (Roadmap 3.1) is the one place with a clock, and the tags work there
  unchanged: an announced intent is exactly what an auto-action can be chosen
  against.
