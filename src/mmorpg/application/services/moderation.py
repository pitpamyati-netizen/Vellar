"""Блокировка аккаунта и журнал смотрителя.

Экран решает, что должно случиться; здесь это случается — то же разделение, что
у остальной панели (``Claude.md``, правило 5). Отдельно от ``keeper_panel.py``
это лежит потому, что там всё стирает, а здесь ничего не стирается: блокировка —
пауза, и снимается она одним нажатием.

Каждая блокировка и каждое её снятие пишутся в журнал прямо здесь, а не
вызывающим: смотрителей больше одного (право раздаётся из панели, ADR 0008), и
действие, о котором можно забыть записать, однажды окажется незаписанным.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.moderation import Ban, KeeperAction, KeeperEntry
from mmorpg.domain.ports.repositories import KeeperLogRepository, UserRepository
from mmorpg.domain.rules import moderation as rules


async def standing(users: UserRepository, telegram_id: int, *, now: int) -> Ban:
    """Что сейчас висит на аккаунте. Истёкший срок отвечает «ничего».

    Истёкшую блокировку никто не снимает: она просто перестаёт действовать, и
    строка в базе доживает до следующей. Так проверка стоит одного чтения и не
    требует ни задачи по расписанию, ни часов в домене.
    """
    user = await users.get(telegram_id)
    if user is None or not rules.is_banned(user.ban, now=now):
        return Ban()
    return user.ban


async def set_ban(
    users: UserRepository,
    log: KeeperLogRepository,
    telegram_id: int,
    ban: Ban,
    *,
    by: KeeperEntry,
    target: str,
) -> None:
    """Наложить блокировку или снять её, и записать это.

    ``by`` — заготовка строки журнала с именем и моментом: кто это делает, знает
    хендлер, а не панель.
    """
    await users.set_ban(telegram_id, ban)
    action = KeeperAction.BAN if ban.until else KeeperAction.UNBAN
    await log.record(replace(by, action=action, target=target, detail=ban.reason))


async def note(log: KeeperLogRepository, entry: KeeperEntry) -> None:
    """Записать в журнал одно действие смотрителя."""
    await log.record(entry)
