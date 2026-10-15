"""Короткие числовые пояснения правил, одинаковые при любом значении."""

from __future__ import annotations


def plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(count)
    if 11 <= count % 100 <= 14:
        return many
    if count % 10 == 1:
        return one
    return few if count % 10 in {2, 3, 4} else many


def percentage(value: float) -> str:
    number = round(value, 2)
    text = f"{number:g}".replace(".", ",")
    word = (
        plural(int(number), "процент", "процента", "процентов")
        if number.is_integer()
        else "процента"
    )
    return f"{text} {word}"


def percentage_points(value: float) -> str:
    number = round(value, 2)
    text = f"{number:g}".replace(".", ",")
    word = (
        plural(int(number), "процентный пункт", "процентных пункта", "процентных пунктов")
        if number.is_integer()
        else "процентного пункта"
    )
    return f"{text} {word}"
