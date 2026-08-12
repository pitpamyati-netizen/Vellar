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

## 4. Determinism

- [ ] `tests/domain/test_procgen.py` green - a change that makes generation
      non-reproducible breaks every player's map mid-cycle
- [ ] No new use of the global `random` module anywhere

## 5. Data and migrations

- [ ] New columns have a migration in `migrations/versions/`
- [ ] Nothing derived was added to a table: totals are recomputed, not stored
- [ ] Redis keys carry a TTL

## 6. Runtime

- [ ] `APP_ENV=local uv run python -m mmorpg.main` starts and plays
- [ ] `docker compose up -d` brings up postgres, redis and the bot
- [ ] Startup logs show `content_loaded` with the expected counts
- [ ] No `slow_operation` or asyncio slow-callback warnings under normal play

## 7. Release

- [ ] Documentation updated **in the same commit** as the code
- [ ] An ADR added for any decision a future reader would question
- [ ] Commit messages are conventional and in English
