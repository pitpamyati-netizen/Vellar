"""Сводка: направленные дела заставы на переворот прилавка (ADR 0053, 0054).

«Сводка» — то, что дома рассылают по своим заставам: где прошёл зверь, куда идёт
обоз, что осело на меже (``Narrative.md``, раздел 1). Здесь она — чистая функция
от ``(город, переворот прилавка, уровень игрока)``: несколько направленных дел,
каждое зовёт игрока в конкретное место этого города и платит надбавку сверх
обычного боя того же уровня. Одно выполнение за переворот; сам учёт держит кэш со
сроком (``presentation/telegram/digest_claim.py``), а не домен.

Ничего здесь не хранится и не пишет: :func:`digest` собирает дела из сида,
:func:`reward` считает надбавку, а предикаты ``closes_*`` говорят, закрыло ли
случившееся одно из дел. Цели — только имена локаций, городов, названных
подземелий и пород из ``content/``: имена узлов зависят от поколения округи и
волн, и дело, привязанное к ним, перестало бы быть функцией от трёх аргументов.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.entities.content import City, Dungeon, GameContent, Location
from mmorpg.domain.entities.location import EnemyArchetype, NodeKind
from mmorpg.domain.procgen.enemies import candidates, gold_at
from mmorpg.domain.procgen.location import guaranteed_find_kinds
from mmorpg.domain.procgen.seeds import derive, rng, shop_seed
from mmorpg.domain.rules.mood import LocationMood
from mmorpg.domain.rules.progression import experience_reward

#: Насколько охотнее застава шлёт дело в локацию по её состоянию (ADR 0055).
#: Веса пологие нарочно: состояние *подталкивает* выбор, а не диктует его, —
#: иначе дело на экране и дело в зачёте расходились бы каждый раз, как в округе
#: осел или ушёл блуждающий ход.
_MOOD_WEIGHT: dict[LocationMood, int] = {
    LocationMood.UNTOUCHED: 2,
    LocationMood.WORKED: 3,
    LocationMood.DEPLETED: 4,
    LocationMood.RESTLESS: 5,
}

#: Во сколько раз дело со сводки платит больше обычного боя того же уровня.
#: «Награда в полтора-два обычных» (Roadmap) — берём середину.
DIGEST_BONUS = 1.75


class DeedKind(StrEnum):
    """Что за дело заставы. Порядок в наборе постоянный: сперва охота, потом
    разредить стаю, потом обоз, потом обыскать место, потом спуск. ``SEARCH``
    бывает не в каждом городе — не у всякого места есть гарантированный узел
    находок (ADR 0054)."""

    HUNT = "hunt"  # выбить названную породу в локации города
    CULL = "cull"  # разредить любую стаю в локации города
    HAUL = "haul"  # проводить обоз до соседнего открытого города
    SEARCH = "search"  # обыскать узел названного вида в локации города
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
    #: Локация города (``HUNT``, ``CULL``).
    slot: int = 0
    #: Куда идти (``HAUL``).
    city_id: str = ""
    #: Какое из названных подземелий (``DELVE``).
    dungeon_id: str = ""
    #: Какую породу выбить (``HUNT``).
    archetype_id: str = ""
    #: Какой узел обыскать — ``NodeKind`` строкой (``SEARCH``).
    node_kind: str = ""


def digest(
    content: GameContent,
    world_seed: str,
    city_id: str,
    rotation: int,
    level: int,
    *,
    moods: Mapping[int, LocationMood] | None = None,
) -> tuple[Deed, ...]:
    """Дела заставы на этот переворот. Детерминировано от аргументов.

    Четыре-пять дел, порядок постоянный: выбить названную породу в одной локации,
    разредить стаю в другой, проводить обоз до соседнего города, обыскать узел
    названного вида (только там, где у места есть гарантированный узел находок) и
    спуститься в названное подземелье. Соседних открытых городов может ещё не быть
    (низкие уровни) — тогда вместо обоза застава просит разредить ещё одну стаю.

    ``moods`` (слот локации → :class:`LocationMood`, ADR 0055) подталкивает выбор
    места: застава охотнее шлёт туда, где неспокойно. Не передан — выбор чисто по
    сиду, как раньше. Живое состояние читают одинаково и экран сводки, и место
    зачёта дела, поэтому расхождение возможно только если округа сменила
    настроение между тем и другим — и тогда дело просто не засчитается, платы
    из ниоткуда не будет.
    """
    city = content.city(city_id)
    source = rng(derive(shop_seed(world_seed, city_id, rotation), "digest"))

    spots = _combat_locations(city, level)

    hunt_here = _pick_spot(source, spots, moods)
    prey = _prey_for(content, hunt_here, source)
    deeds: list[Deed] = []
    if prey is not None:
        deeds.append(_hunt(hunt_here, prey, level))
    else:  # pragma: no cover - у дорожного пула всегда есть «*»-запас
        deeds.append(_cull(hunt_here, level))

    cull_here = _pick_spot(
        source, [loc for loc in spots if loc.slot != hunt_here.slot] or spots, moods
    )
    deeds.append(_cull(cull_here, level))

    neighbours = _haul_targets(content, city, level)
    if neighbours:
        deeds.append(_haul(source.choice(neighbours), level))
    else:
        elsewhere = [loc for loc in spots if loc.slot != cull_here.slot] or spots
        deeds.append(_cull(_pick_spot(source, elsewhere, moods), level))

    find_here = _pick_spot(source, spots, moods)
    prowl = _searchable_kinds(world_seed, city_id, find_here.slot)
    if prowl:
        deeds.append(_search(find_here, source.choice(prowl), level))

    holes = _delve_targets(city, level)
    if holes:
        deeds.append(_delve(source.choice(holes)))
    else:  # pragma: no cover - у каждого города есть обычные подземелья (ADR 0041)
        deeds.append(_cull(source.choice(spots), level))

    return tuple(deeds)


def reward(level: int) -> tuple[int, int]:
    """Надбавка за дело: золото и опыт, ``DIGEST_BONUS`` от обычного боя уровня дела."""
    lvl = max(1, level)
    gold = max(1, round(gold_at(lvl) * DIGEST_BONUS))
    experience = max(
        1, round(experience_reward(enemy_level=lvl, character_level=lvl) * DIGEST_BONUS)
    )
    return gold, experience


def closes_hunt(deed: Deed, *, slot: int, archetype_ids: tuple[str, ...]) -> bool:
    """Победа в этой локации над стаей, где была названная порода, закрывает ``HUNT``."""
    return (
        deed.kind is DeedKind.HUNT
        and deed.slot == slot
        and bool(deed.archetype_id)
        and deed.archetype_id in archetype_ids
    )


def closes_cull(deed: Deed, *, slot: int) -> bool:
    """Победа в бою в этой локации закрывает дело ``CULL`` на неё."""
    return deed.kind is DeedKind.CULL and deed.slot == slot


def closes_haul(deed: Deed, *, city_id: str) -> bool:
    """Приход в этот город по дороге закрывает дело ``HAUL`` на него."""
    return deed.kind is DeedKind.HAUL and bool(city_id) and deed.city_id == city_id


def closes_search(deed: Deed, *, slot: int, node_kind: str) -> bool:
    """Отработанный узел этого вида в этой локации закрывает дело ``SEARCH``."""
    return (
        deed.kind is DeedKind.SEARCH
        and deed.slot == slot
        and bool(deed.node_kind)
        and deed.node_kind == node_kind
    )


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


def _pick_spot(
    source: random.Random,
    spots: list[Location],
    moods: Mapping[int, LocationMood] | None,
) -> Location:
    """Выбрать локацию под дело с поправкой на состояние округи.

    ``random.choices`` тратит из сида ровно один вызов независимо от весов,
    поэтому наличие или отсутствие ``moods`` не сдвигает последующие броски: дела
    ``HAUL`` и ``DELVE`` от состояния округи не зависят вовсе, а ``HUNT``/``CULL``/
    ``SEARCH`` меняют только *куда* зовут, не *что* идёт следом.
    """
    weights = [
        _MOOD_WEIGHT.get((moods or {}).get(loc.slot, LocationMood.UNTOUCHED), 2) for loc in spots
    ]
    return source.choices(spots, weights=weights, k=1)[0]


def _prey_for(
    content: GameContent, location: Location, source: random.Random
) -> EnemyArchetype | None:
    """Кого выбить у этого места: порода из дорожного пула биома локации.

    Пул — тот же, из которого локация набирает стаи (``procgen/enemies.candidates``),
    поэтому дело всегда выполнимо: названная порода здесь и правда водится.
    """
    pool = candidates(content.enemy_archetypes, location.biome, dungeon=False)
    return source.choice(list(pool)) if pool else None


#: Виды узлов-находок, под которые у сводки есть человеческая фраза. «Событие»
#: (``NodeKind.EVENT``) сюда не входит: «обыскать происшествие» звучит натужно.
_SEARCH_KINDS: tuple[NodeKind, ...] = (NodeKind.CACHE, NodeKind.GATHER)

_SEARCH_LINE: dict[str, str] = {
    NodeKind.CACHE.value: "Обыскать тайник у места «{place}».",
    NodeKind.GATHER.value: "Собрать сырьё у места «{place}».",
}


def _searchable_kinds(world_seed: str, city_id: str, slot: int) -> list[NodeKind]:
    """Виды узлов-находок, которые это место держит в любом поколении, — и только
    те, о которых сводка умеет сказать словами (``_SEARCH_KINDS``)."""
    have = set(guaranteed_find_kinds(world_seed, city_id, slot))
    return [kind for kind in _SEARCH_KINDS if kind in have]


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


def _hunt(location: Location, prey: EnemyArchetype, level: int) -> Deed:
    return Deed(
        kind=DeedKind.HUNT,
        line=f"Выбить стаю «{prey.name}» у места «{location.name}».",
        where=location.name,
        level=_deed_level(location, level),
        slot=location.slot,
        archetype_id=prey.id,
    )


def _search(location: Location, kind: NodeKind, level: int) -> Deed:
    return Deed(
        kind=DeedKind.SEARCH,
        line=_SEARCH_LINE[kind.value].format(place=location.name),
        where=location.name,
        level=_deed_level(location, level),
        slot=location.slot,
        node_kind=kind.value,
    )


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
