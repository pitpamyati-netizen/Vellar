"""Проверка живости контейнера.

Выход 0, пока сердцебиение цикла событий ещё бьётся (``mmorpg.health``), и 1,
как только удары протухли. Docker превращает несостоявшуюся проверку в
нездоровый контейнер, а политика перезапуска превращает это в перезапуск - в
этом весь смысл: снаружи вставший цикл выглядит точно так же, как здоровый.

Зовётся из ``HEALTHCHECK`` образа; внутри приложения её не зовёт ничто.
"""

from __future__ import annotations

import sys

from mmorpg.config import load_settings
from mmorpg.health import age_seconds


def main() -> int:
    settings = load_settings()
    age = age_seconds(settings.heartbeat_path)

    if age is None:
        print(f"no heartbeat at {settings.heartbeat_path}", file=sys.stderr)
        return 1

    limit = settings.heartbeat_stale_after
    if age > limit:
        print(f"heartbeat is {age:.1f}s old, limit is {limit:.1f}s", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
