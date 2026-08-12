"""The seed chain.

Every generated thing in the game descends from one world seed through blake2b:

    cycle_index    = unix_time // CYCLE_SECONDS
    location_seed  = blake2b(world_seed, city_id, slot, cycle_index)
    node_seed(i)   = blake2b(location_seed, i)
    enemy_seed     = blake2b(node_seed, attempt)

No global ``random`` anywhere: callers get an explicit ``random.Random`` instance
built from a seed. This module knows nothing about the clock - ``cycle_index`` is
always passed in, which is what keeps generation testable and pure.
"""

from __future__ import annotations

import random
from hashlib import blake2b

DIGEST_SIZE = 16
DEFAULT_CYCLE_SECONDS = 21_600  # six hours, see docs/adr/0003-six-hour-world-cycle.md


def cycle_index(unix_time: int, cycle_seconds: int = DEFAULT_CYCLE_SECONDS) -> int:
    """Which world cycle a moment in time belongs to."""
    if cycle_seconds <= 0:
        msg = "cycle_seconds must be positive"
        raise ValueError(msg)
    return unix_time // cycle_seconds


def cycle_started_at(index: int, cycle_seconds: int = DEFAULT_CYCLE_SECONDS) -> int:
    return index * cycle_seconds


def cycle_ends_at(index: int, cycle_seconds: int = DEFAULT_CYCLE_SECONDS) -> int:
    return (index + 1) * cycle_seconds


def seconds_left_in_cycle(unix_time: int, cycle_seconds: int = DEFAULT_CYCLE_SECONDS) -> int:
    """Used as the TTL for the location delta log in Redis."""
    return cycle_ends_at(cycle_index(unix_time, cycle_seconds), cycle_seconds) - unix_time


def derive(*parts: str | int | bytes) -> bytes:
    """blake2b over the parts, joined with a separator that cannot appear in ids."""
    digest = blake2b(digest_size=DIGEST_SIZE)
    for part in parts:
        digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.digest()


def location_seed(world_seed: str, city_id: str, slot: int, cycle: int) -> bytes:
    return derive(world_seed, city_id, slot, cycle)


def node_seed(parent: bytes, index: int) -> bytes:
    return derive(parent, index)


def enemy_seed(parent: bytes, attempt: int) -> bytes:
    return derive(parent, "enemy", attempt)


def shop_seed(world_seed: str, city_id: str, cycle: int) -> bytes:
    return derive(world_seed, "shop", city_id, cycle)


def to_int(seed: bytes) -> int:
    return int.from_bytes(seed, "big")


def rng(seed: bytes) -> random.Random:
    """A private random source. Never use the module-level ``random`` functions."""
    return random.Random(to_int(seed))
