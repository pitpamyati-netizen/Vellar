"""Experience, levels and the points a level grants.

Pure arithmetic over a precomputed table: the curve is evaluated once at import
time for levels 1..300, so ``level_for_experience`` is a binary search rather than
a loop over levels.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mmorpg.domain.rules import modifiers as mods

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mmorpg.domain.entities.character import Character
    from mmorpg.domain.entities.content import GameContent

MAX_LEVEL = 300
_CURVE_FACTOR = 50.0
_CURVE_EXPONENT = 1.6


def experience_to_next_level(level: int) -> int:
    """Experience needed to go from ``level`` to ``level + 1``."""
    if level < 1 or level >= MAX_LEVEL:
        return 0
    cost: float = _CURVE_FACTOR * float(level) ** _CURVE_EXPONENT
    return round(cost)


def _build_thresholds() -> tuple[int, ...]:
    """Cumulative experience required to *reach* each level, index = level - 1."""
    thresholds = [0]
    total = 0
    for level in range(1, MAX_LEVEL):
        total += experience_to_next_level(level)
        thresholds.append(total)
    return tuple(thresholds)


EXPERIENCE_THRESHOLDS: tuple[int, ...] = _build_thresholds()


def experience_to_reach(level: int) -> int:
    """Total experience required to reach ``level`` from scratch."""
    clamped = max(1, min(level, MAX_LEVEL))
    return EXPERIENCE_THRESHOLDS[clamped - 1]


def level_for_experience(experience: int) -> int:
    """The level a character with this much total experience has."""
    if experience <= 0:
        return 1
    index = bisect.bisect_right(EXPERIENCE_THRESHOLDS, experience)
    return min(index, MAX_LEVEL)


def experience_into_level(experience: int) -> tuple[int, int]:
    """Progress inside the current level as ``(earned, needed)``.

    At the maximum level this is ``(0, 0)`` - there is nothing left to fill.
    """
    level = level_for_experience(experience)
    if level >= MAX_LEVEL:
        return 0, 0
    earned = experience - experience_to_reach(level)
    needed = experience_to_next_level(level)
    return earned, needed


@dataclass(frozen=True, slots=True)
class LevelUp:
    """What a character gained by levelling up."""

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
    skill_points_per_level: int,
) -> LevelUp:
    """Compute the level change caused by gaining ``gained`` experience."""
    if gained < 0:
        msg = "experience gain cannot be negative"
        raise ValueError(msg)
    new_level = level_for_experience(current_experience + gained)
    levels = max(0, new_level - current_level)
    return LevelUp(
        previous_level=current_level,
        new_level=max(current_level, new_level),
        stat_points=levels * stat_points_per_level,
        skill_points=levels * skill_points_per_level,
    )


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
    """The one place experience turns into a level.

    Every source - a fight, a contract, a cache - goes through here, so a level
    always brings the same points no matter what earned it.
    """
    rules = content.rules
    gained = earned(content, character, gained)
    level_up = apply_experience(
        current_level=character.level,
        current_experience=character.experience,
        gained=gained,
        stat_points_per_level=rules.stat_points_per_level,
        skill_points_per_level=rules.skill_point_per_level,
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
        skill_points=levels * rules.skill_point_per_level,
    )


def experience_reward(*, enemy_level: int, character_level: int, base: int = 12) -> int:
    """Experience for defeating an enemy, reduced when it is far below the player.

    Fighting far under your level is not a farming strategy: at 10 levels of
    difference the reward drops to a tenth.
    """
    difference = character_level - enemy_level
    penalty = max(0.1, 1.0 - max(0, difference) * 0.09)
    scaled: float = base * float(enemy_level) ** 0.9 * penalty
    return max(1, round(scaled))
