"""Покупка стопкой и вложение порцией: показ, согласие, отказ и старые экраны."""

import json
from dataclasses import replace

import pytest

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities import Character, StatCode
from mmorpg.domain.rules import milestones
from mmorpg.presentation.telegram.flows import creation, play
from mmorpg.presentation.telegram.flows.state import Goods, PlayState
from mmorpg.presentation.telegram.screens import amounts, items, quests
from mmorpg.presentation.telegram.screens import creation as creation_screens
from mmorpg.presentation.telegram.screens import play as play_screens
from mmorpg.presentation.telegram.screens.base import ScreenId
from mmorpg.presentation.telegram.screens.creation import STAT_NAMES
from mmorpg.presentation.telegram.screens.format import gold


def hero(points=100, money=10000):
    return Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        gold=money,
        unspent_stat_points=points,
        tutorial=0b111111,
    )


def buttons(screen):
    return {button.text for row in screen.rows for button in row}


def card(content, *, money=10000, item_id="small_healing_potion", price=7):
    character = hero(money=money)
    item = content.item(item_id)
    goods = Goods(gold=money, stock=(item,), prices={item.id: price})
    state = replace(
        PlayState().at(ScreenId.SHOP).at(ScreenId.SHOP_ITEM), item_id=item.id, shop_price=price
    )
    return character, state, goods


def advance(content, character, state, text, goods=None):
    return play.advance(content, character, state, text, world_seed="bulk-test", goods=goods)


@pytest.mark.parametrize(
    "item_id", ["small_healing_potion", "small_resource_draught", "coarse_cloth"]
)
@pytest.mark.parametrize("quantity", [1, 5, 10, 50, 100])
def test_bulk_purchase_shows_total_before_a_single_payment(content, item_id, quantity):
    character, state, goods = card(content, item_id=item_id)
    picked = advance(content, character, state, items.quantity_label(quantity).text, goods)
    assert picked.pending.empty
    restored = PlayState.deserialise(picked.serialise())
    shown = play.render(content, character, restored, world_seed="bulk-test", goods=goods)
    assert f"Количество к покупке: {quantity}. За всё: {gold(7 * quantity)}." in shown.text()
    bought = advance(content, character, restored, "/купить", goods)
    assert bought.pending.character.gold == character.gold - 7 * quantity
    assert bought.pending.items == ((state.item_id, quantity),)
    assert bought.screen is ScreenId.SHOP
    assert bought.shop_quantity == 1
    assert f"Уплачено {7 * quantity} золота" in bought.notice


def test_custom_quantity_and_cancel_do_not_spend_gold(content):
    character, state, goods = card(content)
    picked = advance(content, character, state, "/количество 3", goods)
    assert picked.shop_quantity == 3 and picked.pending.empty
    cancelled = advance(content, character, picked, "Назад", goods)
    assert cancelled.screen is ScreenId.SHOP and cancelled.pending.empty
    bought = advance(content, character, picked, "Купить", goods)
    assert bought.pending.items == ((state.item_id, 3),)


@pytest.mark.parametrize(
    "text",
    [
        "/количество 0",
        "/количество -5",
        "/количество 1.5",
        "/количество 9999999999",
        "/количество 100000000",
        "Количество: 50",
    ],
)
def test_invalid_or_unaffordable_quantity_never_changes_values(content, text):
    character, state, goods = card(content, money=14)
    result = advance(content, character, state, text, goods)
    assert result.pending.empty and result.shop_quantity == 1
    assert result.notice
    shown = play.render(content, character, result, world_seed="bulk-test", goods=goods)
    assert items.quantity_label(5).text not in buttons(shown)


def test_price_change_requires_new_consent_for_the_whole_quantity(content):
    character, state, goods = card(content)
    picked = advance(content, character, state, "/количество 10", goods)
    changed = replace(goods, prices={state.item_id: 9})
    first = advance(content, character, picked, "Купить", changed)
    assert first.pending.empty
    assert "за 10 — 90 золота" in first.notice
    second = advance(
        content, character, PlayState.deserialise(first.serialise()), "Купить", changed
    )
    assert second.pending.character.gold == character.gold - 90
    assert second.pending.items == ((state.item_id, 10),)


def test_lost_gold_or_lost_stock_prevents_bulk_purchase(content):
    character, state, goods = card(content)
    picked = advance(content, character, state, "/количество 10", goods)
    poorer = replace(character, gold=69)
    result = advance(content, poorer, picked, "Купить", replace(goods, gold=69))
    assert result.pending.empty and "Не хватает" in result.notice
    assert "Купить" not in buttons(
        play.render(content, poorer, result, world_seed="bulk-test", goods=replace(goods, gold=69))
    )
    for text in ("Купить", "/количество 5"):
        gone = advance(content, character, picked, text, replace(goods, stock=()))
        assert gone.pending.empty and "больше нет" in gone.notice


def test_single_items_have_no_bulk_purchase_and_new_card_resets_quantity(content):
    character, state, goods = card(content, item_id="sword@1#common")
    shown = play.render(content, character, state, world_seed="bulk-test", goods=goods)
    assert not any(text.startswith("Количество:") for text in buttons(shown))
    refused = advance(content, character, state, "/количество 5", goods)
    assert refused.pending.empty and "поштучно" in refused.notice
    bought = advance(content, character, replace(state, shop_quantity=10), "Купить", goods)
    assert bought.pending.items == ((state.item_id, 1),)
    shop = replace(state, shop_quantity=10).at(ScreenId.SHOP)
    shown = play.render(content, character, shop, world_seed="bulk-test", goods=goods)
    choice = next(text for text in buttons(shown) if text.endswith("купить"))
    assert advance(content, character, shop, choice, goods).shop_quantity == 1


