"""Имя игрока: что игра принимает, что отвергает и какими словами (ADR 0078)."""

from __future__ import annotations

import pytest

from mmorpg.domain.rules import guild, names


@pytest.mark.parametrize(
    "name",
    [
        "Аргус",
        "Мал",
        "Ли-Ан",
        "Дед Мороз",
        "O'Brien",
        "Игрок 2",
        "Тьен",
        "Мерла",
        "Борх",
        "Креан",
        "Тень Клинка",
        "Кхарн",
    ],
)
def test_a_name_of_an_adventurer_passes(name: str) -> None:
    assert names.refusal(name) == ""


@pytest.mark.parametrize("name", ["", "А", "   ", "1Игрок", "*звёздочка*", "и" * 21])
def test_length_and_signs_are_explained(name: str) -> None:
    assert names.refusal(name)


def test_two_alphabets_in_one_name_are_refused() -> None:
    """«Аргус» с латинской «A» звучит так же, а зовётся иначе."""
    assert names.refusal("Aргус")
    assert names.refusal("Аргус") == ""


def test_digits_are_counted() -> None:
    assert names.refusal("Игрок 12") == ""
    assert "Цифр" in names.refusal("Игрок123")


# --- брань ------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Пидорас",
        "ПИЗDа",
        "Хууй",
        "х у й",
        "Х Уй",
        "Су4ка",
        "Fucker",
        "Blyat",
        "Мразь",
        "eбalo",
        "Ёбарь",
        "Наебал",
        "Уебок",
    ],
)
def test_swearing_is_caught_however_it_is_written(name: str) -> None:
    assert names.is_profane(name)


@pytest.mark.parametrize(
    "name",
    [
        "Аргус",
        "Топ Издалека",
        "Сукно",
        "Хорёк",
        "Небеса",
        "Победа",
        "Дед Мороз",
        "Мирна",
        "Требуха",
        "Ребус",
        "Учеба",
        "Серебряный оплот",
    ],
)
def test_plain_words_are_not_swearing(name: str) -> None:
    """Корень «еба» ищут с начала слова: посреди него стоит «Требуха», а не брань.

    Склейка слов бранью тоже не делает: склеивают только обрывки в одну-две буквы.
    """
    assert not names.is_profane(name)


# --- настоящие имена --------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Александр",
        "Саша",
        "Иван Петров",
        "Мария",
        "John",
        "Smith",
        "Sasha",
        "Ivanovich",
        "Natasha",
        "Николаевич",
        "Петровна",
        "Сергей Иванов",
    ],
)
def test_a_real_person_name_is_refused(name: str) -> None:
    assert names.is_real_name(name)
    assert "настоящего человека" in names.refusal(name)


@pytest.mark.parametrize(
    "name", ["Иванна", "Романия", "Аргус", "Тьен", "Драконов", "Мерла", "Elyra", "Torn"]
)
def test_an_invented_name_is_not_a_real_one(name: str) -> None:
    """Ловится слово целиком: корень утащил бы за собой половину выдуманных имён."""
    assert not names.is_real_name(name)


# --- бессмыслица ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "фыва",
        "asdfgh",
        "qwerty",
        "йцукен",
        "Кфцщ",
        "Ааарон",
        "Ыыыы",
        "Штрбскл",
        "zxcvbnm",
    ],
)
def test_a_typed_at_random_name_is_refused(name: str) -> None:
    assert names.is_gibberish(name)
    assert "вслух" in names.refusal(name)


@pytest.mark.parametrize(
    "name", ["Аргус", "Мал", "Ли-Ан", "O'Brien", "Дорн", "Проба", "Бьёрн", "Игрок 2"]
)
def test_a_readable_name_is_not_gibberish(name: str) -> None:
    assert not names.is_gibberish(name)


def test_each_refusal_is_a_whole_phrase() -> None:
    """Отказ говорит, чем имя не годится, а не «недопустимое имя»."""
    for name in ("х у й", "Александр", "фыва", "Aргус", "Игрок123"):
        problem = names.refusal(name)
        assert problem.endswith((".", ":")) and len(problem.split()) >= 4


def test_a_real_name_written_in_latin_is_read_back() -> None:
    """«Sasha» звучит вслух ровно как «Саша», и отказ у них один."""
    assert names.is_real_name("Sasha")
    assert not names.is_real_name("Shadowmark")


def test_a_real_name_is_refused_before_it_reads_as_gibberish() -> None:
    """«Смит» - ряд клавиатуры и фамилия сразу; сказать надо про фамилию."""
    assert "настоящего человека" in names.refusal("Смит")


# --- имя гильдии ------------------------------------------------------


@pytest.mark.parametrize("name", ["Артель", "Серебряный оплот", "Товарищество Петрова", "Клинки"])
def test_a_guild_name_of_its_own_passes(name: str) -> None:
    assert guild.name_refusal(name) == ""


@pytest.mark.parametrize("name", ["Гандоны", "фывафыва", "Пидорасы"])
def test_a_guild_name_goes_through_the_same_censor(name: str) -> None:
    assert guild.name_refusal(name)
