"""Два вида ресурса и проверенные параметры восстановления."""

import shutil

import pytest

from mmorpg.infrastructure.content import ContentError, load_content
from tests.conftest import CONTENT_ROOT


def test_all_eight_classes_use_the_expected_resource(content):
    magic = {"mage", "cleric", "druid"}
    for klass in content.classes:
        assert (klass.resource.id, klass.resource.name) == (
            ("mana", "Мана") if klass.id in magic else ("stamina", "Выносливость")
        )


@pytest.mark.parametrize(
    "setting,value",
    [
        ("health_recovery_base_percent", "-1"),
        ("health_recovery_base_percent", "nan"),
        ("health_recovery_endurance_percent", "inf"),
        ("health_recovery_endurance_softener", "0"),
    ],
)
def test_invalid_recovery_settings_are_rejected(tmp_path, setting, value):
    shutil.copytree(CONTENT_ROOT, tmp_path / "content")
    path = tmp_path / "content/classes.toml"
    original = {
        "health_recovery_base_percent": "1.0",
        "health_recovery_endurance_percent": "4.0",
        "health_recovery_endurance_softener": "100.0",
    }[setting]
    path.write_text(
        path.read_text(encoding="utf-8").replace(f"{setting} = {original}", f"{setting} = {value}"),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match=setting):
        load_content(tmp_path / "content")
