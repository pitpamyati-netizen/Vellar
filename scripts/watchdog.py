"""Кто скажет, что игра встала, если она встала молча.

Сердцебиение (``mmorpg.health``) видно снаружи процесса: живой цикл событий
трогает файл, вставший — перестаёт. В контейнере это читает проба Docker и
перезапускает бота. Без контейнеров (ADR 0010) читать некому: процесс держит
консоль и выглядит живым, а игроки в это время нажимают кнопки в пустоту.

Отсюда сторож. Он не в игре нарочно: смысл проверки в том, что она переживёт
зависание того, кого проверяет. Запускается по расписанию (``-Schedule`` у
``scripts/backup.ps1`` рядом, или Планировщик заданий), смотрит на возраст удара
и, если тот просрочен, пишет об этом смотрителям — тем, чьи id стоят в
``ADMIN_IDS``.

    uv run python scripts/watchdog.py           проверить и написать, если плохо
    uv run python scripts/watchdog.py --quiet   только код возврата, без письма

Код возврата: 0 — бьётся, 1 — просрочено или удара не было вовсе. Этого хватает,
чтобы повесить сторожа на что угодно, что умеет запускать команду.

Письмо уходит прямым запросом к Telegram, без aiogram и без игры: сторож должен
работать и тогда, когда собрать приложение уже нельзя.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from mmorpg.config import Settings, load_settings
from mmorpg.health import age_seconds

#: Сколько ждать ответа Telegram. Сторож живёт секунды, а не минуты: он либо
#: успел сказать, либо скажет в следующий раз.
TIMEOUT_SECONDS = 10


def verdict(settings: Settings) -> tuple[bool, str]:
    """Бьётся ли сердце и что об этом сказать одной строкой."""
    age = age_seconds(settings.heartbeat_path)
    if age is None:
        return False, (
            f"Vellar: удара сердца нет вовсе ({settings.heartbeat_path}). Похоже, игра не запущена."
        )
    limit = settings.heartbeat_stale_after
    if age > limit:
        return False, (
            f"Vellar: игра не отвечает. Последний удар был {age:.0f} секунд назад, "
            f"а больше {limit:.0f} — уже не норма. Перезапустите: Start.bat."
        )
    return True, f"Vellar: жива, последний удар {age:.0f} секунд назад."


def tell(settings: Settings, text: str) -> int:
    """Сказать смотрителям. Возвращает, скольким сказали."""
    token = settings.bot_token.get_secret_value()
    if not token or not settings.admins:
        return 0
    told = 0
    for admin in sorted(settings.admins):
        payload = json.dumps({"chat_id": admin, "text": text}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
                told += 1 if answer.status == 200 else 0
        except (urllib.error.URLError, TimeoutError, OSError) as unreachable:
            # Сторож, упавший на том, что Telegram не ответил, — это второй
            # сломавшийся, а не сообщение о первом.
            print(f"не удалось написать {admin}: {unreachable}", file=sys.stderr)
    return told


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Жива ли игра, и сказать, если нет.")
    parser.add_argument("--quiet", action="store_true", help="ничего не писать, только код")
    options = parser.parse_args(argv)

    settings = load_settings()
    alive, said = verdict(settings)
    print(said)
    if alive or options.quiet:
        return 0 if alive else 1

    told = tell(settings, said)
    if not told:
        print(
            "написать было некому: пустой BOT_TOKEN или пустой ADMIN_IDS",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    # Ни одного await: сторож — это одна проверка и одно письмо, и запускается он
    # ровно тогда, когда цикл событий игры уже не крутится.
    raise SystemExit(main())
