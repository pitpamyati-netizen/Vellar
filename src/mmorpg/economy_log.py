"""Откуда золото приходит в игру и куда уходит из неё.

Три числа, на которых стоит баланс этой игры, за столом не решаются: пошлина со
сделки между игроками, ставка и выплата арены и то, чего стоит бой. Все три были
записаны догадками, которые надо поправить «по первым суткам ОБТ», — и в игре не
было ничего, что сделало бы те двадцать четыре часа читаемыми. Число, которое
некому измерить, не настраивают, его переугадывают.

Поэтому каждое движение золота, кроме передачи монеты из рук в руки, пишет одну
строку:

    gold_flow flow=fight amount=124 character_id=17

``flow`` — что случилось, ``amount`` — со знаком: плюс, когда золото приходит в
кошелёк этого персонажа, минус, когда уходит. Сложенное за сутки по видам, это и
есть вся экономика: что мир выплачивает, что города забирают обратно, что убирает
пошлина и дыра арена или фонтан.

Строка журнала — это журнал и больше ничего: её читает только `scripts/economy.py`,
сложив за сутки по видам. Но у неё есть второй приёмник (ADR 0044): `use_sink`
подключает запись той же строки в таблицу `gold_flow`, чтобы смотритель видел
срез по одному игроку на его карточке. Приёмник ставит `main.py`; в `local` и в
тестах его нет, и пишется только журнальная строка. Ошибку приёмника глотаем:
терять строку журнала можно, а ронять из-за неё нажатие игрока — нет.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mmorpg.logging import get_logger

logger = get_logger(__name__)

#: Приёмник: (flow, amount, character_id, detail) -> запись куда-то ещё. Момент
#: приёмник ставит сам — модуль остаётся без явной подстановки времени в вызовах.
Sink = Callable[[str, int, int, str], Awaitable[None]]

_sink: Sink | None = None
#: Держим ссылки на запущенные задачи, иначе сборщик мусора отменит их на лету.
_pending: set[asyncio.Task[None]] = set()


def use_sink(sink: Sink | None) -> None:
    """Подключить второй приёмник денежного журнала или снять его (ADR 0044)."""
    global _sink
    _sink = sink


# Потоки, которые стоит различать. Чего здесь нет, то не измеряется, а это решение
# принимают нарочно, а не по забывчивости.
FIGHT = "fight"  # что нёс противник
SEARCH = "search"  # тайник, святилище, тихий узел
DESCENT = "descent"  # дно вылазки
QUEST = "quest"  # плата по заданию
DEFEAT = "defeat"  # десятая часть кошелька, оставшаяся там, где бой проигран
DUEL = "duel"  # взято у другого игрока или потеряно ему
ARENA_STAKE = "arena_stake"  # на арену
ARENA_PAYOUT = "arena_payout"  # и обратно с неё
TRADE_PRICE = "trade_price"  # что один игрок заплатил другому
TRADE_DUTY = "trade_duty"  # что пошлина вынула из игры
TRADE_ROLLBACK = "trade_rollback"  # закрытая сделка, которую откатил смотритель
SHOP = "shop"  # куплено у города или продано ему
SERVICE = "service"  # ночлег, учитель, грамота гильдии, всё, за что берёт город
TUTORIAL = "tutorial"  # награда за шаг обучения; в мир, не из города
DIGEST = "digest"  # надбавка за дело со сводки заставы (ADR 0053); в мир, не из города
GUILD_VAULT = "guild_vault"  # положено в казну гильдии или взято из неё
KEEPER = "keeper"  # выдано смотрителем, а потому вовсе не экономика


def record(flow: str, amount: int, *, character_id: int, detail: str = "") -> None:
    """Записать одно движение золота. Ноль движением не считается."""
    if not amount:
        return
    logger.info(
        "gold_flow",
        flow=flow,
        amount=amount,
        character_id=character_id,
        detail=detail,
    )
    _emit(flow, amount, character_id, detail)


def _emit(flow: str, amount: int, character_id: int, detail: str) -> None:
    """Отдать событие второму приёмнику, не дожидаясь его и не падая из-за него."""
    if _sink is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Нет петли — синхронный скрипт или тест: писать некуда, и это нормально.
        return
    task = loop.create_task(_guarded(_sink(flow, amount, character_id, detail)))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _guarded(write: Awaitable[None]) -> None:
    try:
        await write
    except Exception as err:
        logger.warning("gold_flow_sink_failed", error=str(err))
