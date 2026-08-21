# Accessibility specification

Vellar is played **entirely by ear**: everything the game knows how to say, it says
in text, and the whole of it has to work through a screen reader. The rules below
are **requirements**, not preferences. Violating any of them is a blocker-priority
bug, and most of them are enforced by tests in
`tests/presentation/test_accessibility.py`.

The design assumption is about the medium, not about the audience. Vellar is played
by sighted and blind players alike, and the game never says which it is talking to:
a screen that is clear by ear is clear on a screen too, so there is exactly one
version of every message and no "accessible mode" to switch on. What the player
hears about is Vellar; what the player never hears about is their own eyesight.

Worst-case reader, and the one every rule is written for: a player who has only the
last message, cannot glance back at what came before, and navigates the keyboard by
position and by memory of the button label.

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

### The one exception: a new level

One action answers with one message, everywhere but here. A level taken is
announced in **its own second message**, sent right after the screen and carrying
the same keyboard, so the player has not moved anywhere
(`screens/play.level_up_report`).

It earns the exception by weight: a level brings stat points, skill points, more
health, sometimes a new skill and sometimes a whole city, and as one line inside a
victory report it sat between the loot and the health and was regularly missed
entirely. The screen it follows says nothing about the level - the game does not
say the same thing twice.

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
```

Never reorder the rows. What keeps a panel learnable is the **number**, not the
row count: skill three is button "3." whether or not one and two are filled.

An action that does not exist is not a button. "5. Пустой слот" was drawn for
months and answered every press with "there is nothing here" - and in a fight it
answered by spending the player's whole turn on nothing while every enemy struck
back. A button that cannot do anything is removed; a button that can do something
later stays and says what it is waiting for.

The rule is about the actions of a screen, not about its machinery. A paginated
list drops its paging row when there is only one page and its filter row when
there is nothing to filter: those rows sit *below* the entries, so no entry ever
moves, and a bag of three things is opened to reach the things.

The same goes for a direction that leads nowhere. On the last page there is no
`Следующая страница` and on the first there is no `Предыдущая`: a button whose
whole answer is "вы уже в конце" spends a press to say nothing, and by ear that
is indistinguishable from a bot that has stopped answering. `Сбросить фильтры`
appears only once something is actually filtered, and `Фильтры` only on a list
that has sections to cut it by.

A list long enough to page is long enough to search. `Поиск` asks for a word in
one message and matches it against both the name and the description of every
entry - a player looks for "уклонение", not for "Кошачья поступь"
(`screens/paginated.py`).

## 8. The last row is always the service row

Every keyboard ends with exactly: `Назад` · `Главное меню`.

**One exception: the main menu.** It is the root, so `Назад` led back to the main
menu and `Главное меню` led to where the player already stood - two buttons that
did nothing, on the most-heard screen in the game (rule 9 of `Claude.md`). Both
commands still work from there, and so does a `Назад` pressed off an older
keyboard.

There is no "look around" button. Telegram keeps every message the bot has sent,
so re-reading the current screen costs a scroll, not a press; a third button on
every screen only added noise. The command `/осмотреться` still re-sends the
current screen for anyone who wants it as a fresh message.

**Coming back to a screen unwinds the walk, it does not stack on it.** After a
skill is put into a slot the player lands back on «Слоты умений», and `Назад`
there must lead to «Умения» - not to the very pick screen that just ended, which
reads as an empty slot holding a skill (`NavigationStack.push`).

*Enforced by:* a test asserting the last row of every screen keyboard, and a flow
test walking the panel there and back.

## 9. Button labels are unique within a screen

Routing is by exact button text, so duplicates are unroutable. Two skills with the
same name are disambiguated by their slot number prefix.

*Enforced by:* a per-screen uniqueness test.

## 10. Text commands duplicate every action

If the keyboard fails to render, the game must stay playable:

| Command | Action |
| --- | --- |
| `/назад` | Same as the Назад button |
| `/осмотреться` | Re-send the current screen (no button for it) |
| `/меню` | Main menu |
| `/бой атака` | Basic attack in combat |
| `/умение 3` | Use skill slot 3 |
| `/страница 4` | Jump to page 4 of a paginated list |
| `/поиск` | Ask for a word to search the current list by |
| `/фильтры` | Open the sections of the current list |
| `/сбросить` | Drop the search and the section |

## 11. Message length up to about 900 characters

Longer content is paginated with an explicit page indicator, never split into
several consecutive messages. One action produces exactly one message - the single
exception is a level taken, which gets a second one of its own (rule 3).

**A page holds what fits, not eight entries.** Eight is a ceiling
(`paginated.PAGE_SIZE`); the number that actually goes on a page is computed from
the longest entry in the list (`entries_per_page`), so a skill list whose entries
run to two hundred characters is cut to three per page. It is one number for the
whole list - «the fourth skill» must be the same skill on every page (rule 7).

The list is where the cutting happens, and nowhere else: sending used to take
`pages()[0]`, the first nine hundred characters, and drop the rest without a
word. A skills page carried eight buttons and five descriptions. Sending now
hands over the whole body (`Screen.body()`).

## 12. Stale keyboards are answered, never ignored

Reply keyboards persist in the chat history, so the player can press a button that
belonged to an old screen. The resolver compares the pressed text against the
current FSM state and, on a mismatch, answers:

```
Действие сейчас недоступно, вы находитесь в: Город Дубно.
```

together with the current keyboard. Never stay silent, never raise.

## 13. No real-time timers in PvE

Combat against the world is turn-based and waits for the player indefinitely.
Nothing in a location, a dungeon or a quest expires while the player is reading.

This holds for fighting other players too. An attack on a free location is fought
against a **snapshot** of the other character, driven by the ordinary engine: the
attacker plays at their own pace, the defender is told by message what happened.
Neither side ever waits on a clock, and neither side has to be online.

## 14. `parse_mode=None`

Markdown asterisks and underscores are spoken aloud by screen readers. The bot
sends plain text. If markup is ever needed, HTML only - never Markdown.

*Enforced by:* a test asserting the bot's default `parse_mode` is `None`.

## 15. The group is not a screen

Rules 3, 8 and 10 describe a private chat, where a message is a place the player
stands in. The game group is a conversation, and three things differ there:

- **no service row.** There is nowhere to go "Назад" to, so offering it would lie.
- **the bot's messages are deleted after five minutes.** Nothing in a private chat
  is ever deleted: there the message *is* the screen, and the player re-reads it
  for as long as they need.
- **the keyboard is `selective`.** An offer's two buttons are shown to its target
  alone, and each button's text is exactly the command it duplicates, so pressing
  and typing are the same act (rule 10). Visibility is a convenience, not a
  permission check - a stranger's press is refused by the handler either way.

Silence is the default: anything the bot is not clearly addressed in goes
unanswered. See `Narrative.md`, section 9.

## Review checklist

Run through this before merging any change that touches `presentation/`:

- [ ] No `InlineKeyboard*`, no `callback_query`, no `edit_message*` added
- [ ] New screen ends with the service row `Назад · Главное меню`
- [ ] Button labels unique within the screen, and still unique without emoji
- [ ] Button positions unchanged between refreshes; unavailable actions kept in place with a text reason
- [ ] First line answers "where am I / what happened"
- [ ] Numbers spelled out as `X of Y`, no bars, no tables
- [ ] Message body under ~900 characters, otherwise paginated
- [ ] `/осмотреться` re-sends this screen without changing state
- [ ] A text command exists for every new action
- [ ] A new list offers `Поиск`, and every button on its machinery rows actually does something
- [ ] Pressing the new screen's buttons from a different state produces the "action unavailable" answer
- [ ] `parse_mode` left at `None`
- [ ] Anything new in the group: no service row, deleted after five minutes, and silent unless addressed
