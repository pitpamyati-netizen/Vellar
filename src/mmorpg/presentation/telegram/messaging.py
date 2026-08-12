"""Sending screens.

One player action produces exactly **one** new message (accessibility rule 3 and
the latency budget). Messages are never edited and never split into a burst -
if a body genuinely cannot fit, it is paged and the player asks for the next page.

``parse_mode`` is ``None`` everywhere: Markdown asterisks and underscores are read
aloud by screen readers (rule 14).
"""

from __future__ import annotations

from aiogram.types import Message

from mmorpg.presentation.telegram.keyboards.reply import keyboard_for
from mmorpg.presentation.telegram.screens.base import Screen


async def send_screen(message: Message, screen: Screen, *, emoji: bool = False) -> None:
    """Send a screen as a single new message with its keyboard attached."""
    await message.answer(
        text=screen.pages()[0],
        reply_markup=keyboard_for(screen, emoji=emoji),
        parse_mode=None,
    )


async def send_text(message: Message, text: str, screen: Screen, *, emoji: bool = False) -> None:
    """Send a one-off answer that still carries the current keyboard.

    Used for stale buttons: the player always gets an explanation *and* the
    buttons that actually work right now (rule 12).
    """
    await message.answer(
        text=text,
        reply_markup=keyboard_for(screen, emoji=emoji),
        parse_mode=None,
    )
