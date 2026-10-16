"""Прежние пороги читаются для совместимости и больше не ограничивают ранги."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mmorpg.application.battle_snapshots import decode, encode
from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT


@pytest.mark.parametrize("legacy", ["", "rank_unlock_levels = [1, 10, 30, 60, 100]\n"])
def test_content_needs_no_rank_thresholds(tmp_path: Path, legacy: str) -> None:
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("[meta]\n", f"[meta]\n{legacy}"),
        encoding="utf-8",
    )
    assert load_content(tmp_path / "content").rules.rank_unlock_levels == ()


def test_old_battle_rules_with_thresholds_still_load(content: GameContent) -> None:
    saved = encode(content.rules)
    saved["fields"]["rank_unlock_levels"] = encode((1, 10, 30, 60, 100))
    assert decode(saved).rank_unlock_levels == (1, 10, 30, 60, 100)
    del saved["fields"]["rank_unlock_levels"]
    assert decode(saved).rank_unlock_levels == ()


@pytest.mark.parametrize("maximum", [0, -1])
def test_max_rank_still_requires_a_positive_limit(tmp_path: Path, maximum: int) -> None:
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("max_rank = 5", f"max_rank = {maximum}"),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="max_rank"):
        load_content(tmp_path / "content")
