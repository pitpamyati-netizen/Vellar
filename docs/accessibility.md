# Accessibility specification

Vellar is played by blind and low-vision players with screen readers. The rules
below are **requirements**, not preferences. Violating any of them is a
blocker-priority bug, and most of them are enforced by tests in
`tests/presentation/test_accessibility.py`.

The audience assumption: a player listens to the last message only, with no ability
to glance at what came before, and navigates the keyboard by position and by memory
of the button label.

## 1. Reply keyboards only

`ReplyKeyboardMarkup` everywhere. `InlineKeyboardMarkup` and `InlineKeyboardButton`
are **forbidden in the entire project**. Inline buttons are attached to a specific
message; a screen reader user has to hunt for the message they belong to, and the
callback answer arrives as a silent toast.

*Enforced by:* a test that scans every module under `src/mmorpg/` for the strings
`InlineKeyboardMarkup`, `InlineKeyboardButton` and `callback_query`.

## 2. Never edit a sent message

`edit_message_text`, `edit_message_reply_markup` and friends are forbidden. Screen
readers do not announce an edit, so the player hears nothing and believes the game
froze. **A new state is always a new message.**

*Enforced by:* a test scanning for `edit_message`.

## 3. Every message is self-sufficient

A player must be able to act after hearing only the newest message. Never rely on
"as mentioned above". Repeat the current health, the current location and the
available actions in every screen that needs them.

## 4. The key fact comes first

```
Line 1: where I am / what just happened
Line 2+: detail
Last:   what I can do now
```

Good: `Combat. Turn 3. Wolf leader: health 68 of 140.`
Bad: `After a long chase through the twilight grove you finally corner...`

## 5. No pseudo-graphics

No `[####----]`, no ASCII tables, no box drawing. A screen reader reads those
character by character.

Good: `Health: 42 of 120, that is 35 percent.`
Bad: `HP [####------] 42/120`

## 6. Emoji never carry meaning alone

Every button label must be unambiguous with all emoji stripped. Emoji are
**off by default** and are toggled in Settings. The renderer builds the label text
first and only then optionally prefixes an emoji.

*Enforced by:* a test that strips emoji from every screen's buttons and asserts the
labels are still non-empty and still unique.

## 7. Stable keyboard layout

A button never moves between refreshes of the same screen. An unavailable action
stays in place and says so in its text:

```
2. Blade whirl - cooldown 2 turns
5. Empty slot
```

Never remove the button, never reorder the rows, never collapse empty slots.

## 8. The last row is always the service row

Every keyboard ends with exactly: `Назад` · `Осмотреться` · `Главное меню`.

`Осмотреться` re-sends the description of the current screen without changing any
state - it is the "say that again, I missed it" button.

*Enforced by:* a test asserting the last row of every screen keyboard.

## 9. Button labels are unique within a screen

Routing is by exact button text, so duplicates are unroutable. Two skills with the
same name are disambiguated by their slot number prefix.

*Enforced by:* a per-screen uniqueness test.

## 10. Text commands duplicate every action

If the keyboard fails to render, the game must stay playable:

| Command | Action |
| --- | --- |
| `/назад` | Same as the Назад button |
| `/осмотреться` | Re-send the current screen |
| `/меню` | Main menu |
| `/бой атака` | Basic attack in combat |
| `/умение 3` | Use skill slot 3 |
| `/страница 4` | Jump to page 4 of a paginated list |

## 11. Message length up to about 900 characters

Longer content is paginated with an explicit page indicator, never split into
several consecutive messages. One action produces exactly one message.

## 12. Stale keyboards are answered, never ignored

Reply keyboards persist in the chat history, so the player can press a button that
belonged to an old screen. The resolver compares the pressed text against the
current FSM state and, on a mismatch, answers:

```
Это действие сейчас недоступно, вы находитесь в: Город Дальний Оплот.
```

together with the current keyboard. Never stay silent, never raise.

## 13. No real-time timers in combat

Combat is turn-based and waits for the player indefinitely. Nothing in the game
expires while the player is reading.

## 14. `parse_mode=None`

Markdown asterisks and underscores are spoken aloud by screen readers. The bot
sends plain text. If markup is ever needed, HTML only - never Markdown.

*Enforced by:* a test asserting the bot's default `parse_mode` is `None`.

## Review checklist

Run through this before merging any change that touches `presentation/`:

- [ ] No `InlineKeyboard*`, no `callback_query`, no `edit_message*` added
- [ ] New screen ends with the service row `Назад · Осмотреться · Главное меню`
- [ ] Button labels unique within the screen, and still unique without emoji
- [ ] Button positions unchanged between refreshes; unavailable actions kept in place with a text reason
- [ ] First line answers "where am I / what happened"
- [ ] Numbers spelled out as `X of Y`, no bars, no tables
- [ ] Message body under ~900 characters, otherwise paginated
- [ ] `Осмотреться` re-sends this screen without changing state
- [ ] A text command exists for every new action
- [ ] Pressing the new screen's buttons from a different state produces the "action unavailable" answer
- [ ] `parse_mode` left at `None`
