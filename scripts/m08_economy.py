"""Спрос, предложение и экономические записи из одного снимка PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
from pydantic import TypeAdapter

from mmorpg.config import Settings
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.item_instance import template_id
from mmorpg.domain.entities.market import MarketState
from mmorpg.domain.rules import economy
from mmorpg.infrastructure.content import load_content

MOVEMENTS_SQL = """
WITH operation_delta AS (
  SELECT e.operation_id, o.kind, e.resource, e.item_id, sum(e.amount)::bigint AS delta
  FROM economic_entries e JOIN economic_operations o ON o.id=e.operation_id
  WHERE o.completed AND ($1::double precision=0 OR
    o.created_at >= transaction_timestamp() - $1::double precision * interval '1 hour')
  GROUP BY e.operation_id, o.kind, e.resource, e.item_id
), sources AS (
  SELECT f.operation_id, string_agg(DISTINCT f.flow, ', ' ORDER BY f.flow) AS flows
  FROM gold_flow f JOIN (SELECT DISTINCT operation_id FROM operation_delta) d
    ON d.operation_id=f.operation_id
  GROUP BY f.operation_id
)
SELECT d.kind, coalesce(s.flows, '') AS flows, d.resource, d.item_id,
       sum(greatest(d.delta, 0))::bigint AS created,
       sum(greatest(-d.delta, 0))::bigint AS consumed,
       count(*) FILTER (WHERE d.delta=0)::bigint AS transfers,
       count(*)::bigint AS operations
