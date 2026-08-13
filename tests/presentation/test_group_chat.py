"""The bot in the game group.

The group is the one place where the bot speaks in front of people who did not
ask it anything, so most of these tests are about **not** speaking: wrong chat,
wrong shape, nobody addressed, too many commands. The rest check that what it does
say is short, plain, gender-neutral and answerable by exactly one person.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message, User

from mmorpg.application.services.group_trade import GroupOutcome, GroupResult
from mmorpg.config import Settings
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.group_commands import GroupIntent, parse_group_command
from mmorpg.domain.rules.group_offers import Offer, OfferKind, Party, Refusal
from mmorpg.infrastructure.cache import InMemoryStateCache
from mmorpg.infrastructure.persistence import (
    InMemoryCharacterRepository,
    InMemoryInventoryRepository,
)
from mmorpg.presentation.telegram.cleanup import MessageReaper
from mmorpg.presentation.telegram.handlers.group import handle_group_message, is_game_group
from mmorpg.presentation.telegram.screens import group as group_screens
from mmorpg.presentation.telegram.screens.format import MESSAGE_LIMIT
from mmorpg.presentation.telegram.throttle import RateLimiter

GROUP_ID = -1001234567890
OTHER_CHAT = -1009999999999
ARGUS_ACCOUNT = 11
MERLA_ACCOUNT = 22
NOW = 10_000
SWORD = "rusty_sword"
SWORD_NAME = "Ржавый меч"

ARGUS = Party(user_id=ARGUS_ACCOUNT, character_id=1, name="Аргус")
MERLA = Party(user_id=MERLA_ACCOUNT, character_id=2, name="Мерла")


# --- doubles ----------------------------------------------------------


class FakeBot:
    """Records what would have been sent and deleted."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.deleted: list[tuple[int, int]] = []
        self._next_id = 500

    async def send_message(self, **kwargs):
        self._next_id += 1
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=self._next_id)

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


def account(user_id: int, name: str, *, is_bot: bool = False) -> User:
    return User(id=user_id, is_bot=is_bot, first_name=name)


def message(
    text: str,
    *,
    sender: User,
    reply_to: Message | None = None,
    message_id: int = 100,
    chat_id: int = GROUP_ID,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup"),
        from_user=sender,
        text=text,
        reply_to_message=reply_to,
    )


@pytest.fixture
def argus_account() -> User:
    return account(ARGUS_ACCOUNT, "Аргус")


@pytest.fixture
def merla_account() -> User:
    return account(MERLA_ACCOUNT, "Мерла")


@pytest.fixture
def characters() -> InMemoryCharacterRepository:
    return InMemoryCharacterRepository()


@pytest.fixture
def inventory() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, group_id=str(GROUP_ID))  # type: ignore[call-arg]


@pytest.fixture
async def world(
    characters: InMemoryCharacterRepository, inventory: InMemoryInventoryRepository
) -> dict[str, Character]:
    people = {}
    for user_id, name in ((ARGUS_ACCOUNT, "Аргус"), (MERLA_ACCOUNT, "Мерла")):
        people[name] = await characters.create(
            Character(
                id=0, user_id=user_id, name=name, race_id="human", class_id="warrior", gold=300
            )
        )
    await inventory.add(people["Аргус"].id, SWORD, 1)
    return people


@pytest.fixture
def reaper() -> MessageReaper:
    return MessageReaper(delay=0.0)


@pytest.fixture
def bus(
    content: GameContent,
    settings: Settings,
    characters: InMemoryCharacterRepository,
    inventory: InMemoryInventoryRepository,
    reaper: MessageReaper,
):
    """Everything ``handle_group_message`` needs, in one callable."""
    bot = FakeBot()
    state = InMemoryStateCache()
    limiter = RateLimiter()

    async def deliver(msg: Message, *, now: int = NOW):
        return await handle_group_message(
            msg,
            bot=bot,  # type: ignore[arg-type]
            settings=settings,
            content=content,
            characters=characters,
            inventory=inventory,
            state_cache=state,
            limiter=limiter,
            reaper=reaper,
            now=now,
        )

    return SimpleNamespace(deliver=deliver, bot=bot, limiter=limiter)


async def drain(reaper: MessageReaper) -> None:
    """Let the scheduled deletions run. The fixture's delay is zero."""
    for _ in range(100):
        if not reaper.pending:
            return
        await asyncio.sleep(0)


# --- when the bot stays quiet -----------------------------------------


