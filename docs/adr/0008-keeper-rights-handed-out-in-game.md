# ADR 0008 - The keeper right is handed out in game, its root stays in the environment

Status: accepted (2026-08-16)

## Context

Until now the keeper right lived in one place only: `ADMIN_IDS`. That is what made
it safe - nobody could grant it to themselves from inside the game, and it survived
the loss of every table. It also made it inconvenient: adding a keeper meant editing
`.env` and restarting the bot, which is exactly the kind of "stop the game to change
the game" the panel exists to remove (Roadmap 1.11).

The naive fix - a button that writes `characters.is_admin` - would have thrown away
the property that mattered: with it, any keeper could make more keepers, and one
compromised account would multiply.

## Decision

The right has two sources, and they are not equal.

- `ADMIN_IDS` is the **root**. An id there is a keeper always, cannot be stripped of
  it from inside the game, and is the only one who hands the right to somebody else.
- Everything else is **handed out**, from the player card in the panel, and stored on
  the account (`users.keeper`, migration `0011`). Such a keeper has the whole panel
  and no way to pass the right on.

`characters.is_admin` stays what it always was - a mirror of both, rewritten when a
character is loaded by its owner, so a right given or taken lands on the next press.

The panel says nothing about any of this to a keeper who cannot hand the right out:
no line, no button, and the typed-by-hand label answers with the same "press a panel
button" as any label the screen does not know. A door you cannot see is one you do
not try.

## Consequences

- Adding a keeper no longer needs `.env` and a restart; taking one back does not
  either.
- The root right is still un-grantable from inside the game, so the worst a stolen
  keeper account can do is what one keeper can do - not appoint more.
- Two sources means two places to look when somebody "is still a keeper": the setting
  and the account row. `application/services/keeper.py` is the only code that reads
  either, which keeps that answer in one file.
- The right lives on the account, not the character, so a second character cannot be
  used to walk around losing it - the same rule as the black list (migration `0003`).
- An account named by `ADMIN_IDS` refuses to be demoted from the panel rather than
  writing a lie the next load would undo.
