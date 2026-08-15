"""Channel posts.

The channel carries news about the game, never a feed of what players did, so
these tests guard two things: the wording rules from ``Narrative.md`` ("Канал"),
and the fact that a dead channel cannot break the caller.
"""

from __future__ import annotations

import pytest

from mmorpg.presentation.telegram import broadcast as bc
from tests.conftest import SOURCE_ROOT

# Where gameplay lives. Nothing here may reach the channel: a level, a kill or a
# trade concerns the people in the group, and only them (Roadmap 2.4).
GAMEPLAY_DIRS = ("handlers", "flows", "screens")


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


def test_the_channel_posts_only_game_news() -> None:
    """No per-player events. Adding one back should fail this test first."""
    assert {kind.value for kind in bc.BroadcastKind} == {"news", "changelog", "service"}


def test_headline_comes_first_and_stands_alone() -> None:
    event = bc.news("Открыто Стрешин.", "Четыре тракта, пять локаций.")
    text = bc.render_broadcast(event, emoji=False)

    assert text.splitlines()[0] == "Открыто Стрешин."


def test_emoji_is_optional_and_never_the_only_meaning() -> None:
    event = bc.service("Технические работы, полчаса.")

    with_emoji = bc.render_broadcast(event, emoji=True)
    without = bc.render_broadcast(event, emoji=False)

    assert with_emoji.endswith(without)
    assert without == "Технические работы, полчаса."


def test_a_changelog_reads_as_news_for_players() -> None:
    event = bc.changelog(
        "0.2",
        added=("Арена: бой между игроками на три круга.",),
        fixed=("Лавка больше не забывает выкупленный товар.",),
    )
    text = bc.render_broadcast(event, emoji=False)

    assert text.splitlines()[0] == "Обновление 0.2."
    # Sections with no entries are dropped, not left as empty headings.
    assert "Изменилось:" not in text
    assert "Добавлено:" in text and "Исправлено:" in text


def test_an_empty_changelog_is_refused() -> None:
    with pytest.raises(ValueError, match="not an update"):
        bc.changelog("0.3")


def test_a_changelog_may_be_longer_than_a_notice() -> None:
    assert bc.limit_for(bc.BroadcastKind.CHANGELOG) > bc.limit_for(bc.BroadcastKind.NEWS)


@pytest.mark.parametrize(
    "event",
    [
        bc.news("Открыто Стрешин."),
        bc.service("Бот перезапущен."),
        bc.changelog("0.2", added=("Арена.",)),
    ],
    ids=lambda event: event.kind.value,
)
def test_every_post_is_plain_short_text(event: bc.BroadcastEvent) -> None:
    text = bc.render_broadcast(event)

    assert len(text) <= bc.limit_for(event.kind)
    # Markdown is spoken aloud by screen readers (accessibility rule 14).
    assert not set(text) & set("*_`[]")
    # No pseudo-graphics (rule 5).
    assert "#" not in text and "|" not in text


def test_no_gameplay_code_posts_to_the_channel() -> None:
    """The guard for "player actions stay in the group" is mechanical, not a habit."""
    telegram = SOURCE_ROOT / "presentation" / "telegram"
    offenders = [
        path.name
        for directory in GAMEPLAY_DIRS
        for path in sorted((telegram / directory).rglob("*.py"))
        if ".announce(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"gameplay code announces to the channel: {offenders}"


@pytest.mark.parametrize(
    "headline",
    [
        "Добавлен модуль арены.",
        "Починили баг с лавкой.",
        "Выкатили хотфикс.",
        "Обновлён API бота.",
    ],
)
def test_a_post_written_for_the_team_is_refused(headline: str) -> None:
    with pytest.raises(ValueError, match="what a player can do"):
        bc.render_broadcast(bc.news(headline))


def test_the_same_fact_passes_when_it_is_said_to_players() -> None:
    text = bc.render_broadcast(bc.news("Лавка больше не забывает выкупленный товар."), emoji=False)

    assert bc.jargon_in(text) is None


def test_a_word_that_only_looks_like_jargon_is_left_alone() -> None:
    """Вырь is a place in Веллар, not a defect (``Narrative.md``, 7)."""
    assert bc.jargon_in("Открыт Вырь.") is None


def test_an_empty_headline_is_refused() -> None:
    with pytest.raises(ValueError, match="headline"):
        bc.news("   ")


def test_an_over_long_post_is_refused_at_render() -> None:
    with pytest.raises(ValueError, match="limit"):
        bc.render_broadcast(bc.news("а" * 800))


async def test_an_unconfigured_channel_is_a_no_op() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="")

    assert broadcaster.enabled is False
    assert await broadcaster.announce(bc.news("Что-то открылось.")) is False
    assert sink.sent == []


async def test_a_configured_channel_receives_exactly_one_message() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="-1001234567890")

    assert await broadcaster.announce(bc.news("Открыта арена.")) is True
    assert len(sink.sent) == 1
    chat_id, text = sink.sent[0]
    assert chat_id == -1001234567890
    assert text.endswith("Открыта арена.")


async def test_a_username_channel_is_passed_through_unchanged() -> None:
    sink = RecordingSink()
    broadcaster = bc.ChannelBroadcaster(sink=sink, chat_id="@vellar_game")

    await broadcaster.announce(bc.service("Проверка."))

    assert sink.sent[0][0] == "@vellar_game"


async def test_a_dead_channel_never_breaks_the_caller() -> None:
    broadcaster = bc.ChannelBroadcaster(sink=RecordingSink(fail=True), chat_id="@vellar_game")

    assert await broadcaster.announce(bc.service("Проверка.")) is False
