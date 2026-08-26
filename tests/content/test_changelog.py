"""История обновлений, которую читают игроки.

Здесь стерегут два дела: что ``content/changelog.toml`` - это файл, который игра
и правда может опубликовать, и что испорченный отвергается со всеми бедами,
названными разом, - ровно так же, как весь остальной каталог содержимого.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mmorpg.infrastructure.content import (
    ContentError,
    load_changelog,
    select_release,
    unannounced_changes,
)
from mmorpg.infrastructure.content.changelog import (
    ENTRY_LIMIT,
    HEADLINE_PREFIX,
    WORKING_TREE,
    Release,
    version_key,
)
from mmorpg.presentation.telegram import broadcast as bc
from tests.conftest import CONTENT_ROOT


def write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "changelog.toml").write_text(body, encoding="utf-8")
    return tmp_path


# --- настоящий файл --------------------------------------------------


def test_the_shipped_changelog_loads() -> None:
    releases = load_changelog(CONTENT_ROOT)

    assert releases
    assert all(release.entries for release in releases)


def test_every_shipped_release_is_a_postable_update() -> None:
    """Версия, которую никто не может опубликовать, - это версия, о которой игроки не узнают."""
    for release in load_changelog(CONTENT_ROOT):
        event = bc.changelog(
            release.version,
            headline=release.headline,
            added=release.added,
            changed=release.changed,
            fixed=release.fixed,
        )
        text = bc.render_broadcast(event)

        assert f"{HEADLINE_PREFIX} {release.version}" in text.splitlines()[0]
        assert len(text) <= bc.limit_for(bc.BroadcastKind.CHANGELOG)


# --- заголовок -------------------------------------------------------


def test_the_headline_written_in_the_file_is_the_first_line(tmp_path: Path) -> None:
    """Строку, которую игрок слышит первой, пишет автор, а не сборка."""
    content_dir = write(
        tmp_path,
        """
        [[release]]
        version = "0.2"
        headline = "Обновление 0.2: на арене снова дерутся."
        added = ["Арена: бой на три круга."]
        """,
    )

    release = select_release(load_changelog(content_dir))
    text = bc.render_broadcast(
        bc.changelog(release.version, headline=release.headline, added=release.added), emoji=False
    )

    assert text.splitlines()[0] == "Обновление 0.2: на арене снова дерутся."


def test_a_release_without_a_headline_still_posts(tmp_path: Path) -> None:
    content_dir = write(
        tmp_path,
        """
        [[release]]
        version = "0.2"
        added = ["Арена: бой на три круга."]
        """,
    )

    release = select_release(load_changelog(content_dir))

    assert release.headline == ""
    text = bc.render_broadcast(
        bc.changelog(release.version, headline=release.headline, added=release.added), emoji=False
    )
    assert text.splitlines()[0] == "Обновление 0.2."


def test_a_headline_that_hides_the_version_is_refused(tmp_path: Path) -> None:
    """Первая строка, прочитанная отдельно, обязана назвать, о каком обновлении речь."""
    content_dir = write(
        tmp_path,
        """
        [[release]]
        version = "0.2"
        headline = "На арене снова дерутся."
        added = ["Арена: бой на три круга."]
        """,
    )

    with pytest.raises(ContentError, match="must start with"):
        load_changelog(content_dir)


def test_an_empty_or_overlong_headline_is_refused(tmp_path: Path) -> None:
    content_dir = write(
        tmp_path,
        f"""
        [[release]]
        version = "0.2"
        headline = "  "
        added = ["Арена: бой на три круга."]

        [[release]]
        version = "0.3"
        headline = "Обновление 0.3: {"а" * ENTRY_LIMIT}"
        added = ["Лавка снова помнит выкупленный товар."]
        """,
    )

    with pytest.raises(ContentError) as error:
        load_changelog(content_dir)

    problems = " ".join(error.value.problems)
    assert "empty headline" in problems
    assert f"headline, the limit is {ENTRY_LIMIT}" in problems


# --- порядок ---------------------------------------------------------


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


# --- отказы ----------------------------------------------------------


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


# --- свежесть --------------------------------------------------------


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def game_tree(root: Path, *, code: str = "one", release: str = "0.1") -> None:
    """Крошечное подобие дерева игры: то, что видит игрок, и рассказ об этом."""
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "rules.py").write_text(code, encoding="utf-8")
    (root / "content").mkdir(exist_ok=True)
    write(root / "content", f'[[release]]\nversion = "{release}"\nadded = ["Дорога."]\n')


def repo_with_a_release(root: Path) -> Path:
    git(root, "init", "-q")
    game_tree(root)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "feat: the road")
    return root


def test_git_that_cannot_answer_is_not_a_verdict(tmp_path: Path) -> None:
    """Нет дерева - нет и ответа: выдумывать за git отказ выпуска нечего."""
    assert unannounced_changes(tmp_path / "nowhere at all") is None


def test_a_release_written_with_the_change_is_fresh(tmp_path: Path) -> None:
    assert unannounced_changes(repo_with_a_release(tmp_path)) == ()


def test_a_change_committed_after_the_release_is_unannounced(tmp_path: Path) -> None:
    """Ровно то, что случилось с 1.12: игру переписали, а файл остался прежним."""
    root = repo_with_a_release(tmp_path)
    (root / "src" / "rules.py").write_text("two", encoding="utf-8")
    git(root, "commit", "-qam", "feat: a tree costs more than it pays")

    behind = unannounced_changes(root)

    assert behind is not None
    assert any("a tree costs more" in line for line in behind)


def test_a_change_still_in_the_working_tree_counts_too(tmp_path: Path) -> None:
    """Пост уходит из того дерева, что лежит на диске, а не из того, что закоммичено."""
    root = repo_with_a_release(tmp_path)
    (root / "src" / "rules.py").write_text("two", encoding="utf-8")

    assert unannounced_changes(root) == (WORKING_TREE,)


def test_the_release_being_written_right_now_is_not_a_complaint(tmp_path: Path) -> None:
    """Дописывают файл как раз тогда, когда дерево изменено: это и есть рассказ о переменах."""
    root = repo_with_a_release(tmp_path)
    game_tree(root, code="two", release="0.2")

    assert unannounced_changes(root) == ()


def test_a_release_never_committed_has_nothing_to_lag_behind(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    game_tree(tmp_path)

    assert unannounced_changes(tmp_path) == ()
