"""Ограничение потока по игрокам, для группы.

Группа публична, поэтому всё, что бот там говорит, сказано всем. Это делает наплыв
самым дешёвым нападением из возможных: горсть ответов «профиль» в секунду — и чат
нечитаем, а того, кто слушает экранный диктор, это бьёт куда сильнее, чем зрячего,
прокручивающего мимо.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

# Шесть команд в минуту - куда больше, чем нужно разговору, и куда меньше наплыва:
# игрок, ответивший на предложение и заглянувший в две карточки, остаётся внутри.
RATE_LIMIT = 6
RATE_WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class RateLimiter:
    """Ограничитель со скользящим окном и одним предупреждением на окно."""

    limit: int = RATE_LIMIT
    window: float = RATE_WINDOW_SECONDS
    clock: Callable[[], float] = time.monotonic
    _hits: dict[int, deque[float]] = field(default_factory=dict)
    _warned: set[int] = field(default_factory=set)
    _swept_at: float = 0.0

    def allow(self, user_id: int) -> bool:
        """Записать одну команду. ``False`` значит, что действовать по ней нельзя."""
        now = self.clock()
        self._sweep(now)
        hits = self._hits.setdefault(user_id, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            return False

        hits.append(now)
        self._warned.discard(user_id)
        return True

    def should_warn(self, user_id: int) -> bool:
        """Первый ли это отказ в окне, а значит, стоит ли о нём говорить."""
        if user_id in self._warned:
            return False
        self._warned.add(user_id)
        return True

    def forget(self, user_id: int) -> None:
        """Забыть историю игрока. Берётся тестами и модерацией."""
        self._hits.pop(user_id, None)
        self._warned.discard(user_id)

    def _sweep(self, now: float) -> None:
        """Забыть всех, чьё окно прошло.

        Без этого бот держал бы по очереди на каждый когда-либо виденный аккаунт, и
        группа, живущая месяцами, платила бы за каждого захожего, сказавшего одно
        слово.
        """
        if now - self._swept_at < self.window:
            return
        self._swept_at = now
        stale = [
            user_id
            for user_id, hits in self._hits.items()
            if not hits or now - hits[-1] >= self.window
        ]
        for user_id in stale:
            self.forget(user_id)
