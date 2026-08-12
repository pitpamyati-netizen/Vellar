"""Container liveness probe.

Exit 0 while the bot's event loop is still beating (``mmorpg.health``), 1 once the
beat has gone stale. Docker turns a failing probe into an unhealthy container, and
the restart policy turns that into a restart - which is the whole point: a wedged
loop looks identical to a healthy one from the outside.

Run by the image's ``HEALTHCHECK``; nothing in the application calls it.
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
