"""Старая цена, бонус продажи, место задания и частный ввод M02.4–10."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED

from mmorpg import economy_log
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.location import NodeKind
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.rules import economy, modifiers, quests
from mmorpg.presentation.telegram.flows import play
from mmorpg.presentation.telegram.flows.state import Goods, PlayState
from mmorpg.presentation.telegram.middlewares.errors import ErrorMiddleware
from mmorpg.presentation.telegram.routing import Command, Intent
from mmorpg.presentation.telegram.screens import items, shop
from mmorpg.presentation.telegram.screens.base import ScreenId
from tests.presentation.test_audit import _lines, _run, _update
from tests.presentation.test_audit import journal as _journal

journal = _journal


def hero(**kwargs):
    return Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=20,
        gold=10000,
        **kwargs,
    )


def buy(content, state, goods):
    return play.advance(content, hero(), state, items.BUY.text, world_seed="test", goods=goods)


@pytest.mark.parametrize("missing", ["stock", "price", "both"])
def test_missing_current_goods_cannot_be_bought_from_a_saved_card(content, missing):
    item = content.item("dagger@1#legendary")
    state = PlayState().at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM)
    state = replace(state, item_id=item.id, shop_price=item.price)
    goods = Goods(
        gold=10000,
        stock=() if missing != "price" else (item,),
        prices={} if missing != "stock" else {item.id: item.price},
    )
    result = buy(content, state, goods)
    assert result.pending.character is None and not result.pending.items
    assert "больше нет" in result.notice


@pytest.mark.parametrize("old_price", [None, 50, 150])
def test_changed_price_requires_new_agreement_and_survives_state_serialisation(content, old_price):
    item = content.item("sword@1#common")
    state = replace(
        PlayState().at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM), item_id=item.id, shop_price=old_price
    )
    goods = Goods(gold=10000, stock=(item,), prices={item.id: 100})
    first = buy(content, state, goods)
    assert first.pending.character is None
    assert "100" in first.notice and "ещё раз" in first.notice
    resumed = PlayState.deserialise(first.serialise())
    second = buy(content, resumed, goods)
    assert second.pending.character.gold == 9900
    assert second.pending.items == ((item.id, 1),)


@pytest.mark.parametrize("bonus", [0, 10, 15])
def test_sale_uses_all_modifiers_for_display_and_payment(content, bonus):
    trait = next(t for t in content.traits if "sell_price_percent" in t.modifiers)
    adjusted = replace(
        content,
        traits=tuple(
            replace(t, modifiers={"sell_price_percent": float(bonus)}) if t.id == trait.id else t
            for t in content.traits
        ),
    )
    character = hero(trait_ids=(trait.id,))
    item = adjusted.item("sword@1#common")
    held = (shop.OwnedItem(item_id=item.id, quantity=1),)
    prices = play._sale_prices(adjusted, held, character)
    expected = economy.sell_price(
        adjusted, item, modifiers=modifiers.collect_modifiers(adjusted, character)
    )
    assert prices[item.id] == expected
    state = PlayState().at(ScreenId.SELL)
    command = Command(intent=Intent.SELECT, argument=shop.sell_text(item, expected))
    sold = play._handle_sell(
        adjusted, character, state, command, Goods(gold=character.gold, owned=held)
    )
    assert sold.pending.character.gold == character.gold + expected


@pytest.mark.parametrize("bonus", [0, 10, 15, 1000])
def test_buy_sell_cycle_never_creates_gold_with_small_prices_or_stacked_bonuses(content, bonus):
    item = content.item("sword@1#common")
    for base in range(1, 60):
        cheap = replace(item, price=base)
        paid = economy.buy_price(
            content, cheap, charisma=10000, modifiers={"shop_price_percent": -1000}
        )
        sold = economy.sell_price(content, cheap, modifiers={"sell_price_percent": bonus})
        assert sold <= paid


@pytest.mark.parametrize(
    "city,slot,expected", [("last_beacon", 1, 0), ("farhold", 2, 0), ("", 0, 0), ("farhold", 1, 1)]
)
def test_search_counts_the_event_place_not_the_character_city(content, city, slot, expected):
    character = hero(
        city_id="last_beacon", quests=QuestLog(taken={"farhold_tallies": 0}, done=("already_paid",))
    )
    log, _ = quests.record_search(
        content, character, NodeKind.CACHE, city_id=city, location_slot=slot
    )
    assert log.progress("farhold_tallies") == expected
    assert log.done == ("already_paid",)


@pytest.mark.parametrize(
    "private_text",
    [
        "мой секрет 123456",
        "Аргус",
        "/неизвестно пароль",
        "причина наказания секрет",
        "найти частное имя",
    ],
)
@pytest.mark.parametrize("outcome", [None, UNHANDLED, RuntimeError("secret exception")])
async def test_private_input_is_absent_from_every_action_record(
    journal: Path, private_text, outcome
):
    from mmorpg.logging import ACTIVITY_FILE, IMPORTANT_FILE

    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError):
            await _run(_update(private_text), outcome)
    else:
        await _run(_update(private_text), outcome)
    text = "\n".join(_lines(journal, ACTIVITY_FILE) + _lines(journal, IMPORTANT_FILE))
    assert private_text not in text
    assert "did=text_input" in text


async def test_exception_message_with_private_input_is_not_logged(journal: Path):
    secret = "пароль закрытой переписки 123456"

    async def failed(event, data):
        raise RuntimeError(secret)

    # Without a Message, no transport is used. The handler's diagnostic is real.
    await ErrorMiddleware()(failed, object(), {})
    text = await asyncio.to_thread(
        lambda: "\n".join(p.read_text(encoding="utf-8") for p in journal.iterdir())
    )
    assert secret not in text
    assert "RuntimeError" in text and "handler_failed" in text


async def test_economy_sink_failure_does_not_copy_private_exception_text(journal: Path):
    secret = "скрытый аргумент игрока 123456"

    async def failed():
        raise RuntimeError(secret)

    await economy_log._guarded(failed())
    text = await asyncio.to_thread(
        lambda: "\n".join(p.read_text(encoding="utf-8") for p in journal.iterdir())
    )
    assert secret not in text
    assert "RuntimeError" in text and "gold_flow_sink_failed" in text
