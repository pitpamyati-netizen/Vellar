"""Ошибки каталога не доходят до запущенной игры."""

import shutil

import pytest

from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('id = "crew"', 'id = "gate"', "distinct meetings"),
        ('rank = "boss"', 'rank = "normal"', "final boss"),
        ('rank = "normal"', 'rank = "boss"', "final boss"),
        ('enemies = ["pit_sentinel"]', 'enemies = ["missing"]', "invalid enemies"),
        ("heal_percent = 20", "heal_percent = 200", "invalid encounter rules"),
        ("health_per_extra_member = 25", "health_per_extra_member = -1", "invalid encounter rules"),
        ('briefing = "Страж', 'briefing = "', "invalid text"),
    ],
)
def test_bad_meeting_is_rejected(tmp_path, old, new, message):
    root = tmp_path / "content"
    shutil.copytree(CONTENT_ROOT, root)
    path = root / "expeditions.toml"
    source = path.read_text(encoding="utf-8")
    if message == "invalid text":
        lines = source.splitlines()
        lines = [
            'briefing = ""' if line.startswith('briefing = "Страж') else line for line in lines
        ]
        source = "\n".join(lines)
    else:
        source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ContentError, match=message):
        load_content(root)


def test_unknown_route_is_rejected(tmp_path):
    root = tmp_path / "content"
    shutil.copytree(CONTENT_ROOT, root)
    path = root / "world.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('route = "pump_house"', 'route = "missing"'),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="unknown route"):
        load_content(root)
