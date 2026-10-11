"""Каталог отвергает неисполнимые условия и враждебную адресную помощь."""

import shutil
from pathlib import Path

import pytest

from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT


@pytest.mark.parametrize(
    "original,replacement",
    [
        ("bonus = 20", "bonus = 999"),
        ('condition = "actor_status"', 'condition = "imaginary"'),
        ('statuses = ["guard"]', "statuses = []"),
        ("threshold = 50", "threshold = 100"),
        ('category = "damage"', 'category = "carry"'),
        ('statuses = ["guard"]', 'statuses = ["guard", "guard"]'),
    ],
)
def test_invalid_tactic_is_rejected(tmp_path: Path, original, replacement):
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/classes.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(original, replacement, 1), encoding="utf-8"
    )
    with pytest.raises(ContentError, match=r"classes\.toml"):
        load_content(tmp_path / "content")


def test_damage_cannot_be_labelled_as_ally_support(tmp_path: Path):
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/skills.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('target = "enemy"', 'target = "ally"', 1),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="hostile effect"):
        load_content(tmp_path / "content")
