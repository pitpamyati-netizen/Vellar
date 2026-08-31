"""Сводка: три направленных дела на переворот прилавка (Roadmap, ADR 0053).

«Сводка» — то, что дома рассылают по своим заставам: где прошёл зверь, куда идёт
обоз, что осело на меже (``Narrative.md``, раздел 1). Здесь она — чистая функция
от ``(город, переворот прилавка, уровень игрока)``: три дела, каждое зовёт игрока
в конкретное место этого города и платит надбавку сверх обычного боя того же
уровня. Одно выполнение за переворот; сам учёт держит кэш со сроком
(``presentation/telegram/digest_claim.py``), а не домен.

Ничего здесь не хранится и не пишет: :func:`digest` собирает дела из сида,
:func:`reward` считает надбавку, а предикаты ``closes_*`` говорят, закрыло ли
случившееся одно из дел. Цели — только имена локаций, городов и названных
подземелий из ``content/``: имена узлов зависят от поколения округи и волн, и
дело, привязанное к ним, перестало бы быть функцией от трёх аргументов.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.entities.content import City, Dungeon, GameContent, Location
from mmorpg.domain.procgen.enemies import GOLD_BASE, GOLD_PER_LEVEL
from mmorpg.domain.procgen.seeds import derive, rng, shop_seed
from mmorpg.domain.rules.progression import experience_reward

#: Во сколько раз дело со сводки платит больше обычного боя того же уровня.
#: «Награда в полтора-два обычных» (Roadmap) — берём середину.
DIGEST_BONUS = 1.75


class DeedKind(StrEnum):
    """Что за дело. Больше трёх видов у заставы нет."""

    CULL = "cull"  # разредить стаю в локации города
    HAUL = "haul"  # проводить обоз до соседнего открытого города
    DELVE = "delve"  # спуститься в названное подземелье города


@dataclass(frozen=True, slots=True)
class Deed:
    """Одно дело сводки: что сделать, где и почём."""

    kind: DeedKind
    #: Фраза игроку: что сделать и в каком месте.
    line: str
    #: Короткое имя места или города — для строки «дело закрыто (…)».
    where: str
    #: Уровень дела: по нему считается надбавка.
    level: int
    #: Локация города (``CULL``).
    slot: int = 0
    #: Куда идти (``HAUL``).
    city_id: str = ""
    #: Какое из названных подземелий (``DELVE``).
    dungeon_id: str = ""


def digest(
    content: GameContent,
    world_seed: str,
    city_id: str,
    rotation: int,
    level: int,
) -> tuple[Deed, ...]:
    """Три дела заставы на этот переворот. Детерминировано от четырёх аргументов.

    Порядок в наборе постоянный: разредить стаю, проводить обоз, спуститься под
    землю. Соседних открытых городов может ещё не быть (низкие уровни) — тогда
    вместо обоза застава просит разредить ещё одну стаю.
    """
    city = content.city(city_id)
    source = rng(derive(shop_seed(world_seed, city_id, rotation), "digest"))

    spots = _combat_locations(city, level)
    cull_here = source.choice(spots)
    deeds: list[Deed] = [_cull(cull_here, level)]

    neighbours = _haul_targets(content, city, level)
    if neighbours:
        deeds.append(_haul(source.choice(neighbours), level))
    else:
        elsewhere = [loc for loc in spots if loc.slot != cull_here.slot] or spots
        deeds.append(_cull(source.choice(elsewhere), level))

    holes = _delve_targets(city, level)
    if holes:
        deeds.append(_delve(source.choice(holes)))
    else:  # pragma: no cover - у каждого города есть обычные подземелья (ADR 0041)
        deeds.append(_cull(source.choice(spots), level))

    return tuple(deeds)


def reward(level: int) -> tuple[int, int]:
    """Надбавка за дело: золото и опыт, ``DIGEST_BONUS`` от обычного боя уровня дела."""
    lvl = max(1, level)
    gold = max(1, round((GOLD_BASE + GOLD_PER_LEVEL * lvl) * DIGEST_BONUS))
    experience = max(
        1, round(experience_reward(enemy_level=lvl, character_level=lvl) * DIGEST_BONUS)
    )
    return gold, experience


def closes_cull(deed: Deed, *, slot: int) -> bool:
    """Победа в бою в этой локации закрывает дело ``CULL`` на неё."""
    return deed.kind is DeedKind.CULL and deed.slot == slot


def closes_haul(deed: Deed, *, city_id: str) -> bool:
    """Приход в этот город по дороге закрывает дело ``HAUL`` на него."""
    return deed.kind is DeedKind.HAUL and bool(city_id) and deed.city_id == city_id


def closes_delve(deed: Deed, *, dungeon_id: str = "", roamer_cleared: bool = False) -> bool:
    """Пройденный до логова названный спуск — или любой блуждающий ход — закрывает ``DELVE``."""
    if deed.kind is not DeedKind.DELVE:
        return False
    if roamer_cleared:
        return True
    return bool(dungeon_id) and deed.dungeon_id == dungeon_id


def _band_gap(location: Location, level: int) -> int:
    if location.covers(level):
        return 0
    return min(abs(level - location.level_min), abs(level - location.level_max))


def _combat_locations(city: City, level: int) -> list[Location]:
    """Мирные локации города по уровню игрока — всегда хотя бы две, если они есть.

    Одна локация в наборе заморозила бы дело на все перевороты: сид меняется, а
    выбор из одного — нет.
    """
    pool = [loc for loc in city.locations if not loc.pvp] or list(city.locations)
    covering = [loc for loc in pool if loc.covers(level)]
    if len(covering) >= 2:
        return covering
    by_gap = sorted(pool, key=lambda loc: (_band_gap(loc, level), loc.slot))
    return by_gap[: max(2, len(covering))] if len(by_gap) >= 2 else by_gap


def _haul_targets(content: GameContent, city: City, level: int) -> list[City]:
    """Открытые города по соседству — до трёх ближайших по дороге."""
    others = sorted(
        (other for other in content.cities if other.id != city.id and other.unlock_level <= level),
        key=lambda other: (abs(other.order - city.order), other.order),
    )
    return others[:3]


def _delve_targets(city: City, level: int) -> list[Dungeon]:
    """Обычные подземелья города, открытые игроку по уровню."""
    open_now = [one for one in city.dungeons if not one.deep and one.unlock_level <= level]
    return open_now or [one for one in city.dungeons if not one.deep]


def _deed_level(location: Location, level: int) -> int:
    return min(location.level_max, max(location.level_min, level))


def _cull(location: Location, level: int) -> Deed:
    return Deed(
        kind=DeedKind.CULL,
        line=f"Разредить стаю у места «{location.name}».",
        where=location.name,
        level=_deed_level(location, level),
        slot=location.slot,
    )


def _haul(city: City, level: int) -> Deed:
    return Deed(
        kind=DeedKind.HAUL,
        line=f"Проводить обоз до города {city.name}.",
        where=city.name,
        level=max(1, level),
        city_id=city.id,
    )


def _delve(dungeon: Dungeon) -> Deed:
    return Deed(
        kind=DeedKind.DELVE,
        line=f"Спуститься в подземелье «{dungeon.name}».",
        where=dungeon.name,
        level=max(1, dungeon.level),
        dungeon_id=dungeon.id,
    )
