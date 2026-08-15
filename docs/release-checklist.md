# Release checklist

Run through this before shipping anything player-facing. The accessibility half is
not advisory: a failure there makes the game unplayable for its primary audience.

## 1. The gate

```bash
pwsh -File scripts/ci.ps1
```

- [ ] `ruff check` and `ruff format --check` clean
- [ ] `mypy --strict` clean
- [ ] `pytest` green
- [ ] `domain/` coverage at 90% or above (the CI script enforces this separately)

## 2. Accessibility

The full rules are in [accessibility.md](accessibility.md). The mechanical ones are
covered by `tests/presentation/test_accessibility.py`; these are the ones a human
still has to check:

- [ ] Every **new screen is listed in `tests/presentation/conftest.py::all_screens`**
      - a screen nobody listed is a screen nobody checked
- [ ] First line of each new screen answers "where am I / what just happened"
- [ ] Numbers read as `X из Y`, never as a bar or a table
- [ ] Every new action has a typed command duplicate
- [ ] Button positions unchanged between refreshes of the same screen; unavailable
      actions stay in place and explain themselves in the body
- [ ] Pressing a button from a *different* screen produces the "action unavailable"
      answer, not silence and not a crash
- [ ] Listen to one full session with a screen reader before shipping

## 3. Content

```bash
uv run pytest tests/content
```

- [ ] Content loads: the bot refuses to start on invalid content, so a green run
      here is a green start
- [ ] New skills declare an `effect` that exists in `EFFECT_SPECS`
- [ ] New modifier keys added to `traits.toml [meta].modifier_keys` first
- [ ] World table still covers levels 1-300 with no gaps

## 3a. The keeper panel

Only when the panel or what it edits changed. The rules are in
[keeper.md](keeper.md).

- [ ] A new field on an editable entity is a row in `FIELDS`
      (`domain/rules/overlay.py`), not a button in the screen - the card is drawn
      from the description
- [ ] A field whose value ends up on a button carries a `limit`, or a keeper can
      type a paragraph into one
- [ ] `overlay.apply` still leaves `content/` untouched: dropping every edit gives
      back exactly the world the files describe
- [ ] A half-written edit is stored, is *not* in the game, and the card says why
- [ ] Anything the panel deletes for good is confirmed twice or reports its number

## 4. Determinism

- [ ] `tests/domain/test_procgen.py` green - a change that makes generation
      non-reproducible breaks every player's map mid-visit
- [ ] No new use of the global `random` module anywhere

## 5. Data and migrations

```bash
docker compose up -d postgres redis
uv run pytest -m integration
```

- [ ] New columns have a migration in `migrations/versions/`
- [ ] The integration tests pass. They are the *only* place the SQL is executed;
      the rest of the suite runs against the in-memory adapters and cannot see a
      column PostgreSQL refuses, or a name it reserves - `verbose` was one
- [ ] Every new query is covered there, or it ships unverified
- [ ] Nothing derived was added to a table: totals are recomputed, not stored
- [ ] Redis keys carry a TTL
- [ ] A purse two players can reach at once moves by `spend_gold`/`grant_gold`,
      never by writing back a character read earlier in the step

## 5a. The economy

Only when something that pays or charges changed.

- [ ] Every new movement of gold writes a `gold_flow` line
      (`mmorpg.economy_log`), or the next tuning pass is a guess again
- [ ] Nothing new hands out gold that does not come from somewhere: the Circle
      pays out of what it holds, the duty removes, fights and contracts create
- [ ] Combat numbers still meet their promises -
      `tests/domain/test_combat_balance.py` covers length, the cost of an
      ordinary fight, and the spread between classes

## 6. Runtime

- [ ] `Start.bat local` starts and plays
- [ ] `Start.bat` (or `docker compose up -d --wait`) reaches healthy on its own
- [ ] Startup logs show `content_loaded` with the expected counts, then `connected`
- [ ] `docker compose stop bot` logs `Polling stopped` and exits 0 - a shutdown
      that has to be killed is dropping players' updates
- [ ] No `slow_operation` or asyncio slow-callback warnings under normal play
- [ ] `SLOW_CALLBACK_DETECTOR` is off wherever players are connected

## 7. Channel and group

- [ ] `uv run python scripts/broadcast.py --kind service --headline "..." --dry-run`
      renders the post and stays under the limit
- [ ] The bot is an administrator of the channel in `CHANNEL_ID`, and a real post
      arrives - a broadcast nobody saw is a broadcast that does not work
- [ ] New broadcast texts follow `Narrative.md`, section 8, and are covered by
      `tests/presentation/test_broadcast.py`
- [ ] This version has a section in `content/changelog.toml`, written as what a
      player can now do, and `--changelog latest --dry-run` renders it
- [ ] The update is posted **after** the code is live - a changelog announcing
      something nobody can do yet is a bug report from every player at once
- [ ] Group commands answer in one message and stay silent for other bots' traffic
- [ ] `GROUP_ID` names the real group. `*` is for trying the commands out; left in
      production it answers in every group anybody adds the bot to

## 8. Release

- [ ] Documentation updated **in the same commit** as the code
- [ ] An ADR added for any decision a future reader would question
- [ ] Commit messages are conventional and in English