@pytest.mark.parametrize("points", [0, 1, 4, 5, 9, 10, 49, 50, 99, 100, 105])
def test_only_affordable_stat_amounts_are_offered(content, points):
    character = hero(points)
    shown = play.render(content, character, PlayState().at(ScreenId.STATS), world_seed="bulk-test")
    for amount in amounts.STAT_AMOUNTS:
        assert (amounts.stat_amount_label(amount).text in buttons(shown)) == (points >= amount)


@pytest.mark.parametrize("code,name", STAT_NAMES.items())
@pytest.mark.parametrize("amount", amounts.STAT_AMOUNTS)
def test_stat_batch_spends_exact_amount_and_returns_to_one_when_needed(content, code, name, amount):
    character = hero(points=amount)
    state = PlayState().at(ScreenId.STATS)
    selected = advance(content, character, state, amounts.stat_amount_label(amount).text)
    assert selected.pending.empty
    restored = PlayState.deserialise(selected.serialise())
    spent = advance(content, character, restored, play_screens.spend_label(name, amount).text)
    stronger = spent.pending.character
    assert stronger.allocated[code] == character.allocated[code] + amount
    assert stronger.unspent_stat_points == 0
    assert spent.stat_amount == 1
    stale = advance(content, stronger, spent, play_screens.spend_label(name, amount).text)
    assert stale.pending.empty


def test_stat_preview_uses_whole_batch_and_announces_all_new_milestones(content):
    from mmorpg.domain.rules.stats import derived_stats
    from mmorpg.presentation.telegram.screens.format import number

    character = hero(points=100)
    state = replace(PlayState().at(ScreenId.STATS), stat_amount=100)
    shown = play.render(content, character, state, world_seed="bulk-test")
    assert "Вложение 100 очков: расчёт" in shown.text()
    assert "за 100 очков" in shown.text()
    tougher = replace(character, allocated=character.allocated.with_change(StatCode.END, 100))
    health_gain = (
        derived_stats(content, tougher).max_health - derived_stats(content, character).max_health
    )
    assert f"максимальное здоровье плюс {number(health_gain)}" in shown.text()
    spent = advance(content, character, state, play_screens.spend_label("Сила", 100).text)
    before = {one.name for one in milestones.reached(content, character)}
    after = {one.name for one in milestones.reached(content, spent.pending.character)}
    assert after - before
    for name in after - before:
        assert name in spent.notice


def test_stale_stat_button_cannot_spend_a_different_amount(content):
    state = replace(PlayState().at(ScreenId.STATS), stat_amount=10)
    assert advance(content, hero(points=9), state, "Вложить 10: Сила").pending.empty
    assert advance(content, hero(points=10), state, "Вложить: Сила").pending.empty


@pytest.mark.parametrize("amount", [1, 5, 10, 50, 100])
def test_creation_supports_the_same_stat_batches(content, amount):
    adjusted = replace(content, rules=replace(content.rules, free_points_at_creation=amount))
    state = creation.CreationState(
        screen=ScreenId.CREATE_POINTS,
        draft=CharacterDraft(name="Аргус", race_id="human", class_id="warrior"),
    )
    selected = creation.advance(adjusted, state, amounts.stat_amount_label(amount).text)
    assert selected.draft == state.draft
    restored = creation.CreationState.deserialise(selected.serialise())
    spent = creation.advance(adjusted, restored, creation_screens.point_label("Сила", amount).text)
    assert spent.draft.allocated[StatCode.STR] == amount
    assert spent.draft.points_left(amount) == 0
    shown = creation.render(adjusted, spent)
    assert not any(text.startswith("Вкладывать по") for text in buttons(shown))
    assert creation.advance(adjusted, spent, "Продолжить").screen is ScreenId.CREATE_CONFIRM


@pytest.mark.parametrize("amount", [-1, 0, 6])
def test_creation_budget_cannot_be_bypassed_by_batch(amount):
    draft = CharacterDraft()
    assert draft.spend_point(StatCode.STR, 5, amount) == draft


def test_old_states_keep_single_purchase_and_single_point():
    state = PlayState().serialise()
    saved = json.loads(state)
    for field in ("stat_amount", "shop_quantity"):
        saved.pop(field)
    restored = PlayState.deserialise(json.dumps(saved))
    assert restored.stat_amount == restored.shop_quantity == 1
    saved_creation = json.loads(creation.CreationState().serialise())
    saved_creation.pop("stat_amount")
    assert creation.CreationState.deserialise(json.dumps(saved_creation)).stat_amount == 1


@pytest.mark.parametrize("taken", [False, True])
def test_every_quest_offer_has_no_payer_button_or_payer_reply(content, taken):
    for quest in content.quests:
        character = hero()
        if taken:
            character = replace(character, quests=replace(character.quests, taken={quest.id: 0}))
        shown = quests.offer_screen(content, quest, character)
        assert not any("кто платит" in text.casefold() for text in buttons(shown))
        state = replace(PlayState().at(ScreenId.QUEST_OFFER), quest_id=quest.id)
        response = advance(content, character, state, "Спросить, кто платит")
        assert response.pending.empty
        assert "из своего кармана" not in response.notice
