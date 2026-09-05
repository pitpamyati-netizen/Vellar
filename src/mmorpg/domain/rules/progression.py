"""Опыт, уровни и очки, которые уровень даёт.

Чистая арифметика по заранее посчитанной таблице: кривая считается один раз при
импорте для уровней 1..150, поэтому ``level_for_experience`` - двоичный поиск, а
не проход по уровням.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mmorpg.domain.rules import modifiers as mods

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from mmorpg.domain.entities.character import Character
    from mmorpg.domain.entities.content import GameContent

#: Потолок роста. Сто пятьдесят, а не триста (ADR 0058): лишние уровни ничего
#: не открывали, а растягивали одно и то же вдвое.
MAX_LEVEL = 150
#: Кривая опыта. С показателем 1,45 путь до потолка - около пяти тысяч боёв:
#: четыре боя на первый уровень, около семидесяти на сто пятидесятый (ADR 0058).
_CURVE_FACTOR = 50.0
_CURVE_EXPONENT = 1.45


def experience_to_next_level(level: int) -> int:
    """Сколько опыта нужно, чтобы уйти с ``level`` на ``level + 1``."""
    if level < 1 or level >= MAX_LEVEL:
        return 0
    cost: float = _CURVE_FACTOR * float(level) ** _CURVE_EXPONENT
    return round(cost)


def _build_thresholds() -> tuple[int, ...]:
    """Накопленный опыт, нужный, чтобы *достичь* каждого уровня; индекс = уровень - 1."""
    thresholds = [0]
    total = 0
    for level in range(1, MAX_LEVEL):
        total += experience_to_next_level(level)
        thresholds.append(total)
    return tuple(thresholds)


EXPERIENCE_THRESHOLDS: tuple[int, ...] = _build_thresholds()


def experience_to_reach(level: int) -> int:
    """Сколько всего опыта нужно, чтобы дойти до ``level`` с нуля."""
    clamped = max(1, min(level, MAX_LEVEL))
    return EXPERIENCE_THRESHOLDS[clamped - 1]


def level_for_experience(experience: int) -> int:
    """Уровень персонажа, у которого столько всего опыта."""
    if experience <= 0:
        return 1
    index = bisect.bisect_right(EXPERIENCE_THRESHOLDS, experience)
    return min(index, MAX_LEVEL)


def experience_into_level(experience: int) -> tuple[int, int]:
    """Продвижение внутри нынешнего уровня в виде ``(набрано, нужно)``.

    На последнем уровне это ``(0, 0)``: заполнять уже нечего.
    """
    level = level_for_experience(experience)
    if level >= MAX_LEVEL:
        return 0, 0
    earned = experience - experience_to_reach(level)
    needed = experience_to_next_level(level)
    return earned, needed


@dataclass(frozen=True, slots=True)
class LevelUp:
    """Что персонаж получил, взяв уровень."""

    previous_level: int
    new_level: int
    stat_points: int
    skill_points: int
    unlocked_skills: tuple[str, ...] = ()

    @property
    def levels_gained(self) -> int:
        return self.new_level - self.previous_level


def apply_experience(
    *,
    current_level: int,
    current_experience: int,
    gained: int,
    stat_points_per_level: int,
    levels_per_skill_point: int,
) -> LevelUp:
    """Посчитать смену уровня, вызванную ``gained`` опыта."""
    if gained < 0:
        msg = "experience gain cannot be negative"
        raise ValueError(msg)
    new_level = level_for_experience(current_experience + gained)
    levels = max(0, new_level - current_level)
    reached = max(current_level, new_level)
    return LevelUp(
        previous_level=current_level,
        new_level=reached,
        stat_points=levels * stat_points_per_level,
        skill_points=skill_points_between(current_level, reached, levels_per_skill_point),
    )


def skill_points_between(previous_level: int, new_level: int, levels_per_point: int) -> int:
    """Сколько очков умений принесли уровни, взятые между двумя числами.

    Очко приходит через уровень, поэтому считается разностью долей, а не
    умножением: иначе «каждые два уровня» врало бы на нечётных (ADR 0067).
    """
    step = max(1, levels_per_point)
    return max(0, max(0, new_level) // step - max(0, previous_level) // step)


#: Прибавка к опыту. Ключ лежал в словаре особенностей с самого начала - его
#: обещали «Закалённый», «Долг души», «Схватывает на лету» и раса человека, -
#: и не читал его никто (``Roadmap.md``, ADR 0018). Читается здесь: опыт
#: становится уровнем в одном месте, значит и прибавка к нему одна на всю игру.
EXPERIENCE_KEY = "exp_percent"


def earned(content: GameContent, character: Character, gained: int) -> int:
    """Сколько опыта на самом деле достанется этому персонажу.

    Прибавка к опыту - обычная прибавка (``exp_percent``), и считается она в
    одном месте, здесь. Отчёт о бое, о задании и о дне спуска называет игроку
    это же число: сказать «99 опыта» и записать 104 - это та же ложь, что и
    прибавка, которой никто не считает (``Claude.md``, правило 7).
    """
    if gained <= 0:
        return gained
    share = mods.percent(mods.collect_modifiers(content, character), EXPERIENCE_KEY)
    return max(1, round(gained * max(0.0, share)))


def grant_experience(
    content: GameContent, character: Character, gained: int
) -> tuple[Character, LevelUp]:
    """Единственное место, где опыт превращается в уровень.

    Каждый источник - бой, задание, тайник - проходит здесь, поэтому уровень всегда
    приносит одни и те же очки, чем бы он ни был заработан.
    """
    rules = content.rules
    gained = earned(content, character, gained)
    level_up = apply_experience(
        current_level=character.level,
        current_experience=character.experience,
        gained=gained,
        stat_points_per_level=rules.stat_points_per_level,
        levels_per_skill_point=rules.levels_per_skill_point,
    )
    grown = character.with_experience(gained)
    if level_up.levels_gained:
        grown = grown.with_level(
            level_up.new_level,
            stat_points=level_up.stat_points,
            skill_points=level_up.skill_points,
        )
    return grown, level_up


def growth(content: GameContent, previous_level: int, new_level: int) -> LevelUp | None:
    """Что дали уровни, взятые между двумя числами. ``None`` - ни одного.

    Одно действие бывает не одним источником опыта: бой платит за схватку, дно
    спуска - за спуск, задание - за задание, и всё это внутри одного нажатия.
    Уровень объявляется один раз и по разнице уровней, а не по каждому
    источнику: игрок взял его один, а не трижды.
    """
    levels = new_level - previous_level
    if levels <= 0:
        return None
    rules = content.rules
    return LevelUp(
        previous_level=previous_level,
        new_level=new_level,
        stat_points=levels * rules.stat_points_per_level,
        skill_points=skill_points_between(previous_level, new_level, rules.levels_per_skill_point),
    )


def experience_reward(*, enemy_level: int, character_level: int, base: int = 12) -> int:
    """Опыт за побеждённого противника, урезанный, когда он сильно ниже игрока.

    Драться сильно ниже своего уровня - не способ фармить: на разнице в пять
    уровней награда падает до десятой части (ADR 0058).
    """
    difference = character_level - enemy_level
    penalty = max(0.1, 1.0 - max(0, difference) * 0.18)
    scaled: float = base * float(enemy_level) ** 0.9 * penalty
    return max(1, round(scaled))
