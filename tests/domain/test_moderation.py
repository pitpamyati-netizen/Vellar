"""Сроки блокировки: что значит «заблокирован» и когда это кончается.

Часов у домена нет, поэтому все проверки здесь считают от переданного момента —
и именно поэтому истёкший срок не требует ни задачи по расписанию, ни уборки.
"""

from __future__ import annotations

import pytest

from mmorpg.domain.entities.moderation import Ban, KeeperAction
from mmorpg.domain.rules import moderation as rules

NOW = 1_700_000_000


def test_every_sentence_has_a_name_and_a_key() -> None:
    keys = [sentence.key for sentence in rules.SENTENCES]
    names = [sentence.name for sentence in rules.SENTENCES]
    assert len(set(keys)) == len(keys)
    # Надписи кнопок обязаны различаться: маршрутизация идёт по тексту.
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("sentence", rules.SENTENCES)
def test_a_sentence_is_found_by_its_key_and_by_its_button(sentence: rules.Sentence) -> None:
    assert rules.sentence_of(sentence.key) is sentence
    assert rules.sentence_named(f"  {sentence.name.upper()}  ") is sentence


def test_an_unknown_sentence_is_not_invented() -> None:
    assert rules.sentence_of("век") is None
    assert rules.sentence_named("пока не надоест") is None


def test_a_timed_ban_ends_by_itself() -> None:
    ban = rules.imposed(rules.sentence_of("hour"), "ругался", now=NOW)

    assert rules.is_banned(ban, now=NOW)
    assert rules.remaining(ban, now=NOW) == rules.HOUR
    # Никто ничего не снимал: срок просто перестал действовать.
    assert not rules.is_banned(ban, now=NOW + rules.HOUR)
    assert rules.remaining(ban, now=NOW + rules.HOUR) == 0


def test_a_ban_without_an_end_does_not_depend_on_the_clock() -> None:
    ban = rules.imposed(rules.sentence_of("forever"), "обман в сделке", now=NOW)

    assert ban.forever
    assert rules.is_banned(ban, now=NOW + 100 * 365 * rules.DAY)
    assert rules.remaining(ban, now=NOW) == rules.FOREVER


def test_lifting_a_ban_takes_the_reason_with_it() -> None:
    lifted = rules.lifted()

    assert not rules.is_banned(lifted, now=NOW)
    assert lifted.reason == ""


def test_no_ban_at_all_is_the_default() -> None:
    assert not rules.is_banned(Ban(), now=NOW)


def test_the_reason_loses_the_spaces_around_it() -> None:
    ban = rules.imposed(rules.sentence_of("day"), "  оскорбления  ", now=NOW)
    assert ban.reason == "оскорбления"


def test_every_keeper_action_is_named_in_russian() -> None:
    """Журнал читает человек: действие без названия было бы строкой из кода."""
    assert set(rules.ACTIONS) == set(KeeperAction)
    assert all(name and name == name.lower() for name in rules.ACTIONS.values())
