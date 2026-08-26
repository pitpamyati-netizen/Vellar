"""Цепочка сидов.

Всё собираемое в игре происходит от одного мирового сида через blake2b:

    location_seed  = blake2b(world_seed, city_id, slot)
    node_seed(i)   = blake2b(location_seed, i)
    wave_seed(i,w) = blake2b(location_seed, "wave", i, w)
    enemy_seed     = blake2b(node_seed, attempt)
    shop_seed      = blake2b(world_seed, "shop", city_id, rotation)

Локация - это место, а не бросок: у её карты нет счётчика поколений вовсе,
поэтому Луга завтра те же самые Луга, а тропы, выученные игроком на слух,
остаются где были. Меняется то, что стоит *в* узлах: каждый узел считает свои
волны, и отработанную волну через несколько минут сменяет следующая
(``domain/rules/nodes.py``). Второе, что считается по живым часам, - лавка: она
переворачивается каждые полчаса (``rotation``), потому что прилавок, который не
меняется, лишает смысла возвращение.

Никакого глобального ``random`` нигде: вызывающему выдаётся явный образец
``random.Random``, собранный из сида. О часах модуль не знает ничего - номер
переворота всегда приходит аргументом, и это то, что держит сборку чистой и
проверяемой.
"""

from __future__ import annotations

import random
from hashlib import blake2b

DIGEST_SIZE = 16
# Полчаса. Достаточно мало, чтобы игрок, пришедший за оружием, дождался следующего
# прилавка, и достаточно много, чтобы лавка не была игровым автоматом.
DEFAULT_SHOP_ROTATION_SECONDS = 1_800


def rotation_index(unix_time: int, rotation_seconds: int = DEFAULT_SHOP_ROTATION_SECONDS) -> int:
    """К какому перевороту лавки относится этот момент времени."""
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
    """Сколько ещё стоит нынешний прилавок. Идёт в сроки жизни кэша."""
    return (
        rotation_ends_at(rotation_index(unix_time, rotation_seconds), rotation_seconds) - unix_time
    )


def derive(*parts: str | int | bytes) -> bytes:
    """blake2b по частям, сшитым разделителем, которого не бывает в идентификаторах."""
    digest = blake2b(digest_size=DIGEST_SIZE)
    for part in parts:
        digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.digest()


def location_seed(world_seed: str, city_id: str, slot: int) -> bytes:
    """Карта одной локации. Поколения у неё нет: место постоянно."""
    return derive(world_seed, city_id, slot)


def node_seed(parent: bytes, index: int) -> bytes:
    return derive(parent, index)


def wave_seed(parent: bytes, index: int, wave: int) -> bytes:
    """Что стоит в одном узле в одну из его волн.

    Карта не двигается, поэтому это единственный сид, который меняется со временем:
    новая волна - это новая стая противников и новая горсть находок на том же месте.
    """
    return derive(parent, "wave", index, wave)


def enemy_seed(parent: bytes, attempt: int) -> bytes:
    return derive(parent, "enemy", attempt)


def shop_seed(world_seed: str, city_id: str, rotation: int) -> bytes:
    return derive(world_seed, "shop", city_id, rotation)


def to_int(seed: bytes) -> int:
    return int.from_bytes(seed, "big")


def rng(seed: bytes) -> random.Random:
    """Личный источник случайности. Функциями модуля ``random`` не пользоваться никогда."""
    return random.Random(to_int(seed))
