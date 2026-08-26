"""Чтение ``content/changelog.toml`` - истории обновлений, которую читают игроки.

Обновление - это содержимое, а не список коммитов: каждая строка говорит, что
игрок теперь может сделать или увидеть. Живёт оно в TOML по той же причине, что и
мир: написать обновление - значит поправить файл, а не поправить код
(``Claude.md``, правило 6).

Читается по требованию, а не на старте: внутри игры никто не спрашивает, что
изменилось в версии 0.2, а играть игра обязана и вовсе без настроенного канала.
Читатель - ``scripts/broadcast.py``, в ту минуту, когда обновление объявляют.

Проверяется здесь форма файла. Слова проверяются там же, где все прочие правила
канала, - в ``presentation/telegram/broadcast.py``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mmorpg.infrastructure.content.loader import ContentError

CHANGELOG_FILE = "changelog.toml"
# Одна запись - одна строка поста, читаемого вслух: длиннее, и она перестаёт быть
# строкой и становится абзацем, который никто не удержит в голове.
ENTRY_LIMIT = 200
LATEST = "latest"
# Заголовок стоит отдельно, поэтому обязан назвать, о каком обновлении речь: игрок,
# остановившийся после первой строки, всё равно должен знать, о чём он только что
# услышал.
HEADLINE_PREFIX = "Обновление"


@dataclass(frozen=True, slots=True)
class Release:
    """Одна вышедшая версия, в трёх разделах, понятных игроку."""

    version: str
    # Первая строка поста. Пусто — значит, файл её не написал, и пост откатывается к
    # голому «Обновление 0.2».
    headline: str = ""
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()

    @property
    def entries(self) -> tuple[str, ...]:
        return self.added + self.changed + self.fixed


def version_key(version: str) -> tuple[int, ...]:
    """``"0.10"`` идёт после ``"0.9"``, и поэтому версии обязаны быть числами."""
    return tuple(int(part) for part in version.split("."))


def load_changelog(content_dir: Path) -> tuple[Release, ...]:
    """Разобрать список обновлений, старые сверху.

    Бросает:
        ContentError: если файла нет, он испорчен или противоречив. Все беды
            собираются вместе, чтобы автор увидел весь список разом.
    """
    path = content_dir / CHANGELOG_FILE
    if not path.is_file():
        raise ContentError([f"missing content file: {CHANGELOG_FILE}"])
    try:
        with path.open("rb") as handle:
            raw: dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:  # pragma: no cover - зависит от испорченного ввода
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
    """Взять один выпуск по версии или самый свежий по слову ``"latest"``."""
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
    """Строка, которую игрок слышит первой, если файл её пишет."""
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
