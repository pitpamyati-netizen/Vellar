# ADR 0002 - Reply keyboards only, no message editing

Status: accepted (2026-08-12)

## Context

The primary audience is blind players using screen readers. Inline keyboards attach
buttons to a specific message, which forces the user to locate that message in the
history before acting, and callback answers surface as toasts that are often not
announced. Editing a message changes text that a screen reader has already read and
will not re-read, so the player perceives a frozen game.

## Decision

`ReplyKeyboardMarkup` is the only markup type in the project. `edit_message_*` is
never called. Every state change produces a new message. Routing is by exact button
text. Every screen ends with the same service row, and every action has a text
command duplicate.

## Consequences

- Button text is part of the routing contract: labels must be unique within a screen
  and stable across releases; renaming a button is a routing change.
- Old keyboards stay in the chat, so a stale-button resolver must answer every
  unexpected text with the current screen instead of failing.
- No progress bars, no live-updating messages, no timers.
- Enforced mechanically by `tests/presentation/test_accessibility.py`; see
  `docs/accessibility.md`.
