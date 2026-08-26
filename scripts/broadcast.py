"""Опубликовать один пост в канал игры из командной строки.

Это тот инструмент, который доказывает, что канал работает от начала до конца —
токен, права администратора, id чата, — не дожидаясь, пока игрок сделает
что-нибудь достойное новости:

    uv run python scripts/broadcast.py --kind service --headline "Проверка связи." --dry-run
    uv run python scripts/broadcast.py --kind service --headline "Проверка связи."

Обновление здесь не набирают: его пишут в ``content/changelog.toml`` и оттуда же
публикуют, чтобы игроки прочитали именно то, что сказано в файле.

    uv run python scripts/broadcast.py --changelog latest --dry-run
    uv run python scripts/broadcast.py --changelog 0.2

``--dry-run`` рисует и проверяет пост и не отправляет ничего, поэтому его
безопасно запускать и против живого канала. Без него пост публичен и постоянен:
канал — это летопись игры, а удалённый пост всё равно остаётся постом, который
игроки видели.

Поэтому ``latest`` значит «то, что в игре сейчас», а не «последняя строка в
файле»: если игру меняли после того, как файл дописали, пост не уходит вовсе и
говорит, чего в нём не хватает. Назвать версию числом по-прежнему можно всегда -
тогда выбор сделали вы, а не слово ``latest``.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from mmorpg.config import Settings, load_settings
from mmorpg.infrastructure.content import ContentError
from mmorpg.infrastructure.content.changelog import (
    LATEST,
    load_changelog,
    select_release,
    unannounced_changes,
)
from mmorpg.presentation.telegram.broadcast import (
    BroadcastEvent,
    BroadcastKind,
    ChannelBroadcaster,
    changelog,
    render_broadcast,
)


def use_utf8_console() -> None:
    """Консоль Windows по умолчанию в cp1251, а она такой пост не напечатает."""
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


def stale_complaint(project_root: Path, version: str) -> str | None:
    """Сказать, чем самая свежая запись отстала от игры, - или промолчать, если не отстала."""
    behind = unannounced_changes(project_root)
    if not behind:
        # Пустой кортеж - файл свежий; None - git не ответил, и выдумывать за него нечего.
        return None
    listed = "\n  ".join(behind)
    return (
        f"content/changelog.toml is behind the game: the newest release it lists is {version}, "
        f"but the game changed after it:\n  {listed}\n"
        f"Write those changes into a release, or name the version you meant: --changelog {version}"
    )


def build_event(args: argparse.Namespace, settings: Settings) -> BroadcastEvent:
    """Превратить аргументы в тот единственный пост, который сделает этот запуск."""
    if args.changelog:
        release = select_release(load_changelog(settings.content_dir), args.changelog)
        if args.changelog == LATEST:
            complaint = stale_complaint(settings.content_dir.parent, release.version)
            if complaint:
                raise ValueError(complaint)
        return changelog(
            release.version,
            headline=release.headline,
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
    # Неверная версия или пост, написанный для своих, - ошибка автора, а не падение:
    # сказать, что не так, и остановиться до того, как дело дойдёт до Telegram.
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
