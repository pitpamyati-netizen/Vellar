"""Параметры копий и реальное завершение зависшего отдельного процесса."""

import os
import subprocess
import sys

import pytest
from scripts.backup import database_dsn, pg_environment, rotate
from scripts.supervise import exclusive, stop_child


def test_database_replacement_keeps_query_and_encoded_password():
    dsn = "postgresql://user:p%40ss@127.0.0.1:55432/old?sslmode=disable"
    assert database_dsn(dsn, "new_test").endswith("/new_test?sslmode=disable")
    env = pg_environment(dsn)
    assert env["PGPASSWORD"] == "p@ss"
    assert "p%40ss" not in env["PGDATABASE"]


@pytest.mark.parametrize("keep", [0, -1])
def test_bad_keep_cannot_delete_anything(tmp_path, keep):
    current = tmp_path / "vellar-current.dump"
    current.write_bytes(b"good")
    with pytest.raises(ValueError):
        rotate(tmp_path, keep, current)
    assert current.read_bytes() == b"good"


def test_legacy_sql_without_proof_is_preserved(tmp_path):
    old = tmp_path / "vellar-old.sql"
    old.write_bytes(b"legacy")
    current = tmp_path / "vellar-new.dump"
    current.write_bytes(b"new")
    rotate(tmp_path, 1, current)
    assert old.exists() and current.exists()


def test_two_supervisors_cannot_own_one_process(tmp_path):
    path = tmp_path / "lock"
    with exclusive(path), pytest.raises(OSError), exclusive(path):
        pass


def test_hung_child_is_stopped():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stop_child(child, timeout=0.1)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_real_supervisor_restarts_hang_without_repeating_effect(tmp_path):
    helper = tmp_path / "child.py"
    marker = tmp_path / "committed"
    recovered = tmp_path / "recovered"
    beat = tmp_path / "beat"
    helper.write_text(
        "from pathlib import Path\nfrom mmorpg.health import touch\nimport time\n"
        f"beat=Path({str(beat)!r})\nmarker=Path({str(marker)!r})\n"
        f"recovered=Path({str(recovered)!r})\n"
        "touch(beat)\n"
        "if marker.exists():\n    recovered.write_text(marker.read_text())\n"
        "else:\n    marker.write_text('one effect')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    command = (
        "from scripts.supervise import supervise; from pathlib import Path; import sys; "
        f"supervise([sys.executable,{str(helper)!r}],startup=1,"
        f"stop_requested=lambda: Path({str(recovered)!r}).exists())"
    )
    env = dict(os.environ, HEARTBEAT_PATH=str(beat), HEARTBEAT_SECONDS="0.05")
    result = subprocess.run(
        [sys.executable, "-c", command], env=env, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert marker.read_text() == recovered.read_text() == "one effect"
    assert not beat.exists()
