"""The update history players read.

Two things are guarded here: that ``content/changelog.toml`` is a file the game
can actually post, and that a malformed one is refused with every problem listed
at once, the way the rest of the content directory is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mmorpg.infrastructure.content import ContentError, load_changelog, select_release
from mmorpg.infrastructure.content.changelog import ENTRY_LIMIT, Release, version_key
from mmorpg.presentation.telegram import broadcast as bc
from tests.conftest import CONTENT_ROOT


def write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "changelog.toml").write_text(body, encoding="utf-8")
    return tmp_path


# --- the real file ---------------------------------------------------


def test_the_shipped_changelog_loads() -> None:
    releases = load_changelog(CONTENT_ROOT)

    assert releases
    assert all(release.entries for release in releases)


def test_every_shipped_release_is_a_postable_update() -> None:
    """A version nobody can post is a version players never hear about."""
    for release in load_changelog(CONTENT_ROOT):
        event = bc.changelog(
            release.version,
            added=release.added,
            changed=release.changed,
            fixed=release.fixed,
        )
        text = bc.render_broadcast(event)

        assert text.splitlines()[0].endswith(f"Обновление {release.version}.")
        assert len(text) <= bc.limit_for(bc.BroadcastKind.CHANGELOG)


# --- ordering --------------------------------------------------------


def test_ten_comes_after_nine() -> None:
    assert version_key("0.10") > version_key("0.9")


def test_releases_are_ordered_and_latest_is_the_newest(tmp_path: Path) -> None:
    content_dir = write(
        tmp_path,
        """
        [[release]]
        version = "0.10"
        fixed = ["Лавка снова помнит выкупленный товар."]

        [[release]]
        version = "0.9"
        added = ["Арена: бой на три круга."]
        """,
    )

    releases = load_changelog(content_dir)

    assert [release.version for release in releases] == ["0.9", "0.10"]
    assert select_release(releases).version == "0.10"
    assert select_release(releases, "0.9").version == "0.9"


def test_an_unknown_version_names_the_ones_that_exist() -> None:
    releases = (Release(version="0.1", added=("Дорога.",)),)

    with pytest.raises(ContentError, match=r"it lists 0\.1"):
        select_release(releases, "3.0")


def test_selecting_from_nothing_is_refused() -> None:
    with pytest.raises(ContentError, match="no releases"):
        select_release(())


# --- refusals --------------------------------------------------------


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match=r"missing content file: changelog\.toml"):
        load_changelog(tmp_path)


def test_an_empty_changelog_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="no releases"):
        load_changelog(write(tmp_path, "\n"))


def test_a_release_without_changes_is_not_a_release(tmp_path: Path) -> None:
    content_dir = write(tmp_path, '[[release]]\nversion = "0.1"\n')

    with pytest.raises(ContentError, match="lists no changes"):
        load_changelog(content_dir)


def test_every_problem_is_reported_together(tmp_path: Path) -> None:
    content_dir = write(
        tmp_path,
        f"""
        [[release]]
        version = "весна"
        added = ["Дорога."]

        [[release]]
        version = "0.1"
        added = ["Дорога."]

        [[release]]
        version = "0.1"
        added = ["Дорога."]

        [[release]]
        version = "0.2"
        added = ["{"а" * (ENTRY_LIMIT + 1)}", "  "]
        """,
    )

    with pytest.raises(ContentError) as error:
        load_changelog(content_dir)

    problems = " ".join(error.value.problems)
    assert "is not a version" in problems
    assert "duplicate release 0.1" in problems
    assert f"the limit is {ENTRY_LIMIT}" in problems
    assert "empty line" in problems
