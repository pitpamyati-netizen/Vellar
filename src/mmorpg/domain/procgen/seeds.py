"""The seed chain.

Every generated thing in the game descends from one world seed through blake2b:

    location_seed  = blake2b(world_seed, city_id, slot)
    node_seed(i)   = blake2b(location_seed, i)
    wave_seed(i,w) = blake2b(location_seed, "wave", i, w)
    enemy_seed     = blake2b(node_seed, attempt)
    shop_seed      = blake2b(world_seed, "shop", city_id, rotation)

A location is a place, not a roll: its map has no generation counter at all, so
the Meadows are the same Meadows tomorrow and the paths a player learned by ear
stay where they were. What does change is what stands *in* the nodes - each node
counts its own waves, and a wave that was worked through is replaced by the next
one a few minutes later (``domain/rules/nodes.py``). The other thing counted in
wall time is the shop, which turns over every half hour (``rotation``) - a shelf
that never changed would make coming back pointless.

No global ``random`` anywhere: callers get an explicit ``random.Random`` instance
built from a seed. This module knows nothing about the clock - the rotation index
is always passed in, which is what keeps generation testable and pure.
"""

from __future__ import annotations

import random
from hashlib import blake2b

DIGEST_SIZE = 16
# Half an hour. Short enough that a player who came for a weapon can wait for the
# next shelf, long enough that the shop is not a slot machine.
DEFAULT_SHOP_ROTATION_SECONDS = 1_800


def rotation_index(unix_time: int, rotation_seconds: int = DEFAULT_SHOP_ROTATION_SECONDS) -> int:
    """Which shop rotation a moment in time belongs to."""
    if rotation_seconds <= 0:
        msg = "rotation_seconds must be positive"
        raise ValueError(msg)
    return unix_time // rotation_seconds


def rotation_started_at(index: int, rotation_seconds: int = DEFAULT_SHOP_ROTATION_SECONDS) -> int:
    return index * rotation_seconds


def rotation_ends_at(index: int, rotation_seconds: int = DEFAULT_SHOP_ROTATION_SECONDS) -> int:
    return (index + 1) * rotation_seconds


def seconds_left_in_rotation(
    unix_time: int, rotation_seconds: int = DEFAULT_SHOP_ROTATION_SECONDS
) -> int:
    """How long the current shelf still stands. Used for cache lifetimes."""
    return (
        rotation_ends_at(rotation_index(unix_time, rotation_seconds), rotation_seconds) - unix_time
    )


def derive(*parts: str | int | bytes) -> bytes:
    """blake2b over the parts, joined with a separator that cannot appear in ids."""
    digest = blake2b(digest_size=DIGEST_SIZE)
    for part in parts:
        digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.digest()


def location_seed(world_seed: str, city_id: str, slot: int) -> bytes:
    """The map of one location. It has no generation: the place is permanent."""
    return derive(world_seed, city_id, slot)


def node_seed(parent: bytes, index: int) -> bytes:
    return derive(parent, index)


def wave_seed(parent: bytes, index: int, wave: int) -> bytes:
    """What stands in one node during one of its waves.

    The map does not move, so this is the only seed that changes with time: a new
    wave is a new pack of opponents and a new handful of finds in the same place.
    """
    return derive(parent, "wave", index, wave)


def enemy_seed(parent: bytes, attempt: int) -> bytes:
    return derive(parent, "enemy", attempt)


def shop_seed(world_seed: str, city_id: str, rotation: int) -> bytes:
    return derive(world_seed, "shop", city_id, rotation)


def to_int(seed: bytes) -> int:
    return int.from_bytes(seed, "big")


def rng(seed: bytes) -> random.Random:
    """A private random source. Never use the module-level ``random`` functions."""
    return random.Random(to_int(seed))
