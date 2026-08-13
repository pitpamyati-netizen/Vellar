"""Trades between players, end to end through the repositories.

Two properties are worth more than the rest: nothing is ever created or destroyed
by a trade, and an offer nobody may accept moves nothing at all.
"""

from __future__ import annotations

import pytest

from mmorpg.application.services.group_trade import GroupResult, GroupTrade
from mmorpg.application.services.offers import OfferStore
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.group_commands import parse_group_command
from mmorpg.domain.rules.group_offers import (
    OFFER_TTL_SECONDS,
    Offer,
    OfferKind,
    Party,
    Refusal,
)
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.content import load_content
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)
from tests.conftest import CONTENT_ROOT

ARGUS_ACCOUNT = 1
MERLA_ACCOUNT = 2
STRANGER_ACCOUNT = 3
NOW = 10_000
SWORD = "rusty_sword"
SWORD_NAME = "Ржавый меч"


@pytest.fixture(scope="module")
def content() -> GameContent:
    return load_content(CONTENT_ROOT)


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def inventory() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


@pytest.fixture
def trade(
    content: GameContent,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
) -> GroupTrade:
    return GroupTrade(
        content=content,
        characters=characters,
        inventory=inventory,
        offers=OfferStore(cache=InMemoryStateCache()),
    )


async def make(
    characters: InMemoryCharacterRepository,
    account: int,
    name: str,
    *,
    gold: int = 0,
) -> Character:
    return await characters.create(
        Character(
            id=0,
            user_id=account,
            name=name,
            race_id="human",
            class_id="warrior",
            gold=gold,
        )
    )


@pytest.fixture
async def argus(characters: InMemoryCharacterRepository) -> Character:
    return await make(characters, ARGUS_ACCOUNT, "Аргус", gold=500)


@pytest.fixture
async def merla(characters: InMemoryCharacterRepository) -> Character:
    return await make(characters, MERLA_ACCOUNT, "Мерла", gold=300)


async def run(trade: GroupTrade, text: str, *, author: int, target: int | None, now: int = NOW):
    command = parse_group_command(text)
    assert command is not None, text
    return await trade.run(command, author_id=author, target_id=target, now=now)


# --- profile ----------------------------------------------------------


