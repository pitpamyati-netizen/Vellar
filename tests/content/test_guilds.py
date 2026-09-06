"""Лестница ступеней гильдии: она идёт вверх, и каждая ступень что-то даёт (ADR 0076)."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from mmorpg.domain.entities import GameContent
from mmorpg.domain.rules.guild import MAX_MEMBERS
from mmorpg.infrastructure.content.loader import ContentError, load_content


def test_the_ladder_starts_at_nothing_and_climbs(content: GameContent) -> None:
    tiers = content.guild_tiers
    assert tiers, "гильдии нужна хотя бы одна ступень"
    assert tiers[0].deeds == 0, "первая ступень стоит гильдии из одного человека"
    for below, above in pairwise(tiers):
        assert above.level == below.level + 1
        assert above.deeds > below.deeds
        assert above.seats >= below.seats
        assert (above.seats, above.exp_percent, above.gold_percent) > (
            below.seats,
            below.exp_percent,
            below.gold_percent,
        ), f"ступень {above.level} не даёт ничего сверх {below.level}"


def test_no_tier_promises_more_seats_than_a_guild_holds(content: GameContent) -> None:
    for tier in content.guild_tiers:
        assert tier.seats <= MAX_MEMBERS, tier.name
        assert tier.name


def test_the_tier_is_found_by_deeds_and_never_falls_between(content: GameContent) -> None:
    tiers = content.guild_tiers
    assert content.guild_tier_at(0) is tiers[0]
    assert content.guild_tier_at(-100) is tiers[0], "деяний не бывает меньше нуля"
    assert content.guild_tier_at(tiers[1].deeds - 1) is tiers[0]
    assert content.guild_tier_at(tiers[1].deeds) is tiers[1]
    assert content.guild_tier_at(tiers[-1].deeds * 100) is tiers[-1]
    assert content.guild_tier_after(tiers[-1].deeds) is None
    assert content.guild_tier_after(0) is tiers[1]


def _sandbox(tmp_path: Path) -> Path:
    for name in Path("content").glob("*.toml"):
        (tmp_path / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_a_ladder_going_nowhere_is_refused(tmp_path: Path) -> None:
    """Ступень, которую игрок берёт и не замечает, — не ступень."""
    root = _sandbox(tmp_path)
    broken = (root / "guilds.toml").read_text(encoding="utf-8")
    second = """level = 2
name = "Артель"
deeds = 150
seats = 15
exp_percent = 0
gold_percent = 2"""
    assert second in broken
    flat = """level = 2
name = "Артель"
deeds = 150
seats = 12
exp_percent = 0
gold_percent = 0"""
    (root / "guilds.toml").write_text(broken.replace(second, flat), encoding="utf-8")
    with pytest.raises(ContentError) as failure:
        load_content(root)
    assert "guilds.toml" in str(failure.value)


def test_a_tier_wider_than_a_guild_is_refused(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    broken = (root / "guilds.toml").read_text(encoding="utf-8")
    (root / "guilds.toml").write_text(broken.replace("seats = 30", "seats = 300"), encoding="utf-8")
    with pytest.raises(ContentError) as failure:
        load_content(root)
    assert "guilds.toml" in str(failure.value)


def test_a_ladder_that_starts_above_zero_is_refused(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    broken = (root / "guilds.toml").read_text(encoding="utf-8")
    first = """name = "Товарищество"
deeds = 0"""
    assert first in broken
    (root / "guilds.toml").write_text(
        broken.replace(
            first,
            """name = "Товарищество"
deeds = 40""",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as failure:
        load_content(root)
    assert "guilds.toml" in str(failure.value)
