"""Channel broadcasts.

The channel is player-facing text, so it is held to the same rules as a screen:
plain text, the key fact first, emoji that carry nothing on their own, and a hard
length limit. See ``Narrative.md`` for the wording rules these tests guard.
"""

from __future__ import annotations

import pytest

from mmorpg.presentation.telegram import broadcast as bc


class RecordingSink:
    """A Telegram stand-in that remembers what it was asked to send."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int | str, str]] = []
        self.fail = fail

    async def send_message(self, chat_id: int | str, text: str) -> object:
        if self.fail:
            msg = "chat not found"
            raise RuntimeError(msg)
        self.sent.append((chat_id, text))
        return object()


def test_headline_comes_first_and_stands_alone() -> None:
    event = bc.trade_completed("Аргус", "Мерла", "Соляной клинок", 240)
    text = bc.render_broadcast(event, emoji=False)

    assert text.splitlines()[0] == "Сделка закрыта: Соляной клинок, 240 золотых."


def test_emoji_is_optional_and_never_the_only_meaning() -> None:
    event = bc.level_reached("Аргус", 100)

    with_emoji = bc.render_broadcast(event, emoji=True)
    without = bc.render_broadcast(event, emoji=False)

    assert with_emoji.endswith(without)
    assert without == "Аргус достигает 100 уровня."
    assert without.strip()


@pytest.mark.parametrize(
    "event",
    [
        bc.level_reached("Аргус", 50),
        bc.city_unlocked("Аргус", "Медный Перекрёсток", 70),
        bc.arena_result("Аргус", "Мерла", 3),
        bc.arena_result("Аргус", "Мерла", 3, timeout=True),
        bc.trade_completed("Аргус", "Мерла", "Соляной клинок", 240),
        bc.boss_defeated("Аргус", "Смотритель Отлива", "Утопленный Храм"),
        bc.cycle_turned(1200),
    ],
    ids=lambda event: event.kind.value,
)
def test_every_event_is_plain_short_text(event: bc.BroadcastEvent) -> None:
    text = bc.render_broadcast(event)

    assert len(text) <= bc.BROADCAST_LIMIT
    # Markdown is spoken aloud by screen readers (accessibility rule 14).
    assert not set(text) & set("*_`[]")
    # No pseudo-graphics (rule 5).
    assert "#" not in text and "|" not in text


def test_an_empty_headline_is_refused() -> None:
    with pytest.raises(ValueError, match="headline"):
        bc.BroadcastEvent(kind=bc.BroadcastKind.SERVICE, headline="   ")


def test_an_over_long_post_is_refused_at_render() -> None:
    event = bc.BroadcastEvent(kind=bc.BroadcastKind.SERVICE, headline="а" * 800)

    with pytest.raises(ValueError, match="limit"):
        bc.render_broadcast(event)


async def test_an_unconfigured_channel_is_a_no_op() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="")

    assert broadcaster.enabled is False
    assert await broadcaster.announce(bc.cycle_turned(1)) is False
    assert sink.sent == []


async def test_a_configured_channel_receives_exactly_one_message() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="-1001234567890")

    assert await broadcaster.announce(bc.level_reached("Аргус", 300)) is True
    assert len(sink.sent) == 1
    chat_id, text = sink.sent[0]
    assert chat_id == -1001234567890
    assert text.endswith("Аргус достигает 300 уровня.")


async def test_a_username_channel_is_passed_through_unchanged() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="@vellar_game")

    await broadcaster.announce(bc.cycle_turned(7))

    assert sink.sent[0][0] == "@vellar_game"


async def test_a_dead_channel_never_breaks_the_caller() -> None:
    broadcaster = bc.ChannelBroadcaster(sink=RecordingSink(fail=True), chat_id="@vellar_game")

    assert await broadcaster.announce(bc.cycle_turned(7)) is False


@pytest.mark.parametrize("level", [1, 10, 50, 100, 300])
def test_milestone_levels_are_posted(level: int) -> None:
    assert bc.is_notable_level(level)


@pytest.mark.parametrize("level", [2, 11, 99, 101, 299])
def test_ordinary_levels_are_not_posted(level: int) -> None:
    assert not bc.is_notable_level(level)
