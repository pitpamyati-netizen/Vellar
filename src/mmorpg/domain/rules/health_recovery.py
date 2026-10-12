"""Восстановление вне боя по явно переданному времени, без потери долей."""

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules.stats import derived_stats

# Скорость округляется до миллионной очка в минуту; остаток хранится целым числом.
UNITS = 60_000_000


def restart(character: Character, *, now: int) -> Character:
    """После выхода из боя отсчёт начинается заново: ожидание хода не лечит."""
    return replace(
        character,
        health_recovered_at=max(now, character.health_recovered_at or 0),
        health_recovery_remainder=0,
    )


def recover(content: GameContent, character: Character, *, now: int) -> Character:
    stats = derived_stats(content, character)
    health = character.health_or(stats.max_health)
    if health >= stats.max_health:
        return (
            replace(character, health_recovered_at=None, health_recovery_remainder=0)
            if character.health_recovery_remainder or character.health_recovered_at is not None
            else character
        )
    previous = character.health_recovered_at
    if previous is None:
        return replace(character, health_recovered_at=now, health_recovery_remainder=0)
    if now <= previous:
        return character
    elapsed = now - previous
    rate = round(stats.health_recovery_per_minute * 1_000_000)
    gained, remainder = divmod(elapsed * rate + character.health_recovery_remainder, UNITS)
    healed = min(stats.max_health, health + gained)
    return replace(
        character,
        health=healed,
        health_recovered_at=now,
        health_recovery_remainder=0 if healed == stats.max_health else remainder,
    )
