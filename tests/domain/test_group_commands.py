"""Грамматика команд в группе (``Narrative.md``, раздел 9).

Два свойства важнее любого отдельного случая: небрежно набранная команда всё
равно работает, а всё прочее - молчание: бот не должен отвечать разговору, в
котором к нему не обращались.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.rules.group_commands import (
    MAX_QUANTITY,
    UNADDRESSED,
    GroupIntent,
    parse_group_command,
)


def test_profile_takes_no_arguments() -> None:
    assert parse_group_command("профиль") == parse_group_command("Профиль")
    parsed = parse_group_command("профиль")
    assert parsed is not None and parsed.intent is GroupIntent.PROFILE
    # Аргумент значит, что игрок хотел чего-то совсем другого.
    assert parse_group_command("профиль Аргуса") is None


@pytest.mark.parametrize(
    "text",
    [
        "продать 100 кожаная броня",
        "Продать 100 Кожаная Броня",
        "  продать   100    кожаная броня  ",
        "/продать 100 кожаная броня",
    ],
)
def test_a_sale_is_read_however_it_is_typed(text: str) -> None:
    parsed = parse_group_command(text)

    assert parsed is not None
    assert parsed.intent is GroupIntent.SELL
    assert parsed.amount == 100
    assert parsed.item_query == "кожаная броня"


def test_buying_mirrors_selling() -> None:
    parsed = parse_group_command("купить 250 бронзовый клинок")

    assert parsed is not None
    assert parsed.intent is GroupIntent.BUY
    assert parsed.amount == 250
    assert parsed.item_query == "бронзовый клинок"


def test_yo_is_optional_because_players_do_not_type_it() -> None:
    parsed = parse_group_command("передать кожаная бронё")

    assert parsed is not None
    assert parsed.item_query == "кожаная броне"


@pytest.mark.parametrize(
    ("text", "amount", "query"),
    [
        ("передать кожаная броня", 1, "кожаная броня"),
        ("передать 2 кожаная броня", 2, "кожаная броня"),
        (f"передать {MAX_QUANTITY} стрела", MAX_QUANTITY, "стрела"),
    ],
)
def test_giving_an_item_counts_units(text: str, amount: int, query: str) -> None:
    parsed = parse_group_command(text)

    assert parsed is not None
    assert parsed.intent is GroupIntent.GIVE_ITEM
    assert parsed.amount == amount
    assert parsed.item_query == query


@pytest.mark.parametrize("text", ["передать 100 золота", "передать 5000 золотых"])
def test_giving_gold_is_a_different_intent(text: str) -> None:
    parsed = parse_group_command(text)

    assert parsed is not None
    assert parsed.intent is GroupIntent.GIVE_GOLD
    assert parsed.item_query == ""


def test_gold_is_not_capped_at_the_item_quantity() -> None:
    """Вещи считают штуками, золото - тысячами."""
    parsed = parse_group_command("передать 50000 золота")

    assert parsed is not None and parsed.amount == 50_000
    assert parse_group_command(f"передать {MAX_QUANTITY + 1} стрела") is None


def test_a_bare_number_never_moves_money() -> None:
    """«передать 100» — с тем же успехом оговорка, что и намерение."""
    assert parse_group_command("передать 100") is None


@pytest.mark.parametrize(
    ("text", "intent"),
    [("принять 12", GroupIntent.ACCEPT), ("отказ 12", GroupIntent.DECLINE)],
)
def test_answers_carry_the_offer_number(text: str, intent: GroupIntent) -> None:
    parsed = parse_group_command(text)

    assert parsed is not None
    assert parsed.intent is intent
    assert parsed.amount == 12


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("блок", GroupIntent.BLOCK),
        ("Блок", GroupIntent.BLOCK),
        ("разблок", GroupIntent.UNBLOCK),
        ("снять блок", GroupIntent.UNBLOCK),
        ("скрыть профиль", GroupIntent.HIDE_PROFILE),
        ("Открыть профиль", GroupIntent.SHOW_PROFILE),
    ],
)
def test_privacy_is_said_in_one_command(text: str, intent: GroupIntent) -> None:
    parsed = parse_group_command(text)

    assert parsed is not None
    assert parsed.intent is intent
    assert parsed.amount == 0


def test_only_answers_and_the_privacy_switch_need_no_target() -> None:
    """Всё остальное называет адресата тем, что отвечает ему.

    Расширить это множество значит, что бот начнёт отвечать на выкрикнутое в
    комнату, поэтому оно выписано здесь, а не оставлено хендлеру.
    """
    assert set(UNADDRESSED) == {
        GroupIntent.ACCEPT,
        GroupIntent.DECLINE,
        GroupIntent.HIDE_PROFILE,
        GroupIntent.SHOW_PROFILE,
    }


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "привет всем",
        "скрыть",  # половина фразы - это чья-то реплика
        "открыть",
        "блок Аргуса",
        "снять",
        "продать кожаная броня",  # цены нет
        "продать 100",  # no item
        "продать 0 кожаная броня",  # даром не бывает
        "продать -5 кожаная броня",
        "продать 100р кожаная броня",
        "продать 2000000 кожаная броня",  # выше потолка
        "принять",
        "принять двенадцать",
        "отказ 0",
        "передать",
        "передать 0 стрела",
    ],
)
def test_anything_else_is_silence(text: str) -> None:
    assert parse_group_command(text) is None