FROM operation_delta d LEFT JOIN sources s ON s.operation_id=d.operation_id
GROUP BY d.kind, s.flows, d.resource, d.item_id
ORDER BY d.resource, d.item_id, d.kind, s.flows
"""


@dataclass(frozen=True)
class Movement:
    kind: str
    flows: str
    resource: str
    item_id: str
    created: int
    consumed: int
    transfers: int
    operations: int


@dataclass(frozen=True)
class Demand:
    item_id: str
    offered: int = 0
    reserved: int = 0
    orders: int = 0
    ordered_base: int = 0
    sold: int = 0


@dataclass(frozen=True)
class Receipt:
    id: str
    kind: str
    created_at: datetime
    completed: bool
    entries: tuple[dict[str, Any], ...]
    gold_flows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Investigation:
    id: int
    category: str
    status: str
    item_id: str
    quantity: int
    price: int
    tax: int
    receipts: tuple[Receipt, ...]
    missing_receipts: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    at: datetime
    hours: float
    movements: tuple[Movement, ...]
    demand: tuple[Demand, ...]
    awaiting_return: int
    investigation: Investigation | None
    requested_id: int | None
    npc_checks: int
    npc_profitable: tuple[tuple[str, int, int], ...]


def demand_snapshot(
    state: MarketState, content: GameContent, now: int, recent: set[str]
) -> tuple[tuple[Demand, ...], int]:
    counted: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    awaiting_return = 0
    for lot in state.lots:
        item_id = template_id(lot.item_id)
        active = lot.status in {"open", "reserved"}
        if active and (
            lot.expires <= now or (lot.status == "reserved" and lot.reserved_until <= now)
        ):
            awaiting_return += 1
        elif lot.status == "open":
            counted[item_id][0] += lot.quantity
        elif lot.status == "reserved":
            counted[item_id][1] += lot.quantity
        if lot.status == "sold" and lot.receipts and lot.receipts[-1] in recent:
            counted[item_id][4] += lot.quantity
    recipes = {one.id: one for one in content.recipes}
    for order in state.orders:
        if order.status == "open" and order.expires <= now:
            awaiting_return += 1
        elif order.status == "open":
            recipe = recipes.get(order.recipe_id)
            item_id = recipe.output_id if recipe else f"неизвестный рецепт: {order.recipe_id}"
            counted[item_id][2] += 1
            counted[item_id][3] += recipe.output_count if recipe else 0
        elif order.status == "filled" and order.receipts and order.receipts[-1] in recent:
            counted[template_id(order.item_id)][4] += order.quantity
    return tuple(
        Demand(item_id, *counts) for item_id, counts in sorted(counted.items())
    ), awaiting_return


def npc_cycles(content: GameContent) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """Проверить покупку и продажу NPC с предельными бонусами текущих правил."""
    profitable = []
    for item in content.items:
        bought = economy.buy_price(
            content, item, charisma=1000, modifiers={"shop_price_percent": -1000}
        )
        sold = economy.sell_price(content, item, modifiers={"sell_price_percent": 1000})
        if sold > bought:
            profitable.append((item.id, bought, sold))
    return len(content.items), tuple(profitable)


async def investigate(connection: Any, state: MarketState, lot_id: int) -> Investigation | None:
    lot = next((one for one in state.lots if one.id == lot_id), None)
    order = next((one for one in state.orders if one.id == lot_id), None)
    target = lot or order
    if target is None:
        return None
    receipts = []
    missing = []
    for receipt_id in dict.fromkeys(target.receipts):
        operation = await connection.fetchrow(
            "SELECT id, kind, created_at, completed FROM economic_operations WHERE id=$1",
            receipt_id,
        )
        if operation is None:
            missing.append(receipt_id)
            continue
        entries = await connection.fetch(
            "SELECT owner_kind, owner_id, container, resource, item_id, amount"
            " FROM economic_entries WHERE operation_id=$1 ORDER BY id",
            receipt_id,
        )
        flows = await connection.fetch(
            "SELECT flow, character_id, amount FROM gold_flow WHERE operation_id=$1 ORDER BY id",
            receipt_id,
        )
        receipts.append(
            Receipt(
                operation["id"],
                operation["kind"],
                operation["created_at"],
                operation["completed"],
                tuple(map(dict, entries)),
                tuple(map(dict, flows)),
            )
        )
    return Investigation(
        target.id,
        "лот" if lot else "заказ",
        target.status,
        target.item_id,
        target.quantity,
        target.price,
        target.tax,
        tuple(receipts),
        tuple(missing),
    )


async def read_report(
    connection: Any, content: GameContent, *, hours: float = 24, lot_id: int | None = None
) -> Report:
    """Все чтения используют один неизменный снимок; записи SQL запрещены."""
    if not math.isfinite(hours) or hours < 0:
        raise ValueError("Часы должны быть конечным неотрицательным числом.")
    if lot_id is not None and lot_id <= 0:
        raise ValueError("Номер лота или заказа должен быть положительным.")
    async with connection.transaction(isolation="repeatable_read", readonly=True):
        at = await connection.fetchval("SELECT transaction_timestamp()")
        schema = await connection.fetchval("SELECT version_num FROM alembic_version")
        escrow = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='market_economic_audit'"
            " AND tgrelid='gameplay_state'::regclass AND NOT tgisinternal"
            " AND tgenabled IN ('O', 'A'))"
        )
        if not escrow:
            raise ValueError(f"Схема {schema} не учитывает резерв рынка. Нужна миграция 0038.")
        raw = await connection.fetchval("SELECT value FROM gameplay_state WHERE key='market:board'")
        state = TypeAdapter(MarketState).validate_json(raw) if raw else MarketState()
        rows = await connection.fetch(MOVEMENTS_SQL, hours)
        movements = tuple(Movement(**dict(one)) for one in rows)
        closing_receipts = [
            one.receipts[-1]
            for one in (*state.lots, *state.orders)
            if one.status in {"sold", "filled"} and one.receipts
        ]
        recent_rows = await connection.fetch(
            "SELECT id FROM economic_operations WHERE id=ANY($2::text[]) AND completed"
            " AND ($1::double precision=0 OR created_at >= transaction_timestamp()"
            " - $1::double precision * interval '1 hour')",
            hours,
            closing_receipts,
        )
        demand, awaiting = demand_snapshot(
            state, content, int(at.timestamp()), {one["id"] for one in recent_rows}
        )
        investigation = await investigate(connection, state, lot_id) if lot_id else None
    checks, profitable = npc_cycles(content)
    return Report(at, hours, movements, demand, awaiting, investigation, lot_id, checks, profitable)


def item_name(content: GameContent, item_id: str) -> str:
    return content.item(item_id).name if content.has_item(item_id) else item_id


def render(report: Report, content: GameContent) -> str:
    period = f"за последние {report.hours:g} ч" if report.hours else "за всю сохранённую историю"
    lines = [f"Экономика {period}. Снимок: {report.at.isoformat()}."]
    lines.append(
        "Рынок сейчас: товар в свободной продаже / в резерве / заказы / продано за период."
    )
    if not report.demand:
        lines.append("Предложений, заказов и завершённых продаж за период нет.")
    for row in report.demand:
        lines.append(
            f"{item_name(content, row.item_id)}: {row.offered} / {row.reserved} / "
            f"{row.orders} (базовое количество {row.ordered_base}) / {row.sold}."
        )
    lines.append(f"Просроченных объявлений, ожидающих возврата: {report.awaiting_return}.")
    lines.append("Источники и расходы: создание и расход посчитаны отдельно для каждой операции.")
    if not report.movements:
        lines.append("За период экономических записей нет.")
    for row in report.movements:
        resource = "золото" if row.resource == "gold" else item_name(content, row.item_id)
        source = f"; виды: {row.flows}" if row.flows else "; вид известен только по операции"
        lines.append(
            f"{resource}; операция {row.kind}{source}: создано {row.created}, "
            f"израсходовано {row.consumed}, передач/перекладываний {row.transfers}, "
            f"операций {row.operations}."
        )
    gold = [one for one in report.movements if one.resource == "gold"]
    created, consumed = sum(one.created for one in gold), sum(one.consumed for one in gold)
    lines.append(
        f"Всего золота создано: {created}; израсходовано: {consumed}; "
        f"разница: {created - consumed:+}."
    )
    lines.append(
        "Резерв, банк и передачи между игроками включены в общую сумму имущества: "
        "они сами по себе не создают и не расходуют ресурс. Пошлина уменьшает эту сумму."
    )
    lines.append(
        "Рост количества золота сам по себе не доказывает инфляцию. "
        "Для оценки нужны цены, число игроков, их активность и объём покупок."
    )
    lines.append(
        f"Перепродажа NPC: проверено предметов каталога {report.npc_checks}; "
        f"выгодных циклов покупки и продажи {len(report.npc_profitable)}."
    )
    for item_id, bought, sold in report.npc_profitable:
        lines.append(
            f"Выгодный цикл: {item_name(content, item_id)}; покупка {bought}, продажа {sold}."
        )
    lines.append("Составные циклы ремесла, разбора и перековки проверяет scripts/m08_prices.py.")
    if report.requested_id is not None:
        investigation = report.investigation
        if investigation is None:
            lines.append(f"Лот или заказ {report.requested_id} не найден.")
        else:
            lines.append(
                f"Расследование: {investigation.category} {investigation.id}; состояние "
                f"{investigation.status}; предмет {item_name(content, investigation.item_id)}; "
                f"количество {investigation.quantity}; цена {investigation.price}; "
                f"пошлина {investigation.tax}."
            )
            for receipt in investigation.receipts:
                completed = "да" if receipt.completed else "нет"
                lines.append(
                    f"Операция {receipt.id}; вид {receipt.kind}; время "
                    f"{receipt.created_at.isoformat()}; завершена: {completed}."
                )
                for entry in receipt.entries:
                    lines.append(
                        f"Запись: {entry['owner_kind']} {entry['owner_id']}; "
                        f"{entry['container']}; {entry['resource']} {entry['item_id']}; "
                        f"изменение {entry['amount']:+}."
                    )
                for flow in receipt.gold_flows:
                    lines.append(
                        f"Денежный отчёт: {flow['flow']}; персонаж {flow['character_id']}; "
                        f"изменение {flow['amount']:+}."
                    )
            for missing in investigation.missing_receipts:
                lines.append(f"Связанная операция {missing} отсутствует: история неполна.")
    return "\n".join(lines)


async def run(options: argparse.Namespace) -> int:
    settings = Settings()
    content = load_content(settings.content_dir)
    connection = await asyncpg.connect(
        settings.postgres_dsn,
        command_timeout=30,
        server_settings={"default_transaction_read_only": "on", "application_name": "m08-report"},
    )
    try:
        report = await read_report(connection, content, hours=options.hours, lot_id=options.lot)
    finally:
        await connection.close()
    print(render(report, content))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24, help="период в часах; 0 — вся история")
    parser.add_argument("--lot", type=int, help="расследовать лот или ремесленный заказ по номеру")
    options = parser.parse_args()
    if not math.isfinite(options.hours) or options.hours < 0:
        parser.error("--hours: требуется конечное неотрицательное число")
    if options.lot is not None and options.lot <= 0:
        parser.error("--lot: требуется положительный номер")
    try:
        return asyncio.run(run(options))
    except Exception as error:
        print(
            f"Отчёт не получен ({type(error).__name__}). Проверьте доступ к базе и схему 0038; "
            "адрес подключения и содержимое ошибки не выводятся.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
