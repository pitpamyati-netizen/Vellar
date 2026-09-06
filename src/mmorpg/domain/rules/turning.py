"""Новое имя: три ступени, на которых уровень кончается и начинается заново.

Уровень кончается трижды - на семьдесят пятом, на сотом и на сто пятидесятом.
Каждый раз приключенец идёт в управу и просит у Престола новое имя: уровень
падает до первого, опыт обнуляется, а розданные очки характеристик возвращаются
нерозданными (ADR 0070).

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО УХОДА. Прежде уход не отбирал ничего и давал
десять очков с потолком на пятом разе: вторая дорога была той же самой дорогой,
только длиннее. Теперь уход **сбрасывает раздачу** и платит за это процентом к
характеристикам - навсегда и поверх всего прочего. Тот же человек проходит те же
уровни, став больше, и вправе собраться иначе.

НАСЛЕДИЕ. Розданные очки вернулись - значит характеристики упали, а с ними
теряются вехи (ADR 0068): пороги, взятые за сто пятьдесят уровней, разом
перестают выполняться. Наследие - ответ на такую потерю: перед уходом игрок
**называет** столько вех, сколько у него слотов, и названные продолжают
работать, чего бы ни показывали характеристики.

Называют, а не берут лучшее по списку: какая веха была для этой сборки главной,
знает игрок, а не порядок в файле.

Всё здесь чистое: функция возвращает нового персонажа или ``None``, а словами
отказ объясняет экран.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Rebirth
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.rules import milestones as milestone_rules

#: Титул того, кто ещё не уходил. Пусто, а не «Никто»: у человека без титула
#: титула нет, а не есть титул «нет титула».
NO_TITLE = ""


def next_rebirth(content: GameContent, character: Character) -> Rebirth | None:
    """Ступень, которую этому персонажу предстоит взять. ``None`` - все взяты."""
    return content.rebirth_at(character.remorts + 1)


def current_rebirth(content: GameContent, character: Character) -> Rebirth | None:
    """Ступень, на которой персонаж стоит сейчас. ``None`` - ни одной не брал."""
    return content.rebirth_at(character.remorts) if character.remorts else None


def title(content: GameContent, remorts: int) -> str:
    """Титул того, кто брал новое имя ``remorts`` раз. Пусто - ни разу не брал."""
    titles = content.rebirth_titles
    if remorts <= 0 or not titles:
        return NO_TITLE
    return titles[min(remorts, len(titles)) - 1]


def stat_bonus(content: GameContent, character: Character) -> float:
    """На сколько процентов выше характеристики этого персонажа.

    Берётся у ступени, на которой он стоит, а не складывается по всем пройденным:
    у второго ухода написано, каким ты стал ПОСЛЕ него. Складывать проценты
    значило бы, что число на экране нельзя проверить, не помня всей дороги.
    """
    step = current_rebirth(content, character)
    return step.stat_bonus if step is not None else 0.0


def legacy_slots(content: GameContent, character: Character) -> int:
    """Сколько вех этот персонаж вправе унести через следующий сброс."""
    step = next_rebirth(content, character)
    return step.legacy_slots if step is not None else 0


def refusal(content: GameContent, character: Character) -> str:
    """Пусто, когда новое имя можно взять, иначе - почему нельзя."""
    step = next_rebirth(content, character)
    if step is None:
        return (
            "Имён больше не дают: вы прошли все ступени, какие Престол ведёт в книге. "
            "Дальше растёт только вложенное."
        )
    if character.level < step.level:
        return (
            f"«{step.name}» просят с {step.level} уровня. Ваш уровень: {character.level}. "
            "До этого в управе смотрят на уровень и не более того."
        )
    return ""


def may_carry(content: GameContent, character: Character) -> tuple[str, ...]:
    """Вехи, которые сейчас можно назвать наследием: взятые и ещё не названные."""
    named = set(character.legacy_ids)
    return tuple(
        item.trait_id
        for item in milestone_rules.reached(content, character)
        if item.trait_id and item.trait_id not in named
    )


def carry(content: GameContent, character: Character, trait_id: str) -> Character | None:
    """Назвать веху наследием. ``None`` - слотов нет, веха не взята или уже названа."""
    if trait_id in character.legacy_ids:
        return None
    if trait_id not in may_carry(content, character):
        return None
    if len(character.legacy_ids) >= legacy_slots(content, character):
        return None
    return character.with_legacy((*character.legacy_ids, trait_id))


def drop(character: Character, trait_id: str) -> Character | None:
    """Снять веху с наследия. ``None`` - её там не было."""
    if trait_id not in character.legacy_ids:
        return None
    return character.with_legacy(tuple(one for one in character.legacy_ids if one != trait_id))


def legacy_trait_ids(content: GameContent, character: Character) -> tuple[str, ...]:
    """Вехи, которые работают наследием, а не порогом.

    Только те, что сейчас **не** держатся своими характеристиками: иначе одна и
    та же веха вошла бы в свёрток дважды и обещала бы вдвое (``Claude.md``,
    правило 7). И только те, чья черта в содержимом ещё есть.
    """
    standing = set(milestone_rules.trait_ids(content, character))
    return tuple(
        one for one in character.legacy_ids if one not in standing and content.has_trait(one)
    )


@dataclass(frozen=True, slots=True)
class Reborn:
    """Тот, кто взял новое имя: кем он стал и что за это получил."""

    character: Character
    #: Ступень, которую он только что взял.
    step: Rebirth
    #: Титул после ухода, словами игрока.
    title: str
    #: Сколько нераспределённых очков у него оказалось на первом уровне.
    stat_points: int
    #: Сколько вех ушло с ним.
    carried: int


def become(content: GameContent, character: Character) -> Reborn | None:
    """Взять новое имя. Уровень до первого, раздача заново, прибавка навсегда."""
    if refusal(content, character):
        return None
    step = next_rebirth(content, character)
    assert step is not None  # refusal() уже сказал бы, что ступени нет
    # Наследие обрезается по слотам ступени: игрок мог назвать вехи, когда слотов
    # было больше, - например, до того, как содержимое поправили.
    carried = character.legacy_ids[: step.legacy_slots]
    points = content.rules.free_points_at_creation + step.stat_points
    reborn = replace(
        character,
        level=1,
        experience=0,
        health=0,
        remorts=character.remorts + 1,
        # Раздача заново - вот что делает уход уходом, а наследие - наследием.
        allocated=StatBlock(),
        unspent_stat_points=points,
        legacy_ids=carried,
    )
    return Reborn(
        character=reborn,
        step=step,
        title=title(content, reborn.remorts),
        stat_points=points,
        carried=len(carried),
    )
