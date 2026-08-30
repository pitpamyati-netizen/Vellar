"""Что панель смотрителя делает с хранилищами.

Экран решает, что должно случиться; здесь это случается. Разделение то же, что у
всей игры (``Claude.md``, правило 5), и здесь оно важнее обычного: каждая функция
ниже что-нибудь необратимо стирает, и такое должно лежать в одном месте, где это
видно целиком, а не растекаться по веткам автомата.

Все функции возвращают числа, а не фразы. Фразу составляет экран
(``presentation/telegram/screens/keeper.py``): числа одинаковы в любом языке, а
слова у игры свои.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mmorpg.application.services.content import ContentRegistry
from mmorpg.domain.entities.overlay import OverlayKind, OverlayRecord
from mmorpg.domain.ports.repositories import (
    CharacterRepository,
    ContentOverlayRepository,
    UserRepository,
)
from mmorpg.domain.rules import overlay as overlay_rules

#: Сколько суток персонаж должен пролежать нетронутым, чтобы считаться брошенным.
#: Неделя: за неделю возвращается тот, кто вообще собирался вернуться.
ABANDONED_AFTER_DAYS = 7

#: Ключ и срок флага режима обслуживания (ADR 0045). Со сроком, как всё в кэше:
#: забытый стоп-кран снимется сам, а нажать его заново — одно движение.
MAINTENANCE_KEY = "keeper:maintenance"
MAINTENANCE_TTL = 3600

#: Сколько живёт просьба сбросить экран игрока: он либо зайдёт и сбросится, либо
#: и так давно не заходил.
PLAYER_RESET_TTL = 600


def player_reset_key(user_id: int) -> str:
    """Ключ просьбы сбросить сохранённый экран игрока (ADR 0045)."""
    return f"keeper:reset:{user_id}"


#: Сколько аккаунтов проверяется за одно нажатие. Каждая проверка — обращение к
#: Telegram, а у Telegram есть счёт обращениям; порция маленькая нарочно, кнопку
#: можно нажать ещё раз.
SWEEP_BATCH = 40

#: Как часто имеет смысл спрашивать один и тот же аккаунт заново.
RECHECK_AFTER_DAYS = 3

DAY = 24 * 60 * 60

#: Проверка одного аккаунта: правда — бот ему всё ещё может писать. Функцию
#: передаёт хендлер, потому что она разговаривает с Telegram, а этот слой — нет.
Probe = Callable[[int], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Sweep:
    """Итог одной уборки, числами."""

    checked: int = 0
    blocked: int = 0
    removed: int = 0


async def sweep_drafts(characters: CharacterRepository, *, now: int) -> Sweep:
    """Убрать персонажей, которых завели и бросили, не начав играть."""
    removed = await characters.purge_abandoned(before=now - ABANDONED_AFTER_DAYS * DAY)
    return Sweep(removed=removed)


async def sweep_blocked(users: UserRepository, probe: Probe, *, now: int) -> Sweep:
    """Спросить у Telegram про порцию аккаунтов, кто ещё читает бота.

    Узнать это иначе нельзя: человек, заблокировавший бота, ничего боту больше не
    скажет, и в базе он ничем не отличается от того, кто просто не заходил.
    """
    candidates = await users.unchecked(limit=SWEEP_BATCH, before=now - RECHECK_AFTER_DAYS * DAY)
    blocked = 0
    for telegram_id in candidates:
        reachable = await probe(telegram_id)
        await users.mark_checked(telegram_id, at=now, blocked=not reachable)
        blocked += 0 if reachable else 1
    return Sweep(checked=len(candidates), blocked=blocked)


async def drop_blocked(users: UserRepository) -> Sweep:
    """Убрать заблокировавших вместе со всем, что им принадлежало."""
    return Sweep(removed=await users.purge_blocked())


async def save_edit(
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
    record: OverlayRecord,
) -> tuple[str, ...]:
    """Записать правку и пересобрать мир. Возвращает, почему она пока не работает.

    Запись идёт всегда, даже с отказами: недозаполненная правка — это работа на
    середине, а не ошибка. Мир её просто не показывает, пока она не дописана.
    """
    await overlays.put(record)
    await registry.reload(overlays)
    return overlay_rules.problems(registry.current, record)


async def drop_edit(
    overlays: ContentOverlayRepository,
    registry: ContentRegistry,
    kind: OverlayKind,
    entity_id: str,
) -> bool:
    """Снять правку целиком. Мир возвращается к тому, что записано в ``content/``."""
    dropped = await overlays.forget(kind, entity_id)
    if dropped:
        await registry.reload(overlays)
    return dropped
