"""Post one broadcast to the game channel from the command line.

This is the tool that proves the channel works end to end - token, admin rights,
chat id - without waiting for a player to do something newsworthy:

    uv run python scripts/broadcast.py --kind service --headline "Проверка связи." --dry-run
    uv run python scripts/broadcast.py --kind service --headline "Проверка связи."

An update is not typed here: it is written in ``content/changelog.toml`` and
posted from there, so what players read is what the file says.

    uv run python scripts/broadcast.py --changelog latest --dry-run
    uv run python scripts/broadcast.py --changelog 0.2

``--dry-run`` renders and validates the post and sends nothing, so it is safe to
run against a live channel. Without it the post is public and permanent - the
channel is the game's log, and a deleted post is still a post players saw.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from mmorpg.config import Settings, load_settings
from mmorpg.infrastructure.content import ContentError
from mmorpg.infrastructure.content.changelog import LATEST, load_changelog, select_release
from mmorpg.presentation.telegram.broadcast import (
    BroadcastEvent,
    BroadcastKind,
    ChannelBroadcaster,
    changelog,
    render_broadcast,
)


def use_utf8_console() -> None:
    """The Windows console defaults to cp1251, which cannot print the post."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post one message to the game channel.")
    parser.add_argument("--kind", choices=[kind.value for kind in BroadcastKind])
    parser.add_argument("--headline", help="First line. It must stand alone.")
    parser.add_argument("--detail", action="append", default=[], help="Extra line; repeatable.")
    parser.add_argument(
        "--changelog",
        nargs="?",
        const=LATEST,
        metavar="VERSION",
        help=f"Post an update from content/changelog.toml: a version, or {LATEST!r}.",
    )
    parser.add_argument("--no-emoji", action="store_true", help="Render without the leading emoji.")
    parser.add_argument("--dry-run", action="store_true", help="Render and validate, send nothing.")
    args = parser.parse_args(argv)

    if bool(args.headline) == bool(args.changelog):
        parser.error("give either --headline or --changelog, not both and not neither")
    if args.headline and not args.kind:
        parser.error("--kind is required with --headline")
    if args.changelog and (args.kind or args.detail):
        parser.error("--changelog carries its own kind and lines")
    return args


def build_event(args: argparse.Namespace, settings: Settings) -> BroadcastEvent:
    """Turn the arguments into the one post this run will make."""
    if args.changelog:
        release = select_release(load_changelog(settings.content_dir), args.changelog)
        return changelog(
            release.version,
            added=release.added,
            changed=release.changed,
            fixed=release.fixed,
        )
    return BroadcastEvent(
        kind=BroadcastKind(args.kind),
        headline=args.headline,
        details=tuple(args.detail),
    )


async def _send(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        event = build_event(args, settings)
        text = render_broadcast(event, emoji=not args.no_emoji)
    # A wrong version or a post written for the team is an author's mistake, not
    # a crash: say what is wrong and stop before Telegram is involved.
    except (ContentError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    print(text)

    if args.dry_run:
        print("--- dry run, nothing sent ---", file=sys.stderr)
        return 0
    if not settings.broadcasts_enabled:
        print("CHANNEL_ID is not set in .env - nowhere to post.", file=sys.stderr)
        return 1
    if not settings.bot_token.get_secret_value():
        print("BOT_TOKEN is not set in .env.", file=sys.stderr)
        return 1

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=None),
    )
    broadcaster = ChannelBroadcaster(sink=bot, chat_id=settings.channel_id, emoji=not args.no_emoji)
    try:
        sent = await broadcaster.announce(event)
    finally:
        await bot.session.close()

    if not sent:
        print("Telegram refused the post - see the log line above.", file=sys.stderr)
        return 1
    print(f"--- sent to {settings.channel_id} ---", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    return asyncio.run(_send(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
