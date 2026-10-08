"""Ссылка на физическую вещь. Случайность и выдача номера остаются в хранилище."""

from __future__ import annotations


def template_id(reference: str) -> str:
    return reference.partition("!")[0]


def instance_ids(reference: str) -> tuple[int, ...]:
    _, mark, tail = reference.partition("!")
    if not mark:
        return ()
    parts = tail.split(",")
    if not all(part.isdecimal() and int(part) > 0 for part in parts):
        raise ValueError("Invalid item instance reference")
    ids = tuple(int(part) for part in parts)
    if len(ids) != len(set(ids)):
        raise ValueError("Repeated item instance")
    return ids


def references(reference: str) -> tuple[str, ...]:
    return tuple(f"{template_id(reference)}!{number}" for number in instance_ids(reference))


def with_template(reference: str, template: str) -> str:
    _, mark, tail = reference.partition("!")
    return f"{template}!{tail}" if mark else template
