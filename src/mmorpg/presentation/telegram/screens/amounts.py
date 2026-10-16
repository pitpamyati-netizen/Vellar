"""Размер вложения характеристик: одни подписи при создании и в игре."""

from mmorpg.presentation.telegram.keyboards.labels import Label, label

STAT_AMOUNTS = (1, 5, 10, 50, 100)


def stat_amount_label(amount: int) -> Label:
    return label(f"Вкладывать по {amount}")


def stat_amount(selected: int, available: int) -> int:
    return selected if selected in STAT_AMOUNTS and selected <= available else 1


def stat_amount_rows(available: int) -> tuple[tuple[Label, ...], ...]:
    choices = [stat_amount_label(amount) for amount in STAT_AMOUNTS if amount <= available]
    return tuple(tuple(choices[index : index + 2]) for index in range(0, len(choices), 2))
