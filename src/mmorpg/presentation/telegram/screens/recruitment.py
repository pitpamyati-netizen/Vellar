"""Доска, выбор цели и осознанное согласие на совместный поход."""

from __future__ import annotations

from mmorpg.application.services.recruitment import Listing
from mmorpg.domain.rules.party import MAX_MEMBERS
from mmorpg.domain.rules.recruitment import PACES, Goal
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId

BOARD = label("Поиск отряда")
CREATE = label("Объявить набор")
OWN = label("Мой набор")
JOIN = label("Вступить в этот отряд")
READY = label("Готов к этому походу")
PAUSE = label("Снять готовность")
CLOSE = label("Закрыть набор")
LEAVE = label("Выйти из этого отряда")
DEPART = label("Перейти к цели похода")
PUBLISH = label("Опубликовать этот набор")
REFRESH = label("Обновить наборы")


def setup(goal: Goal | None, pace: str, low: int, high: int, notice: str = "") -> Screen:
    lines = ["Объявить набор."]
    if notice:
        lines.append(notice)
    if goal:
        lines.extend(
            (
                f"Цель: {goal.name}, уровень {goal.level}.",
                f"Уровни участников: с {low} по {high}.",
                "Изменить уровни: /набор уровни 5 15.",
            )
        )
    rows: list[tuple[Label, ...]] = []
    if not pace:
        lines.append("Выберите темп. Перерывы не тратят ход и не штрафуются.")
        lines.append("Темп: /набор темп 1 — с перерывами; /набор темп 2 — обычный.")
        rows.extend((label(one),) for one in PACES)
    else:
        lines.extend(
            (
                f"Темп: {pace}.",
                "Срок набора — сутки. Истечение не распускает отряд.",
                "Публикация создаст отряд, если вы пока идёте один. Золото не требуется.",
                "Подтвердить: /набор опубликовать.",
            )
        )
        rows.append((PUBLISH,))
    return Screen(ScreenId.RECRUITMENT_SETUP, tuple(lines), tuple(rows))


def card(
    listing: Listing,
    goal: Goal | None,
    city_name: str,
    members: tuple[tuple[int, str], ...],
    actor_id: int,
    *,
    now: int,
    notice: str = "",
) -> Screen:
    lines = ["Набор отряда."]
    if notice:
        lines.append(notice)
    status = "открыт" if listing.status == "open" and now < listing.expires_at else "завершён"
    lines.extend(
        (
            f"Набор {status}.",
            f"Место встречи: {city_name}.",
            f"Цель: {goal.name if goal else 'больше недоступна'}.",
            f"Уровни: с {listing.low} по {listing.high}. Темп: {listing.pace}.",
            f"В отряде: {len(members)} из {MAX_MEMBERS}. Свободно: {MAX_MEMBERS - len(members)}.",
            "Срок открытого набора — сутки; перерыв не отнимает имущество или место.",
            "Вступление: /набор вступить. Готовность: /набор готов; перерыв: /набор пауза.",
        )
    )
    for member_id, name in members:
        lines.append(
            f"{name}: {'готов' if member_id in listing.ready else 'готовность не подтверждена'}."
        )
    rows: list[tuple[Label, ...]] = []
    if actor_id in {one[0] for one in members}:
        rows.extend(((PAUSE if actor_id in listing.ready else READY,), (DEPART,), (LEAVE,)))
        lines.extend(
            (
                "Совместный бой включает готовых участников в названной цели и городе.",
                "Вступление само по себе не переносит вас и не начинает бой.",
                "Переход к цели: /набор идти; выход: /набор выйти.",
                "Блокировка игрока: /набор блокировать Имя. Снять: /набор разрешить Имя.",
            )
        )
    elif status == "открыт" and len(members) < MAX_MEMBERS:
        rows.append((JOIN,))
    if actor_id == listing.leader_id:
        rows.append((CLOSE,))
        lines.append("Закрыть набор: /набор закрыть. Отряд сохранится.")
    rows.extend(((OWN, BOARD), (REFRESH,)))
    return Screen(ScreenId.RECRUITMENT_CARD, tuple(lines), tuple(rows))
