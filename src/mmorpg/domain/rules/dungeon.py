"""Данж: процедурная развилочная вылазка с выбором сложности.

Заход в подземелье города - это не прямая цепочка боёв, а короткая
процедурная вылазка. Слой за слоем игрок выбирает, в какую из двух-трёх
комнат идти дальше; назад пути нет; последний слой - развилка между логовом
босса и ходом наверх. Карта нигде не хранится: и вид комнаты каждого слоя, и
её развилка - чистая функция от сида захода и номера слоя.

Сложность - множитель поверх места. Уровень спуска задаёт выбранное подземелье
(``[[city.dungeon]]``, ADR 0041), а сложность домножает силу врагов и плату и,
на всём, что выше «разведки», бросает на весь заход одно-два случайных условия
и прозвище-модификатор на стаю (ADR 0036, ADR 0042).

Всё здесь чистое: ни времени, ни ввода-вывода. Сид и номер слоя приходят
аргументом.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.procgen.seeds import derive, rng


class Difficulty(StrEnum):
    """Три сложности захода. Условия бросают все, кроме «разведки»."""

    RECON = "recon"
    DELVE = "delve"
    GRIM = "grim"


class RoomKind(StrEnum):
    """Что стоит в комнате слоя.

    Все виды, кроме «хода наверх», - это бой; отличаются они ступенью
    противника и тем, насколько победа в них латает раны.
    """

    SKIRMISH = "skirmish"
    BEAST = "beast"
    HOLLOW = "hollow"
    LAIR = "lair"
    STAIRS = "stairs"

    @property
    def rank(self) -> EnemyRank:
        match self:
            case RoomKind.BEAST:
                return EnemyRank.ELITE
            case RoomKind.LAIR:
                return EnemyRank.BOSS
            case _:
                return EnemyRank.NORMAL


@dataclass(frozen=True, slots=True)
class DifficultySpec:
    """Что сложность делает с заходом."""

    kind: Difficulty
    #: Во сколько раз крепче враги и щедрее их плата.
    stakes: float
    #: Сколько слоёв прибавляет к базовой глубине (три и по одной за Печать).
    extra_layers: int
    #: Сколько случайных условий бросает на весь заход.
    conditions: int
    #: С какой вероятностью у стаи будет прозвище-модификатор (ADR 0042).
    affix_chance: float = 0.0
    #: Сколько прозвищ вешать на стаю, когда бросок прошёл.
    affix_count: int = 0


DIFFICULTIES: Mapping[Difficulty, DifficultySpec] = MappingProxyType(
    {
        Difficulty.RECON: DifficultySpec(
            Difficulty.RECON, stakes=1.0, extra_layers=0, conditions=0
        ),
        Difficulty.DELVE: DifficultySpec(
            Difficulty.DELVE,
            stakes=1.5,
            extra_layers=1,
            conditions=1,
            affix_chance=0.35,
            affix_count=1,
        ),
        Difficulty.GRIM: DifficultySpec(
            Difficulty.GRIM,
            stakes=2.1,
            extra_layers=2,
            conditions=2,
            affix_chance=0.70,
            affix_count=2,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class AffixOdds:
    """Шанс и число прозвищ для стаи вне выбора сложности захода."""

    chance: float
    count: int


#: Прозвища на узлах локации: только у сильного одиночки и хозяина логова, и
#: никогда у обычной стаи (ADR 0042). Эпиков в локации и так мало (ADR 0034),
#: так что прозвище тут - редкая встреча, а не общий фон.
NODE_AFFIX_ODDS: Mapping[EnemyRank, AffixOdds] = MappingProxyType(
    {
        EnemyRank.ELITE: AffixOdds(chance=0.50, count=1),
        EnemyRank.BOSS: AffixOdds(chance=0.90, count=1),
    }
)


def affix_odds(rank: EnemyRank) -> AffixOdds:
    """Шанс прозвища для противника этой ступени на узле локации."""
    return NODE_AFFIX_ODDS.get(rank, AffixOdds(chance=0.0, count=0))


#: Насколько бой в комнате этого вида слабее обычного. Логово и зверь крепче
#: не здесь, а ступенью (``RoomKind.rank``); «затишье» - лёгкий бой и передышка.
ROOM_STAKES: Mapping[RoomKind, float] = MappingProxyType(
    {
        RoomKind.SKIRMISH: 1.0,
        RoomKind.BEAST: 1.0,
        RoomKind.HOLLOW: 0.55,
        RoomKind.LAIR: 1.0,
        RoomKind.STAIRS: 0.0,
    }
)

#: На сколько процентов максимума здоровья латает раны победа в комнате.
#: Пассивного восстановления между боями нет - только это и зелья (ADR 0036).
ROOM_HEAL_PERCENT: Mapping[RoomKind, int] = MappingProxyType(
    {
        RoomKind.SKIRMISH: 10,
        RoomKind.BEAST: 6,
        RoomKind.HOLLOW: 45,
        RoomKind.LAIR: 0,
        RoomKind.STAIRS: 0,
    }
)

#: Виды комнат внутри захода и их веса на развилке. Одинаковых в одной
#: развилке не бывает: две кнопки с одной надписью читались бы одной строкой.
_FORK_ROOMS: tuple[tuple[RoomKind, int], ...] = (
    (RoomKind.SKIRMISH, 6),
    (RoomKind.HOLLOW, 3),
    (RoomKind.BEAST, 2),
)

#: Меньше двух слоёв заход не бывает: слой входа плюс логово.
MIN_FINAL_LAYER = 2


def dungeon_unlocked(
    *, deep: bool, unlock_level: int, char_level: int, deep_threshold: int
) -> bool:
    """Открыт ли этот спуск игроку (ADR 0041).

    Глубокий спуск открывает не его уровень, а та же земля - самая глубокая
    локация города (``deep_threshold``); обычный - своё ``unlock_level``
    (у большинства это ноль).
    """
    if deep:
        return char_level >= deep_threshold
    return char_level >= unlock_level


def difficulty_of(value: str) -> Difficulty:
    """Сложность по ключу; неизвестное читается как «разведка» (правило 8)."""
    try:
        return Difficulty(value)
    except ValueError:
        return Difficulty.RECON


def spec_of(difficulty: Difficulty) -> DifficultySpec:
    return DIFFICULTIES[difficulty]


def room_of(value: str) -> RoomKind:
    """Вид комнаты по ключу; неизвестное читается как обычная схватка."""
    try:
        return RoomKind(value)
    except ValueError:
        return RoomKind.SKIRMISH


def final_layer(base_depth: int, difficulty: Difficulty) -> int:
    """Номер последнего слоя - логова и хода наверх. Слои идут 0..final_layer.

    ``base_depth`` - сколько схваток в спуске у этого персонажа
    (``turning.descent_depth``: три и по одной за Печать). Сложность
    добавляет свои слои сверху.
    """
    return max(MIN_FINAL_LAYER, base_depth + spec_of(difficulty).extra_layers)


def run_seed(
    world_seed: str, city_id: str, dungeon_id: str, difficulty: Difficulty, started_at: int
) -> bytes:
    """Сид одного захода: два захода подряд - два разных подземелья.

    ``dungeon_id`` - какое из названных подземелий города (ADR 0041): у каждого
    свой сид, поэтому «Барсучьи ходы» и «Затопленный штрек» никогда не совпадут.
    """
    return derive(world_seed, "dungeon", city_id, dungeon_id, difficulty.value, started_at)


def room_options(seed: bytes, layer: int, final: int) -> tuple[RoomKind, ...]:
    """Из каких комнат выбирают, входя в ``layer``.

    Слой 0 - вход, выбора нет: всегда обычная схватка. Последний слой - две
    двери: логово и ход наверх, и драться с боссом - решение (ADR 0019). Между
    ними - две-три разных комнаты, вытянутые из сида слоя.
    """
    if layer <= 0:
        return (RoomKind.SKIRMISH,)
    if layer >= final:
        return (RoomKind.LAIR, RoomKind.STAIRS)
    source = rng(derive(seed, "layer", layer))
    count = source.choice((2, 2, 3))
    pool = [kind for kind, _ in _FORK_ROOMS]
    weights = [weight for _, weight in _FORK_ROOMS]
    picked: list[RoomKind] = []
    while len(picked) < count and pool:
        chosen = source.choices(pool, weights=weights, k=1)[0]
        index = pool.index(chosen)
        pool.pop(index)
        weights.pop(index)
        picked.append(chosen)
    return tuple(picked)


# --- случайные условия захода ---------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """Одно условие захода: беда или благо на все его бои.

    ``hero_effects`` и ``enemy_effects`` навешиваются в начале каждого боя
    захода и держатся весь бой (``permanent``): ходом и очищением их не снять.
    ``bounty`` домножает золото со всех комнат.
    """

    id: str
    name: str
    text: str
    good: bool
    hero_effects: tuple[ActiveEffect, ...] = ()
    enemy_effects: tuple[ActiveEffect, ...] = ()
    bounty: float = 1.0


def _lasting(slug: str, name: str, modifiers: Mapping[str, float], *, good: bool) -> ActiveEffect:
    """Постоянная прибавка на весь бой - основа большинства условий."""
    return ActiveEffect(
        id=f"dungeon:{slug}",
        name=name,
        modifiers=MappingProxyType(dict(modifiers)),
        turns_left=1,
        beneficial=good,
        permanent=True,
    )


_HAZARDS: tuple[Condition, ...] = (
    Condition(
        "gloom",
        "Промозглая тьма",
        "Темно и сыро: инициатива падает на весь заход.",
        good=False,
        hero_effects=(
            _lasting("gloom", "Промозглая тьма", {"initiative_percent": -15.0}, good=False),
        ),
    ),
    Condition(
        "stale_air",
        "Спёртый воздух",
        "Дышать нечем: лечение помогает вполовину слабее.",
        good=False,
        hero_effects=(
            _lasting("stale_air", "Спёртый воздух", {"healing_taken_percent": -35.0}, good=False),
        ),
    ),
    Condition(
        "oppressive",
        "Гнетущий свод",
        "Свод давит: по вам бьют заметно сильнее.",
        good=False,
        hero_effects=(
            _lasting("oppressive", "Гнетущий свод", {"damage_taken_percent": 12.0}, good=False),
        ),
    ),
    Condition(
        "aching",
        "Ломота в костях",
        "Холод сковывает руки: ваш удар слабее.",
        good=False,
        hero_effects=(
            _lasting("aching", "Ломота в костях", {"damage_percent": -10.0}, good=False),
        ),
    ),
)

_BOONS: tuple[Condition, ...] = (
    Condition(
        "rich_seam",
        "Богатая порода",
        "Стены в прожилках руды: золота со всего захода больше.",
        good=True,
        bounty=1.4,
    ),
    Condition(
        "old_cache",
        "Старый схрон",
        "Кто-то прятал здесь добро: находки богаче.",
        good=True,
        hero_effects=(
            _lasting(
                "old_cache",
                "Старый схрон",
                {"drop_rate_percent": 20.0, "rarity_percent": 15.0},
                good=True,
            ),
        ),
    ),
    Condition(
        "stillness",
        "Затишье",
        "Тварей будто выморило: враги медлительны.",
        good=True,
        enemy_effects=(
            _lasting("stillness", "Затишье", {"initiative_percent": -20.0}, good=False),
        ),
    ),
    Condition(
        "steady_hand",
        "Твёрдая рука",
        "Здесь легко дышится и метко бьётся: чаще проходит крит.",
        good=True,
        hero_effects=(
            _lasting("steady_hand", "Твёрдая рука", {"crit_chance_percent": 12.0}, good=True),
        ),
    ),
)

CONDITIONS: tuple[Condition, ...] = (*_HAZARDS, *_BOONS)


def conditions_for(seed: bytes, difficulty: Difficulty) -> tuple[Condition, ...]:
    """Какие условия выпали этому заходу. «Разведка» - ни одного.

    Одна беда и одно благо всегда идут в паре, когда условий два: заход не
    должен оказаться ни целиком гиблым, ни целиком щедрым.
    """
    count = spec_of(difficulty).conditions
    if count <= 0:
        return ()
    source = rng(derive(seed, "conditions"))
    if count == 1:
        return (source.choice(CONDITIONS),)
    return (source.choice(_HAZARDS), source.choice(_BOONS))


def bounty_of(conditions: tuple[Condition, ...]) -> float:
    """Общий множитель золота от выпавших условий."""
    factor = 1.0
    for one in conditions:
        factor *= one.bounty
    return factor
