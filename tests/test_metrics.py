"""Что игра о себе рассказывает, пока в неё играют."""

from __future__ import annotations

import asyncio

import pytest

from mmorpg.metrics import BUCKETS, Metrics, Stopwatch, report, reporting


def test_an_untouched_window_says_nothing_rather_than_guessing() -> None:
    metrics = Metrics()
    assert metrics.snapshot() == {
        "updates": 0,
        "failures": 0,
        "p50": 0.0,
        "p95": 0.0,
        "slowest": 0.0,
    }


def test_the_percentile_names_the_bucket_the_share_fits_under() -> None:
    metrics = Metrics()
    for _ in range(95):
        metrics.observe(0.004)
    for _ in range(5):
        metrics.observe(0.4)

    assert metrics.quantile(0.5) == BUCKETS[0]
    assert metrics.quantile(0.95) == BUCKETS[0]
    # Хвост считается: ста́вить его в один ряд со срединой значило бы потерять
    # ровно то, ради чего меряют.
    assert metrics.slowest == pytest.approx(0.4)


def test_the_slowest_update_survives_the_averages() -> None:
    metrics = Metrics()
    metrics.observe(0.01)
    metrics.observe(1.5)
    assert metrics.snapshot()["slowest"] == pytest.approx(1.5)
    assert metrics.quantile(0.95) >= 1.0


def test_a_percentile_never_claims_a_wait_that_never_happened() -> None:
    """Потолок корзины - верхняя граница, а не измерение.

    Окно, где самое долгое обновление заняло 266 мс, писало ``p95=0.5``: полсекунды
    ожидания, которого не было. Читающий журнал видит в этом беду там, где её нет.
    """
    metrics = Metrics()
    for _ in range(11):
        metrics.observe(0.2)
    metrics.observe(0.266)

    assert metrics.quantile(0.95) == pytest.approx(0.266)
    assert metrics.quantile(0.95) <= metrics.slowest


def test_anything_slower_than_every_bucket_still_lands_somewhere() -> None:
    metrics = Metrics()
    metrics.observe(30.0)
    assert metrics.quantile(0.5) == BUCKETS[-1]


def test_a_failure_is_counted_where_it_was_caught() -> None:
    metrics = Metrics()
    metrics.observe(0.01, failed=True)
    metrics.failed()
    assert metrics.snapshot() == {
        "updates": 1,
        "failures": 2,
        "p50": BUCKETS[1],
        "p95": BUCKETS[1],
        "slowest": 0.01,
    }


def test_the_window_starts_over_after_it_is_reported() -> None:
    """Средняя за сутки прячет те десять минут, ради которых всё и меряется."""
    metrics = Metrics()
    metrics.observe(2.0)
    metrics.reset()
    assert metrics.snapshot()["updates"] == 0
    assert metrics.snapshot()["slowest"] == 0.0


async def test_the_last_window_is_written_on_the_way_out() -> None:
    metrics = Metrics()
    async with reporting(metrics, interval=60.0):
        metrics.observe(0.02)
    # Отчёт на выходе обнуляет окно: то, что успели насчитать, записано.
    assert metrics.snapshot()["updates"] == 0


async def test_reporting_writes_a_window_while_the_game_runs() -> None:
    metrics = Metrics()
    async with reporting(metrics, interval=0.01):
        metrics.observe(0.02)
        await asyncio.sleep(0.05)
        # Окно уже записано и начато заново, а игра идёт дальше.
        assert metrics.snapshot()["updates"] == 0
        metrics.observe(0.03)


def test_a_stopwatch_measures_forward() -> None:
    watch = Stopwatch()
    assert watch.seconds >= 0.0


def test_a_minute_in_which_nothing_happened_is_not_written(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Тихая минута не строка.

    Живость подтверждает сердцебиение (``mmorpg.health``), его и читает
    ``scripts/watchdog.py``; строка о пустом окне только уносила из окна
    оператора то, ради чего он его и открыл.
    """
    metrics = Metrics()
    report(metrics)
    assert "metrics" not in capsys.readouterr().out

    metrics.observe(0.01)
    report(metrics)
    assert "metrics" in capsys.readouterr().out
