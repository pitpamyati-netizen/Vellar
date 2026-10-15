"""Пороги рангов задаются данными и проверяются до запуска игры."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mmorpg.application.battle_snapshots import decode, encode
from mmorpg.domain.entities import GameContent
from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "[1, 10, 30, 60]",
        "[2, 10, 30, 60, 100]",
        "[1, 10, 10, 60, 100]",
        "[1, 30, 10, 60, 100]",
        "[1, 10, 30, 60, 151]",
        "[1, 10, 30.0, 60, 100]",
        "[1, 10, true, 60, 100]",
        '"1,10,30,60,100"',
    ],
)
def test_bad_rank_level_settings_are_rejected(tmp_path: Path, value: str) -> None:
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "rank_unlock_levels = [1, 10, 30, 60, 100]", f"rank_unlock_levels = {value}"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="rank_unlock_levels"):
        load_content(tmp_path / "content")


def test_old_content_without_rank_levels_gets_the_full_default(tmp_path: Path) -> None:
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("rank_unlock_levels = [1, 10, 30, 60, 100]\n", ""),
        encoding="utf-8",
    )
    assert load_content(tmp_path / "content").rules.rank_unlock_levels == (1, 10, 30, 60, 100)


def test_old_battle_rules_without_the_field_are_read_without_a_data_migration(
    content: GameContent,
) -> None:
    saved = encode(content.rules)
    del saved["fields"]["rank_unlock_levels"]
    assert decode(saved).rank_unlock_levels == content.rules.rank_unlock_levels
