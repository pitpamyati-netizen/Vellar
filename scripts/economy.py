"""Сутки экономики одной таблицей.

Каждое движение золота, кроме передачи из рук в руки, пишет строку ``gold_flow``
(``mmorpg.economy_log``). Строк этих за сутки игры тысячи, и по одной они не
говорят ничего; сложенные по видам — говорят всё: сколько мир выплатил, сколько
города забрали, сколько убрала пошлина и не печатает ли арена золото.

    uv run python scripts/economy.py logs/important.log            за весь файл
    uv run python scripts/economy.py logs/important.log --hours 24 за сутки

Файл игра пишет сама (``mmorpg.logging``): ``logs/important.log`` — та половина
журнала, которую автоочистка не трогает, и ``gold_flow`` лежит именно в ней.

Читает и то, и другое: строки JSON (``LOG_JSON=true``, как в проде) и обычный
вывод в консоль. Ничего не меняет и никуда не ходит — считает и печатает.

Ради этого счёта журнал и заведён: три числа, на которых стоит экономика —
пошлина, ставка арены и цена боя, — были записаны догадками, и первая их правка
должна опираться на сутки живой игры, а не на ощущение.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

#: Что и в каком порядке показывать. Порядок не алфавитный, а смысловой: сначала
#: то, откуда золото берётся, потом то, куда девается.
ORDER = (
    "fight",
    "search",
    "descent",
    "quest",
    "arena_payout",
    "duel",
    "trade_price",
    "shop",
    "service",
    "defeat",
    "arena_stake",
    "trade_duty",
    "trade_rollback",
    "keeper",
)

NAMES = {
    "fight": "бои",
    "search": "находки",
    "descent": "дно спуска",
    "quest": "задания",
    "arena_payout": "выплаты арены",
    "arena_stake": "ставки арены",
    "duel": "поединки",
    "trade_price": "сделки между игроками",
    "trade_duty": "пошлина",
    "trade_rollback": "откаты сделок",
    "shop": "лавки",
    "service": "службы города",
    "defeat": "потеряно в поражениях",
    "keeper": "выдачи смотрителя",
}

#: Движения, которые ничего не создают и не убирают: золото переходит от одного
#: игрока к другому. В итог они не входят, иначе каждая сделка выглядела бы
#: притоком - пишется-то она один раз, со стороны получателя.
TRANSFERS = frozenset({"trade_price", "duel", "trade_rollback"})

#: Обычный вывод в консоль: ``gold_flow  amount=124 character_id=17 flow=fight``.
CONSOLE = re.compile(r"gold_flow\b.*?\bamount=(?P<amount>-?\d+).*?\bflow=(?P<flow>\w+)")
STAMP = re.compile(r"(?P<stamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")


def movements(lines: Iterator[str]) -> Iterator[tuple[str, int, float | None]]:
    """Вид, сумма и момент каждого движения. Всё прочее пропускается молча."""
    for line in lines:
        if "gold_flow" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "gold_flow":
                continue
            yield (
                str(row.get("flow", "?")),
                int(row.get("amount", 0)),
                _moment(row.get("timestamp")),
            )
            continue
        found = CONSOLE.search(stripped)
        if found is None:
            continue
        stamped = STAMP.search(stripped)
        yield (
            found["flow"],
            int(found["amount"]),
            _moment(stamped["stamp"] if stamped else None),
        )


def _moment(stamp: str | None) -> float | None:
    """Секунды unix из отметки времени, или ``None``, если её не разобрать."""
    if not stamp:
        return None
    text = stamp.replace("Z", "+00:00").replace(" ", "T")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def tally(
    rows: Iterator[tuple[str, int, float | None]], *, since: float | None
) -> dict[str, tuple[int, int]]:
    """Сколько раз и на сколько по каждому виду."""
    counted: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    for flow, amount, moment in rows:
        if since is not None and moment is not None and moment < since:
            continue
        times, total = counted[flow]
        counted[flow] = (times + 1, total + amount)
    return counted


def render(counted: dict[str, tuple[int, int]]) -> str:
    """Таблица словами и числами. Ни одного процента и ни одной полоски."""
    if not counted:
        return "Ни одного движения золота в этом файле. Игра либо не шла, либо писала в другой."

    known = [flow for flow in ORDER if flow in counted]
    rest = sorted(flow for flow in counted if flow not in ORDER)
    lines = ["Движения золота, сложенные по видам:"]
    for flow in (*known, *rest):
        times, total = counted[flow]
        name = NAMES.get(flow, flow)
        between = " (между игроками)" if flow in TRANSFERS else ""
        lines.append(f"{name}: {total:+} золота за {times} раз{between}.")

    came = sum(total for flow, (_, total) in counted.items() if total > 0 and flow not in TRANSFERS)
    went = sum(total for flow, (_, total) in counted.items() if total < 0 and flow not in TRANSFERS)
    lines.append(f"Пришло в игру: {came}. Ушло из игры: {went}. Итого: {came + went:+}.")
    lines.append(
        "Из рук в руки не считается: такое золото не появляется и не исчезает, "
        "оно меняет владельца."
    )
    lines.append(
        "Растущее «итого» - это инфляция: миру платят больше, чем города и пошлина забирают."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Экономика по журналу игры.")
    parser.add_argument("log", type=Path, help="файл, куда писала игра")
    parser.add_argument("--hours", type=float, default=0.0, help="только за последние часы")
    options = parser.parse_args(argv)

    if not options.log.exists():
        print(f"файла нет: {options.log}", file=sys.stderr)
        return 1

    since = time.time() - options.hours * 3600 if options.hours else None
    with options.log.open(encoding="utf-8", errors="replace") as file:
        counted = tally(movements(iter(file)), since=since)
    print(render(counted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
