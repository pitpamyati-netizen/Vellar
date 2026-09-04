"""Новое имя на 150 уровне и голос в Большом совете.

Уровень кончается, игра — нет. Дойдя до потолка, приключенец идёт в управу и
просит у Престола новое имя: уровень падает до первого, а золото, вещи и
изученные умения остаются при нём (``Narrative.md``, раздел 6). За уход дают
нераспределённые очки характеристик — с потолком, чтобы лестница после потолка
не пошла вверх без края (ADR 0011, 0048), — и титул, растущий с каждым разом.

Титул — это ещё и голос: в Большом совете он весит столько уходов, сколько за ним
стоит (до потолка). Совет спрашивает — про долю в казну, про дороги, про патент
на магию, — а ответ считают по тем, кто прошёл дорогу не по одному разу.

Всё здесь чистое: функция возвращает нового персонажа или ``None``, а словами
отказ объясняет экран.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import Turning
from mmorpg.domain.rules.progression import MAX_LEVEL

#: С какого уровня берут новое имя. Он же последний: дальше расти некуда.
MIN_LEVEL = MAX_LEVEL

#: Сколько нераспределённых очков характеристик даёт один уход и за сколько первых
#: уходов их вообще дают. Потолок держит прибавку конечной: +50 и не больше.
STAT_GIFT_PER_REMORT = 10
STAT_GIFT_CAP = 5

#: Насколько тяжёлым может стать голос в совете. Больше шести уходов силы голосу
#: не добавляют.
COUNCIL_VOTE_CAP = 6

#: Титул за уход, по порядку. Больше титулов, чем ступеней, — остаётся последняя.
TITLES: tuple[str, ...] = (
    "Вписанный",
    "Примеченный",
    "Признанный",
    "Именитый",
    "Ближний",
    "Наречённый",
)


def title(remorts: int) -> str:
    """Титул того, кто брал новое имя ``remorts`` раз. Пусто — ни разу не брал."""
    if remorts <= 0:
        return ""
    return TITLES[min(remorts, len(TITLES)) - 1]


def stat_gift(remorts: int) -> int:
    """Сколько очков даст следующий уход тому, у кого уже ``remorts`` за плечами."""
    return STAT_GIFT_PER_REMORT if remorts < STAT_GIFT_CAP else 0


def refusal(character: Character) -> str:
    """Пусто, когда новое имя можно взять, иначе — почему нельзя."""
    if character.level < MIN_LEVEL:
        return (
            f"Новое имя просят с {MIN_LEVEL} уровня. Ваш уровень: {character.level}. "
            "До этого в управе смотрят на уровень и не более того."
        )
    return ""


@dataclass(frozen=True, slots=True)
class Reborn:
    """Тот, кто взял новое имя: кем он стал и что за это получил."""

    character: Character
    #: Титул после ухода, словами игрока.
    title: str
    #: Сколько нераспределённых очков характеристик прибавил этот уход.
    stat_points: int


def become(character: Character) -> Reborn | None:
    """Взять новое имя. Уровень падает до первого, нажитое остаётся."""
    if refusal(character):
        return None
    gift = stat_gift(character.remorts)
    reborn = replace(
        character,
        level=1,
        experience=0,
        health=0,
        remorts=character.remorts + 1,
        unspent_stat_points=character.unspent_stat_points + gift,
    )
    return Reborn(character=reborn, title=title(reborn.remorts), stat_points=gift)


# --- Большой совет ------------------------------------------------------


def may_answer(character: Character) -> bool:
    """Голос есть у того, кто хоть раз брал новое имя."""
    return character.remorts > 0


def voice(character: Character) -> int:
    """Сколько весит его голос: по уходу за каждый, до потолка."""
    return min(max(0, character.remorts), COUNCIL_VOTE_CAP)


def answered(character: Character, turning: Turning) -> str:
    """Что этот персонаж ответил на открытый вопрос. Пусто — ещё не отвечал.

    Голос, поданный за прошлый цикл, в этом не считается, и ответ, которого в
    содержимом больше нет, не считается тоже (``Claude.md``, правило 8).
    """
    if character.turning_cycle != turning.id:
        return ""
    if not turning.has_option(character.turning_answer):
        return ""
    return character.turning_answer


def answer(character: Character, turning: Turning, option_id: str) -> Character | None:
    """Подать голос. ``None``, когда голоса нет, ответа такого нет или он уже подан."""
    if not may_answer(character) or not turning.has_option(option_id):
        return None
    if answered(character, turning) == option_id:
        return None
    return character.with_turning_answer(turning.id, option_id)


def leading(tally: Mapping[str, int]) -> str:
    """Ответ, за которым сейчас больше голосов. Пусто при равенстве и пустоте."""
    counted = {option: votes for option, votes in tally.items() if votes > 0}
    if not counted:
        return ""
    ranked = sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ""
    return ranked[0][0]
