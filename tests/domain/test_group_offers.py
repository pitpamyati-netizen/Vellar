"""Предложения между игроками (``Narrative.md``, раздел 9).

Важны те правила, которые берегут игрока от чужой ошибки или чужого умысла:
согласиться вправе только адресат, при двусмысленном имени не угадывают ничего,
а протухшее предложение отказывает, а не закрывается. С Roadmap 2.3 автор ещё и
ставит свою сторону вперёд, поэтому большая часть здешних проверок - о том, кому
ещё позволено передумать.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities.trade import Offer, OfferKind, Party
from mmorpg.domain.rules.group_offers import (
    MAX_OFFER_NUMBER,
    OFFER_TTL_SECONDS,
    ItemOption,
    Refusal,
    answerable_by,
    check_gift,
    check_gold_gift,
    check_proposal,
    check_settlement,
    is_expired,
    match_items,
    next_number,
    stakes_gold,
    stakes_item,
)

ARGUS = Party(user_id=1, character_id=10, name="Аргус")
MERLA = Party(user_id=2, character_id=20, name="Мерла")
STRANGER = 3


def offer(kind: OfferKind = OfferKind.SELL, *, price: int = 100, created_at: int = 1000) -> Offer:
    return Offer(
        number=7,
        kind=kind,
        author=ARGUS,
        target=MERLA,
        item_id="light_body@5#common",
        item_name="Кожаная броня",
        price=price,
        created_at=created_at,
    )


# --- кто что делает ---------------------------------------------------


def test_selling_means_the_author_parts_with_the_item() -> None:
    sale = offer(OfferKind.SELL)

    assert sale.giver == ARGUS
    assert sale.payer == MERLA


def test_buying_is_the_same_offer_from_the_other_side() -> None:
    purchase = offer(OfferKind.BUY)

    assert purchase.giver == MERLA
    assert purchase.payer == ARGUS


def test_only_the_target_may_agree() -> None:
    sale = offer()

    assert answerable_by(sale, MERLA.user_id) is True
    assert answerable_by(sale, ARGUS.user_id) is False
    assert answerable_by(sale, STRANGER) is False


# --- пять минут -------------------------------------------------------


def test_an_offer_expires_exactly_when_it_says_it_does() -> None:
    sale = offer(created_at=1000)

    assert is_expired(sale, 1000 + OFFER_TTL_SECONDS - 1) is False
    assert is_expired(sale, 1000 + OFFER_TTL_SECONDS) is True


def test_an_expired_offer_refuses_before_anything_else_is_checked() -> None:
    """Даже безупречно обеспеченное закрытие отвергается, когда время вышло."""
    refusal = check_settlement(
        offer(created_at=0), target_holds=5, target_gold=10_000, now=OFFER_TTL_SECONDS
    )

    assert refusal is Refusal.EXPIRED


# --- что держит предложение -------------------------------------------


def test_a_sale_stakes_the_item_and_a_purchase_stakes_the_gold() -> None:
    """Что бы автор ни выложил, оно и вернётся, если предложение умрёт."""
    assert stakes_item(offer(OfferKind.SELL)) is True
    assert stakes_gold(offer(OfferKind.SELL)) is False
    assert stakes_item(offer(OfferKind.BUY)) is False
    assert stakes_gold(offer(OfferKind.BUY)) is True


# --- объявление предложения -------------------------------------------


def proposal(
    kind: OfferKind = OfferKind.SELL,
    *,
    author: Party = ARGUS,
    target: Party = MERLA,
    giver_holds: int = 1,
    price: int = 100,
    author_gold: int = 500,
) -> Refusal | None:
    return check_proposal(
        kind=kind,
        author=author,
        target=target,
        giver_holds=giver_holds,
        quantity=1,
        price=price,
        author_gold=author_gold,
    )


def test_an_offer_to_yourself_is_refused() -> None:
    assert proposal(target=ARGUS) is Refusal.SELF


def test_selling_what_you_do_not_have_is_your_problem() -> None:
    assert proposal(OfferKind.SELL, giver_holds=0) is Refusal.AUTHOR_LACKS_ITEM


def test_buying_what_they_do_not_have_names_the_right_side() -> None:
    assert proposal(OfferKind.BUY, giver_holds=0) is Refusal.TARGET_LACKS_ITEM


def test_a_buyer_has_to_have_the_money_before_they_offer_it() -> None:
    """Золото ставится в ту минуту, когда предложение объявлено, значит, оно обязано быть."""
    assert proposal(OfferKind.BUY, price=100, author_gold=99) is Refusal.AUTHOR_LACKS_GOLD
    assert proposal(OfferKind.BUY, price=100, author_gold=100) is None


def test_the_targets_purse_is_never_read_to_publish_an_offer() -> None:
    """Проверять здесь золото адресата значило бы отвечать на вопрос, которого никто не задавал.

    У него есть пять минут, чтобы найти деньги; отказ вперёд и выдал бы его остаток,
    и запретил бы сделку, которую он мог бы совершить. Читается только собственный
    кошелёк автора, и только когда платит автор.
    """
    assert proposal(OfferKind.SELL, author_gold=0) is None


# --- закрытие ---------------------------------------------------------


def test_a_settlement_reads_only_the_side_that_is_answering() -> None:
    assert check_settlement(offer(), target_holds=0, target_gold=100, now=1000) is None


def test_a_buyer_who_spent_the_money_meanwhile_cannot_agree() -> None:
    """Вещь продавца в эскроу; открытый вопрос - кошелёк покупателя."""
    refusal = check_settlement(offer(OfferKind.SELL), target_holds=0, target_gold=99, now=1000)

    assert refusal is Refusal.TARGET_LACKS_GOLD


def test_a_seller_who_sold_it_elsewhere_cannot_agree_to_a_purchase() -> None:
    """Покупка держит золото покупателя, поэтому открытый вопрос - вещь."""
    refusal = check_settlement(offer(OfferKind.BUY), target_holds=0, target_gold=0, now=1000)

    assert refusal is Refusal.TARGET_LACKS_ITEM


def test_the_author_can_no_longer_break_their_own_offer() -> None:
    """Что бы автор ни поставил, с его стороны стола этого уже нет."""
    assert check_settlement(offer(OfferKind.BUY), target_holds=1, target_gold=0, now=1000) is None


def test_paying_the_exact_price_is_enough() -> None:
    assert check_settlement(offer(price=100), target_holds=0, target_gold=100, now=1000) is None


# --- передачи ---------------------------------------------------------


def test_a_gift_only_needs_the_stock() -> None:
    assert check_gift(author=ARGUS, target=MERLA, holds=3, quantity=3) is None
    assert check_gift(author=ARGUS, target=MERLA, holds=2, quantity=3) is Refusal.AUTHOR_LACKS_ITEM


def test_gold_cannot_be_given_to_yourself_or_out_of_thin_air() -> None:
    assert check_gold_gift(author=ARGUS, target=ARGUS, purse=500, amount=1) is Refusal.SELF
    assert (
        check_gold_gift(author=ARGUS, target=MERLA, purse=99, amount=100)
        is Refusal.AUTHOR_LACKS_GOLD
    )
    assert check_gold_gift(author=ARGUS, target=MERLA, purse=100, amount=100) is None


# --- номера -----------------------------------------------------------


def test_offer_numbers_stay_short_enough_to_type() -> None:
    assert next_number(0) == 1
    assert next_number(11) == 12
    assert next_number(MAX_OFFER_NUMBER) == 1


# --- как игрок называет вещь ------------------------------------------


CATALOGUE = (
    ItemOption(item_id="light_body@5#common", name="Кожаная броня"),
    ItemOption(item_id="leather_boots", name="Кожаные сапоги"),
    ItemOption(item_id="bronze_blade", name="Бронзовый клинок, щербатый"),
)


@pytest.mark.parametrize(
    "query",
    ["Кожаная броня", "кожаная броня", "  КОЖАНАЯ   БРОНЯ ", "light_body@5#common"],
)
def test_an_exact_name_wins_however_it_is_typed(query: str) -> None:
    found = match_items(query, CATALOGUE)

    assert [option.item_id for option in found] == ["light_body@5#common"]


def test_a_prefix_is_enough_when_it_is_unambiguous() -> None:
    found = match_items("бронзовый", CATALOGUE)

    assert [option.item_id for option in found] == ["bronze_blade"]


def test_an_ambiguous_prefix_returns_every_candidate() -> None:
    """Две кожаные вещи и одно слово: вызывающий обязан спросить, а не гадать."""
    found = match_items("кожа", CATALOGUE)

    assert len(found) == 2


def test_a_word_from_the_middle_still_finds_the_item() -> None:
    found = match_items("щербатый", CATALOGUE)

    assert [option.item_id for option in found] == ["bronze_blade"]


def test_nothing_matches_nothing() -> None:
    assert match_items("подорожная", CATALOGUE) == ()
    assert match_items("   ", CATALOGUE) == ()


def test_an_exact_name_beats_a_longer_one_that_contains_it() -> None:
    catalogue = (
        ItemOption(item_id="rope", name="Верёвка"),
        ItemOption(item_id="rope_long", name="Верёвка длинная"),
    )

    found = match_items("верёвка", catalogue)

    assert [option.item_id for option in found] == ["rope"]
