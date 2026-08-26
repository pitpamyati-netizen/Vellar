"""Чтение ``content/changelog.toml`` - истории обновлений, которую читают игроки.

Обновление - это содержимое, а не список коммитов: каждая строка говорит, что
игрок теперь может сделать или увидеть. Живёт оно в TOML по той же причине, что и
мир: написать обновление - значит поправить файл, а не поправить код
(``Claude.md``, правило 6).

Читается по требованию, а не на старте: внутри игры никто не спрашивает, что
изменилось в версии 0.2, а играть игра обязана и вовсе без настроенного канала.
Читатель - ``scripts/broadcast.py``, в ту минуту, когда обновление объявляют.

Проверяется здесь форма файла. Слова проверяются там же, где все прочие правила
канала, - в ``presentation/telegram/broadcast.py``. И здесь же - свежесть:
``unannounced_changes`` спрашивает git, не отстал ли файл от игры, потому что сам
файл о себе такого не знает, а пост в канал уходит навсегда.
"""

from __future__ import annotations

import subprocess
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
# git отвечает мгновенно или не отвечает вовсе: выпуск не ждёт его дольше этого.
GIT_TIMEOUT = 10.0


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


# --- свежесть --------------------------------------------------------

# Дерево игры: то, изменение чего игрок способен заметить. Тесты, скрипты и
# документация сюда не входят - о них в канале не рассказывают.
GAME_TREE = ("src", "content")
# Сам список обновлений из этого дерева вычтен: строка, дописанная в него, - это
# и есть рассказ о переменах, а не ещё одна перемена, о которой надо рассказать.
_EXCLUDE_SELF = f":(exclude)content/{CHANGELOG_FILE}"
WORKING_TREE = "the working tree changes the game on top of that"


def unannounced_changes(project_root: Path) -> tuple[str, ...] | None:
    """Что изменилось в игре после того, как ``changelog.toml`` дописали в последний раз.

    Пост уходит в канал навсегда, а «самое свежее обновление» в файле свежее лишь
    настолько, насколько его дописали: 1.12 ушла в канал уже после того, как
    дерево умений переписали, и объявила игрокам ровно то правило, которое та
    правка отменила. Спросить об этом можно только git - файл о себе такого не
    знает.

    Возвращает:
        Строки для того, кто выпускает: коммиты, изменившие игру после последней
        записи, и, если она есть, метка незакоммиченной правки. Пустой кортеж -
        файл не отстал. ``None`` - спросить некого: git недоступен или это не
        рабочее дерево, и гадать здесь не о чем.
    """
    if _git(project_root, "rev-parse", "--git-dir") is None:
        return None
    if _git(project_root, "rev-parse", "--verify", "HEAD") is None:
        # Дерево без истории: позади нечему остаться.
        return ()

    last = _git(project_root, "log", "-1", "--format=%H", "--", f"content/{CHANGELOG_FILE}") or ""
    if not last.strip():
        # Файл ещё не в истории: сравнивать не с чем, и он по определению свежее её.
        return ()

    since = f"{last.strip()}..HEAD"
    lines = list(_lines(_git(project_root, "log", "--format=%h %s", since, *_paths())))
    # Файл правят прямо сейчас - значит, о переменах в дереве как раз и пишут.
    writing = _lines(_git(project_root, "status", "--porcelain", "--", f"content/{CHANGELOG_FILE}"))
    if not writing and _lines(_git(project_root, "status", "--porcelain", *_paths())):
        lines.append(WORKING_TREE)
    return tuple(lines)


def _paths() -> tuple[str, ...]:
    return ("--", *GAME_TREE, _EXCLUDE_SELF)


def _lines(output: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (output or "").splitlines() if line.strip())


def _git(project_root: Path, *args: str) -> str | None:
    """Спросить git одну вещь. ``None`` - он не ответил, и это не ошибка выпуска."""
    try:
        # Аргументы свои, оболочки нет, git ищется в PATH - как везде в проекте.
        done = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout
