"""Load ``content/changelog.toml`` - the update history players read.

An update is content, not a commit log: every line says what a player can now do
or will now see. It lives in TOML for the same reason the world does - writing an
update means editing a file, not editing code (``Claude.md``, rule 6).

Read on demand rather than at startup: nobody inside the game asks what changed
in version 0.2, and the game must play with no channel configured at all. The
reader is ``scripts/broadcast.py``, at the moment an update is announced.

Validated here: the shape of the file. The wording is validated where every other
channel rule is - ``presentation/telegram/broadcast.py``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mmorpg.infrastructure.content.loader import ContentError

CHANGELOG_FILE = "changelog.toml"
# One entry is one line of a post read aloud: longer than this and it stops being
# a line and becomes a paragraph nobody can hold in their head.
ENTRY_LIMIT = 200
LATEST = "latest"
# A headline stands alone, so it has to say which update this is: a player who
# stops after the first line must still know what they just heard about.
HEADLINE_PREFIX = "Обновление"


@dataclass(frozen=True, slots=True)
class Release:
    """One published version, in the three sections a player understands."""

    version: str
    # The first line of the post. Empty means the file did not write one, and the
    # post falls back to the bare "Обновление 0.2.".
    headline: str = ""
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()

    @property
    def entries(self) -> tuple[str, ...]:
        return self.added + self.changed + self.fixed


def version_key(version: str) -> tuple[int, ...]:
    """``"0.10"`` sorts after ``"0.9"``, which is why versions must be numeric."""
    return tuple(int(part) for part in version.split("."))


def load_changelog(content_dir: Path) -> tuple[Release, ...]:
    """Parse the changelog, oldest release first.

    Raises:
        ContentError: if the file is missing, malformed or inconsistent. Every
            problem is collected, so an author sees the whole list at once.
    """
    path = content_dir / CHANGELOG_FILE
    if not path.is_file():
        raise ContentError([f"missing content file: {CHANGELOG_FILE}"])
    try:
        with path.open("rb") as handle:
            raw: dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:  # pragma: no cover - depends on broken input
        raise ContentError([f"{CHANGELOG_FILE}: cannot parse TOML: {error}"]) from error

    problems: list[str] = []
    releases: list[Release] = []
    seen: set[str] = set()

    for entry in raw.get("release", ()):
        version = str(entry.get("version", "")).strip()
        if not version:
            problems.append(f"{CHANGELOG_FILE}: a release has no version")
            continue
        if not _is_numeric(version):
            problems.append(f"{CHANGELOG_FILE}: {version!r} is not a version like 0.2")
            continue
        if version in seen:
            problems.append(f"{CHANGELOG_FILE}: duplicate release {version}")
            continue
        seen.add(version)

        release = Release(
            version=version,
            headline=_headline(entry, version, problems),
            added=_section(entry, "added", version, problems),
            changed=_section(entry, "changed", version, problems),
            fixed=_section(entry, "fixed", version, problems),
        )
        if not release.entries:
            problems.append(f"{CHANGELOG_FILE}: release {version} lists no changes")
            continue
        releases.append(release)

    if not releases and not problems:
        problems.append(f"{CHANGELOG_FILE}: no releases")
    if problems:
        raise ContentError(problems)
    return tuple(sorted(releases, key=lambda release: version_key(release.version)))


def select_release(releases: Sequence[Release], version: str = LATEST) -> Release:
    """Pick one release by version, or the newest one for ``"latest"``."""
    if not releases:
        raise ContentError([f"{CHANGELOG_FILE}: no releases"])
    if version == LATEST:
        return releases[-1]
    for release in releases:
        if release.version == version:
            return release
    known = ", ".join(release.version for release in releases)
    raise ContentError([f"{CHANGELOG_FILE}: no release {version}; it lists {known}"])


def _is_numeric(version: str) -> bool:
    parts = version.split(".")
    return bool(parts) and all(part.isdigit() for part in parts)


def _headline(entry: Any, version: str, problems: list[str]) -> str:
    """The line a player hears first, if the file writes one."""
    raw = entry.get("headline")
    if raw is None:
        return ""
    line = str(raw).strip()
    if not line:
        problems.append(f"{CHANGELOG_FILE}: {version} has an empty headline")
        return ""
    if len(line) > ENTRY_LIMIT:
        problems.append(
            f"{CHANGELOG_FILE}: {version} has a {len(line)}-character headline, "
            f"the limit is {ENTRY_LIMIT}"
        )
        return ""
    if not line.startswith(f"{HEADLINE_PREFIX} {version}"):
        problems.append(
            f"{CHANGELOG_FILE}: the headline of {version} must start with "
            f"{HEADLINE_PREFIX!r} and the version - it is read on its own"
        )
        return ""
    return line


def _section(entry: Any, key: str, version: str, problems: list[str]) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in entry.get(key, ()):
        line = str(raw_line).strip()
        if not line:
            problems.append(f"{CHANGELOG_FILE}: {version} has an empty line under {key}")
            continue
        if len(line) > ENTRY_LIMIT:
            problems.append(
                f"{CHANGELOG_FILE}: {version} has a {len(line)}-character line under {key}, "
                f"the limit is {ENTRY_LIMIT}"
            )
            continue
        lines.append(line)
    return tuple(lines)