async def test_a_chat_that_is_not_the_game_group_is_ignored(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("привет", sender=merla_account, message_id=1, chat_id=OTHER_CHAT)

    sent = await bus.deliver(
        message("профиль", sender=argus_account, reply_to=target, chat_id=OTHER_CHAT)
    )

    assert sent is None
    assert bus.bot.sent == []


async def test_ordinary_conversation_is_never_answered(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("вчера был отлив", sender=merla_account, message_id=1)

    await bus.deliver(message("да, зарубки не сошлись", sender=argus_account, reply_to=target))

    assert bus.bot.sent == []


async def test_a_command_shouted_into_the_room_addresses_nobody(
    bus, argus_account: User, world
) -> None:
    """The reply *is* how the target is named, so without one there is no target."""
    await bus.deliver(message("профиль", sender=argus_account))

    assert bus.bot.sent == []


async def test_replying_to_the_bot_is_not_a_command_at_a_player(
    bus, argus_account: User, world
) -> None:
    bot_message = message(
        "Предложение 1.", sender=account(999, "Веллар", is_bot=True), message_id=1
    )

    await bus.deliver(message("профиль", sender=argus_account, reply_to=bot_message))

    assert bus.bot.sent == []


async def test_a_half_typed_command_is_silence_not_a_correction(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("тут", sender=merla_account, message_id=1)

    await bus.deliver(message("продать кожаная броня", sender=argus_account, reply_to=target))

    assert bus.bot.sent == []


# --- answering --------------------------------------------------------


async def test_a_profile_answers_the_person_who_asked(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("я тут", sender=merla_account, message_id=1)

    await bus.deliver(message("профиль", sender=argus_account, reply_to=target, message_id=2))

    assert len(bus.bot.sent) == 1
    posted = bus.bot.sent[0]
    assert posted["chat_id"] == GROUP_ID
    assert posted["text"].startswith("Мерла, уровень")
    # Anchored to the asker: a profile has no buttons and needs no target.
    assert posted["reply_parameters"].message_id == 2
    assert posted["reply_markup"] is None


async def test_a_profile_never_shows_what_a_player_carries(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("я тут", sender=merla_account, message_id=1)

    await bus.deliver(message("профиль", sender=argus_account, reply_to=target))

    text = bus.bot.sent[0]["text"]
    assert "золот" not in text.casefold()


async def test_an_offer_is_anchored_to_its_target_and_carries_two_buttons(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("что продаёшь", sender=merla_account, message_id=7)

    await bus.deliver(
        message(f"продать 100 {SWORD_NAME}", sender=argus_account, reply_to=target, message_id=8)
    )

    posted = bus.bot.sent[0]
    # The anchor is what makes the keyboard selective: only Merla sees it.
    assert posted["reply_parameters"].message_id == 7
    markup = posted["reply_markup"]
    assert markup.selective is True
    assert [button.text for button in markup.keyboard[0]] == ["Принять 1", "Отказ 1"]


async def test_the_buttons_are_the_commands_they_duplicate(
    bus, argus_account: User, merla_account: User, world
) -> None:
    """Pressing a button and typing it must reach the same place (rule 10)."""
    target = message("что продаёшь", sender=merla_account, message_id=7)
    await bus.deliver(message(f"продать 100 {SWORD_NAME}", sender=argus_account, reply_to=target))
    accept, decline = (button.text for button in bus.bot.sent[0]["reply_markup"].keyboard[0])

    parsed_accept = parse_group_command(accept)
    parsed_decline = parse_group_command(decline)

    assert parsed_accept is not None and parsed_accept.intent is GroupIntent.ACCEPT
    assert parsed_decline is not None and parsed_decline.intent is GroupIntent.DECLINE
    assert parsed_accept.amount == parsed_decline.amount == 1


async def test_an_answer_needs_no_reply_and_takes_the_buttons_back(
    bus, argus_account: User, merla_account: User, world, inventory
) -> None:
    target = message("что продаёшь", sender=merla_account, message_id=7)
    await bus.deliver(message(f"продать 100 {SWORD_NAME}", sender=argus_account, reply_to=target))

    await bus.deliver(message("Принять 1", sender=merla_account, message_id=9))

    posted = bus.bot.sent[1]
    assert "принято" in posted["text"]
    assert posted["reply_markup"].selective is True
    assert posted["reply_parameters"].message_id == 9
    assert await inventory.count(world["Мерла"].id, SWORD) == 1


async def test_a_stranger_pressing_the_button_gets_a_refusal_not_the_goods(
    bus, argus_account: User, merla_account: User, world, characters, inventory
) -> None:
    stranger = account(33, "Довен")
    await characters.create(
        Character(id=0, user_id=33, name="Довен", race_id="human", class_id="warrior", gold=999)
    )
    target = message("что продаёшь", sender=merla_account, message_id=7)
    await bus.deliver(message(f"продать 100 {SWORD_NAME}", sender=argus_account, reply_to=target))

    await bus.deliver(message("Принять 1", sender=stranger))

    assert "не вам" in bus.bot.sent[1]["text"]
    assert await inventory.count(world["Аргус"].id, SWORD) == 1


# --- the five minutes -------------------------------------------------


async def test_everything_the_bot_says_in_the_group_is_put_on_the_clock(
    bus, argus_account: User, merla_account: User, world, reaper: MessageReaper
) -> None:
    target = message("я тут", sender=merla_account, message_id=1)

    sent = await bus.deliver(message("профиль", sender=argus_account, reply_to=target))
    await drain(reaper)

    assert bus.bot.deleted == [(GROUP_ID, sent)]


async def test_a_failed_deletion_never_escapes(reaper: MessageReaper) -> None:
    async def refuse(chat_id: int, message_id: int) -> None:
        msg = "message to delete not found"
        raise RuntimeError(msg)

    reaper.schedule(refuse, GROUP_ID, 1)
    await drain(reaper)

    assert reaper.pending == 0


async def test_shutdown_cancels_pending_deletions() -> None:
    deleted: list[int] = []

    async def record(chat_id: int, message_id: int) -> None:
        deleted.append(message_id)

    reaper = MessageReaper(delay=60.0)
    reaper.schedule(record, GROUP_ID, 1)
    await reaper.aclose()

    assert reaper.pending == 0
    assert deleted == []


# --- flooding ---------------------------------------------------------


def test_a_burst_is_stopped_after_the_limit() -> None:
    clock = [0.0]
    limiter = RateLimiter(limit=3, window=60.0, clock=lambda: clock[0])

    allowed = [limiter.allow(ARGUS_ACCOUNT) for _ in range(5)]

    assert allowed == [True, True, True, False, False]


def test_the_window_slides_rather_than_resetting() -> None:
    clock = [0.0]
    limiter = RateLimiter(limit=2, window=60.0, clock=lambda: clock[0])
    limiter.allow(ARGUS_ACCOUNT)
    limiter.allow(ARGUS_ACCOUNT)

    clock[0] = 59.0
    blocked = limiter.allow(ARGUS_ACCOUNT)
    clock[0] = 61.0
    freed = limiter.allow(ARGUS_ACCOUNT)

    assert blocked is False
    assert freed is True


def test_one_player_flooding_does_not_silence_another() -> None:
    limiter = RateLimiter(limit=1)
    limiter.allow(ARGUS_ACCOUNT)

    assert limiter.allow(ARGUS_ACCOUNT) is False
    assert limiter.allow(MERLA_ACCOUNT) is True


def test_a_blocked_player_is_told_once_and_then_left_alone() -> None:
    limiter = RateLimiter(limit=1)
    limiter.allow(ARGUS_ACCOUNT)
    limiter.allow(ARGUS_ACCOUNT)

    assert limiter.should_warn(ARGUS_ACCOUNT) is True
    assert limiter.should_warn(ARGUS_ACCOUNT) is False


async def test_a_flood_gets_one_answer_and_then_silence(
    bus, argus_account: User, merla_account: User, world
) -> None:
    target = message("я тут", sender=merla_account, message_id=1)
    bus.limiter.limit = 2

    for _ in range(6):
        await bus.deliver(message("профиль", sender=argus_account, reply_to=target))

    texts = [posted["text"] for posted in bus.bot.sent]
    assert len(texts) == 3
    assert texts[2].startswith("Слишком много команд")


# --- what the group actually reads ------------------------------------


def offer(kind: OfferKind = OfferKind.SELL) -> Offer:
    return Offer(
        number=12,
        kind=kind,
        author=ARGUS,
        target=MERLA,
        item_id=SWORD,
        item_name=SWORD_NAME,
        price=100,
        created_at=NOW,
    )


def test_every_refusal_has_words(content: GameContent) -> None:
    """A refusal the bot cannot phrase would come out as silence."""
    for refusal in Refusal:
        text = group_screens.render(
            content, GroupOutcome(result=GroupResult.REFUSED, refusal=refusal)
        ).text
        assert text.strip()


def test_an_ambiguous_name_lists_what_it_could_have_been(content: GameContent) -> None:
    reply = group_screens.render(
        content,
        GroupOutcome(
            result=GroupResult.REFUSED,
            refusal=Refusal.AMBIGUOUS_ITEM,
            options=("Железный шлем", "Железный лом"),
        ),
    )

    assert "Железный шлем" in reply.text and "Железный лом" in reply.text


def test_a_profile_names_the_character_traits(content: GameContent) -> None:
    from mmorpg.domain.rules.stats import derived_stats

    hero = Character(
        id=1,
        user_id=ARGUS_ACCOUNT,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        trait_ids=("berserker",),
    )

    text = group_screens.profile_text(content, hero, derived_stats(content, hero))

    assert text.startswith("Аргус, уровень 1")
    assert "Особенности:" in text


def test_an_offer_says_who_gives_what_before_who_may_answer(content: GameContent) -> None:
    reply = group_screens.render(
        content, GroupOutcome(result=GroupResult.OFFER_MADE, offer=offer())
    )
    lines = reply.text.splitlines()

    assert lines[0].startswith("Предложение 12, продажа")
    assert "Отдаёт вещь: Аргус" in lines[1]
    assert "Отвечает только Мерла" in lines[2]


def test_a_purchase_is_worded_from_the_same_two_roles(content: GameContent) -> None:
    reply = group_screens.render(
        content, GroupOutcome(result=GroupResult.OFFER_MADE, offer=offer(OfferKind.BUY))
    )

    assert "покупка" in reply.text
    assert "Отдаёт вещь: Мерла. Платит: Аргус." in reply.text


@pytest.mark.parametrize(
    "outcome",
    [
        GroupOutcome(
            result=GroupResult.GOLD_GIVEN, gold=100, author_name="Аргус", target_name="Мерла"
        ),
        GroupOutcome(
            result=GroupResult.ITEM_GIVEN,
            item_name=SWORD_NAME,
            quantity=2,
            author_name="Аргус",
            target_name="Мерла",
        ),
        GroupOutcome(result=GroupResult.OFFER_MADE, offer=offer()),
        GroupOutcome(result=GroupResult.OFFER_ACCEPTED, offer=offer()),
        GroupOutcome(result=GroupResult.OFFER_DECLINED, offer=offer()),
        GroupOutcome(result=GroupResult.REFUSED, refusal=Refusal.EXPIRED),
    ],
    ids=lambda outcome: outcome.result.value,
)
def test_group_messages_are_plain_short_and_ungendered(
    content: GameContent, outcome: GroupOutcome
) -> None:
    text = group_screens.render(content, outcome).text

    assert len(text) <= MESSAGE_LIMIT
    # Markdown is spoken aloud by screen readers (accessibility rule 14).
    assert not set(text) & set("*_`[]")
    assert not set(text) & set("■□▓░█▒─│")
    # A past-tense verb would have to guess the character's gender.
    for gendered in ("передал", "получил", "продал", "купил"):
        assert gendered not in text.casefold()


def test_a_hand_over_names_both_sides_by_role(content: GameContent) -> None:
    reply = group_screens.render(
        content,
        GroupOutcome(
            result=GroupResult.GOLD_GIVEN, gold=100, author_name="Аргус", target_name="Мерла"
        ),
    )

    assert reply.text.splitlines()[0] == "Передача золота: 100 золотых."
    assert "Отправитель: Аргус. Получатель: Мерла." in reply.text


def test_a_group_reply_carries_no_service_row(content: GameContent) -> None:
    """The group is not a screen: there is nowhere to go "Назад" to."""
    reply = group_screens.render(
        content, GroupOutcome(result=GroupResult.OFFER_MADE, offer=offer())
    )

    assert "Назад" not in reply.text
    assert reply.buttons == ("Принять 12", "Отказ 12")


# --- which chat is the group ------------------------------------------


@pytest.mark.parametrize(
    ("configured", "chat", "expected"),
    [
        (str(GROUP_ID), Chat(id=GROUP_ID, type="supergroup"), True),
        (str(GROUP_ID), Chat(id=OTHER_CHAT, type="supergroup"), False),
        ("@vellar_chat", Chat(id=1, type="supergroup", username="vellar_chat"), True),
        ("vellar_chat", Chat(id=1, type="supergroup", username="vellar_chat"), True),
        ("@vellar_chat", Chat(id=1, type="supergroup", username="other"), False),
        ("@vellar_chat", Chat(id=1, type="supergroup"), False),
    ],
)
def test_the_group_is_recognised_by_id_or_by_username(
    configured: str, chat: Chat, expected: bool
) -> None:
    assert is_game_group(chat, configured) is expected