async def test_a_profile_is_the_character_of_whoever_was_replied_to(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.result is GroupResult.PROFILE
    assert outcome.character is not None and outcome.character.name == "Мерла"
    assert outcome.stats is not None


async def test_a_player_without_a_character_gets_told_so(
    trade: GroupTrade, merla: Character
) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.NO_CHARACTER


async def test_replying_to_someone_who_never_played(trade: GroupTrade, argus: Character) -> None:
    outcome = await run(trade, "профиль", author=ARGUS_ACCOUNT, target=STRANGER_ACCOUNT)

    assert outcome.refusal is Refusal.TARGET_HAS_NO_CHARACTER


# --- hand-overs -------------------------------------------------------


async def test_gold_moves_immediately_and_nothing_is_created(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    argus: Character,
    merla: Character,
) -> None:
    before = argus.gold + merla.gold

    outcome = await run(trade, "передать 100 золота", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.result is GroupResult.GOLD_GIVEN
    after_argus = await characters.get(argus.id)
    after_merla = await characters.get(merla.id)
    assert after_argus is not None and after_merla is not None
    assert (after_argus.gold, after_merla.gold) == (400, 400)
    assert after_argus.gold + after_merla.gold == before


async def test_gold_you_do_not_have_stays_where_it_is(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    argus: Character,
    merla: Character,
) -> None:
    outcome = await run(trade, "передать 5000 золота", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.AUTHOR_LACKS_GOLD
    unchanged = await characters.get(argus.id)
    assert unchanged is not None and unchanged.gold == 500


async def test_an_item_is_handed_over_without_asking_the_receiver(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 2)

    outcome = await run(
        trade, f"передать 2 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.result is GroupResult.ITEM_GIVEN
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 2


async def test_an_item_nobody_holds_cannot_be_named(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(
        trade, "передать соляная печать", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_an_ambiguous_name_asks_instead_of_guessing(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, "iron_helm", 1)
    await inventory.add(argus.id, "iron_scrap", 1)

    outcome = await run(trade, "передать железный", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)

    assert outcome.refusal is Refusal.AMBIGUOUS_ITEM
    assert len(outcome.options) == 2
    assert await inventory.count(argus.id, "iron_helm") == 1


async def test_giving_to_yourself_is_refused(
    trade: GroupTrade, inventory: InMemoryInventoryRepository, argus: Character
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(trade, f"передать {SWORD_NAME}", author=ARGUS_ACCOUNT, target=ARGUS_ACCOUNT)

    assert outcome.refusal is Refusal.SELF
    assert await inventory.count(argus.id, SWORD) == 1


# --- offers -----------------------------------------------------------


async def test_a_sale_is_published_and_nothing_moves_yet(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.result is GroupResult.OFFER_MADE
    assert outcome.offer is not None
    assert outcome.offer.kind is OfferKind.SELL
    assert outcome.offer.price == 100
    # Published, not executed.
    assert await inventory.count(argus.id, SWORD) == 1
    still = await characters.get(merla.id)
    assert still is not None and still.gold == 300


async def test_accepting_a_sale_swaps_goods_for_gold(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    accepted = await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert accepted.result is GroupResult.OFFER_ACCEPTED
    assert await inventory.count(argus.id, SWORD) == 0
    assert await inventory.count(merla.id, SWORD) == 1
    seller = await characters.get(argus.id)
    buyer = await characters.get(merla.id)
    assert seller is not None and buyer is not None
    assert (seller.gold, buyer.gold) == (600, 200)


async def test_a_purchase_runs_the_same_trade_from_the_other_side(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(merla.id, SWORD, 1)
    made = await run(trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert await inventory.count(argus.id, SWORD) == 1
    payer = await characters.get(argus.id)
    seller = await characters.get(merla.id)
    assert payer is not None and seller is not None
    assert (payer.gold, seller.gold) == (400, 400)


async def test_a_stranger_cannot_accept_someone_elses_offer(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await make(characters, STRANGER_ACCOUNT, "Довен", gold=1000)
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    outcome = await run(trade, f"принять {made.offer.number}", author=STRANGER_ACCOUNT, target=None)

    assert outcome.refusal is Refusal.NOT_YOURS
    assert await inventory.count(argus.id, SWORD) == 1


async def test_the_author_may_cancel_but_not_accept(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    refused = await run(trade, f"принять {made.offer.number}", author=ARGUS_ACCOUNT, target=None)
    cancelled = await run(trade, f"отказ {made.offer.number}", author=ARGUS_ACCOUNT, target=None)

    assert refused.refusal is Refusal.NOT_YOURS
    assert cancelled.result is GroupResult.OFFER_DECLINED


async def test_a_declined_offer_is_gone_for_good(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    await run(trade, f"отказ {made.offer.number}", author=MERLA_ACCOUNT, target=None)
    again = await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert again.refusal is Refusal.UNKNOWN_OFFER


async def test_an_offer_older_than_five_minutes_refuses(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None

    outcome = await run(
        trade,
        f"принять {made.offer.number}",
        author=MERLA_ACCOUNT,
        target=None,
        now=NOW + OFFER_TTL_SECONDS,
    )

    assert outcome.refusal is Refusal.EXPIRED
    assert await inventory.count(argus.id, SWORD) == 1


async def test_a_buyer_who_spent_the_money_meanwhile_is_refused(
    trade: GroupTrade,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    """Nothing is held in escrow, so the balance is read when the answer arrives."""
    await inventory.add(argus.id, SWORD, 1)
    made = await run(trade, f"продать 400 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    assert made.offer is not None
    await characters.save(merla.with_gold(-250))

    outcome = await run(trade, f"принять {made.offer.number}", author=MERLA_ACCOUNT, target=None)

    assert outcome.refusal is Refusal.TARGET_LACKS_GOLD
    assert await inventory.count(argus.id, SWORD) == 1


async def test_selling_something_you_do_not_own_is_refused_up_front(
    trade: GroupTrade, argus: Character, merla: Character
) -> None:
    outcome = await run(
        trade, f"продать 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_buying_reads_the_targets_pack_not_your_own(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)

    outcome = await run(
        trade, f"купить 100 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert outcome.refusal is Refusal.UNKNOWN_ITEM


async def test_offers_get_distinct_numbers(
    trade: GroupTrade,
    inventory: InMemoryInventoryRepository,
    argus: Character,
    merla: Character,
) -> None:
    await inventory.add(argus.id, SWORD, 1)
    await inventory.add(argus.id, "militia_spear", 1)

    first = await run(trade, f"продать 10 {SWORD_NAME}", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT)
    second = await run(
        trade, "продать 20 Ополченское копьё", author=ARGUS_ACCOUNT, target=MERLA_ACCOUNT
    )

    assert first.offer is not None and second.offer is not None
    assert first.offer.number != second.offer.number


# --- where the offers are kept ----------------------------------------


async def test_a_taken_number_is_stepped_over_not_overwritten() -> None:
    """A wrap must not hand two live offers the same number."""
    store = OfferStore(cache=InMemoryStateCache())
    first = await store.reserve_number()
    await store.put(
        Offer(
            number=first,
            kind=OfferKind.SELL,
            author=Party(user_id=1, character_id=1, name="Аргус"),
            target=Party(user_id=2, character_id=2, name="Мерла"),
            item_id=SWORD,
            item_name=SWORD_NAME,
            price=10,
            created_at=NOW,
        )
    )

    second = await store.reserve_number()

    assert second != first
    kept = await store.get(first)
    assert kept is not None and kept.price == 10


async def test_an_offer_survives_the_round_trip_through_the_cache() -> None:
    store = OfferStore(cache=InMemoryStateCache())
    original = Offer(
        number=5,
        kind=OfferKind.BUY,
        author=Party(user_id=1, character_id=1, name="Аргус"),
        target=Party(user_id=2, character_id=2, name="Мерла"),
        item_id=SWORD,
        item_name=SWORD_NAME,
        price=250,
        quantity=1,
        created_at=NOW,
    )
    await store.put(original)

    assert await store.get(5) == original


async def test_a_payload_from_an_older_version_reads_as_no_offer() -> None:
    """A cache outlives deployments; an unreadable value is absent, not a crash."""
    cache = InMemoryStateCache()
    store = OfferStore(cache=cache)
    await cache.set(store._key(5), '{"number": 5}', 60)

    assert await store.get(5) is None
