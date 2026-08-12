"""The liveness heartbeat: what the container healthcheck reads."""

from __future__ import annotations

import asyncio

from mmorpg.config import Settings
from mmorpg.health import age_seconds, heartbeat, is_alive, touch


def settings_for(path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        heartbeat_path=path,
        **overrides,  # type: ignore[arg-type]
    )


def test_a_missing_file_is_not_a_heartbeat(tmp_path) -> None:
    """Before the first beat there is nothing to read, and that is not alive."""
    settings = settings_for(tmp_path / "never-written")
    assert age_seconds(settings.heartbeat_path) is None
    assert is_alive(settings) is False


def test_touch_creates_the_file_and_any_missing_parent(tmp_path) -> None:
    path = tmp_path / "run" / "beat"
    touch(path)
    assert path.exists()
    assert age_seconds(path) is not None


def test_a_fresh_beat_is_alive_and_an_old_one_is_not(tmp_path) -> None:
    path = tmp_path / "beat"
    touch(path)
    settings = settings_for(path, heartbeat_seconds=10.0)

    # Three beats is the limit, so 25 seconds still counts and 31 does not.
    assert settings.heartbeat_stale_after == 30.0
    modified = path.stat().st_mtime
    assert is_alive(settings, now=modified + 25) is True
    assert is_alive(settings, now=modified + 31) is False


async def test_the_beat_continues_while_the_block_runs(tmp_path) -> None:
    path = tmp_path / "beat"
    settings = settings_for(path, heartbeat_seconds=0.01)

    async with heartbeat(settings):
        # The first beat lands before the block, so the probe never reads an
        # empty directory while the bot is already serving.
        assert path.exists()
        first = path.stat().st_mtime_ns
        await asyncio.sleep(0.05)
        assert path.stat().st_mtime_ns > first

    # A file left behind by a clean stop would make the next start look wedged.
    assert not path.exists()


async def test_the_beat_stops_even_when_the_block_raises(tmp_path) -> None:
    path = tmp_path / "beat"
    settings = settings_for(path, heartbeat_seconds=0.01)

    class BlockError(Exception):
        pass

    try:
        async with heartbeat(settings):
            raise BlockError
    except BlockError:
        pass

    assert not path.exists()
    # No task survives to keep touching the file after the crash.
    assert all(task.get_name() != "heartbeat" for task in asyncio.all_tasks())
