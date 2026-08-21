"""What each skill effect actually does.

``content/skills.toml`` declares an ``effect`` string per active skill; this table
says how the engine executes it. The two must stay in sync, and
``tests/domain/test_combat.py`` fails if content introduces an effect with no spec
here - that is the guard rail promised in the content guide.

Adding a skill never needs code. Adding a new *kind* of skill behaviour does:
one entry here, and the executor already knows how to run it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.content import Skill


class EffectCategory(StrEnum):
    DAMAGE = "damage"
    HEAL = "heal"
    SHIELD = "shield"
    BUFF = "buff"
    DEBUFF = "debuff"
    CLEANSE = "cleanse"
    SPECIAL = "special"


@dataclass(frozen=True, slots=True)
class ModifierSpec:
    """One modifier a skill applies.

    ``value`` fixes the amount; leaving it ``None`` means "use the skill's power at
    its current rank", scaled by ``scale`` - which is how a debuff turns a power of
    30 into a penalty of -30 percent.
    """

    key: str
    value: float | None = None
    scale: float = 1.0

    def amount(self, power: float) -> float:
        return self.value if self.value is not None else power * self.scale


def M(key: str, value: float | None = None, scale: float = 1.0) -> ModifierSpec:  # noqa: N802
    """Shorthand used by the table below."""
    return ModifierSpec(key=key, value=value, scale=scale)


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """A parametrised description of one skill effect."""

    category: EffectCategory
    aoe: bool = False
    hits: int = 1
    damage_scale: float = 1.0
    pierce: float = 0.0
    guaranteed_crit: bool = False
    crit_bonus: float = 0.0
    lifesteal: float = 0.0
    #: Доля урона, достающаяся второй цели у одноцелевого удара. Ставится только
    #: гранью: умение, бьющее двоих, описывается как ``aoe`` или ``damage_chain``.
    splash: float = 0.0
    #: Лечение и щит сверх основного действия, в процентах от максимума здоровья.
    #: Тоже вотчина граней: «дополнительно лечит 10 процентов» у удара.
    bonus_heal: float = 0.0
    bonus_shield: float = 0.0
    self_damage_taken: float = 0.0
    dot_turns: int = 0
    #: Сколько ходов держится щит. Ноль - щит не сгорает сам, и так было у всех
    #: четырёх щитов игры: текст обещал «3 хода», а щит стоял до конца боя.
    shield_turns: int = 0
    stun_turns: int = 0
    execute_scaling: float = 0.0
    chain_falloff: float = 0.0
    self_modifiers: tuple[ModifierSpec, ...] = ()
    target_modifiers: tuple[ModifierSpec, ...] = ()
    duration: int = 0
    cleanse_count: int = 0
    special: str = ""
    #: Сила ранга ложится в откат, а не в число. Так устроены умения «да или
    #: нет»: «Исчезновение» либо уводит от удара, либо нет, и поднимать в нём
    #: нечего. Ранг у них полгода не менял ровно ничего, и очко, вложенное в
    #: такой ранг, пропадало (``Roadmap.md``, «Что осталось»). Теперь ранг
    #: возвращает умение быстрее: сила - это по-прежнему процент, только процент
    #: от того, как часто умением можно пользоваться.
    recharges: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    # The trace this effect leaves. Left out, it is read off the category by
    # `tag_of`; set it only where the category would say the wrong thing.
    tag: ActionTag | None = None


# --- служебные пометки -----------------------------------------------
#
# То, что умение вешает на себя или на цель не как прибавку, а как признак:
# «истекает кровью», «не может пасть», «отвечает на удар». Ключи нарочно не из
# словаря ``traits.toml``: их читает только бой, и в расчёт характеристик они
# попадать не должны. Подчёркивание в начале - тот же знак, что и в коде.

#: Сколько урона цель получает каждый ход, пока истекает кровью или горит.
BLEED_PER_TURN = "_bleed_per_turn"
#: Сколько здоровья возвращается каждый ход, пока держится лечение по ходам.
MEND_PER_TURN = "_mend_per_turn"
#: Доля удара, которой герой отвечает тому, кто по нему попал.
COUNTER = "_counter_percent"
#: Пока держится, здоровье не опускается ниже единицы.
UNDYING = "_undying"
#: Пока держится, пропуск хода герою не грозит.
UNSTUNNABLE = "_unstunnable"
#: Сколько щита ещё держится этим источником: щит сгорает вместе с ним.
SHIELD_HELD = "_shield_held"


def cleansed_count(spec: EffectSpec, power: float) -> int:
    """Сколько отрицательных эффектов снимет это умение на этой силе.

    У умения, которое только и делает, что снимает эффекты, сила ранга больше
    ложиться некуда: ``cleanse_count`` в спецификации говорит лишь о том, что
    умение снимает вообще. У всех прочих - тех, где снятие идёт довеском к удару
    или щиту, - число написано в спецификации и от ранга не зависит.
    """
    if spec.category is not EffectCategory.CLEANSE:
        return spec.cleanse_count
    return max(1, round(power))


def recharged(cooldown: int, spec: EffectSpec, power: float) -> int:
    """Откат умения «да или нет» на этой силе.

    Сила ранга ложится сюда, потому что больше ей лечь некуда: «Исчезновение»
    либо уводит от удара, либо нет. Сто процентов - это откат, написанный в
    содержимом; сто шестьдесят на пятом ранге - тот же откат, поделённый на
    полтора. Ниже одного хода не опускается: умение с откатом - это умение,
    которое нельзя нажимать подряд.
    """
    if not spec.recharges or not cooldown or power <= 0:
        return cooldown
    return max(1, round(cooldown * 100.0 / power))


def _damage(**kwargs: object) -> EffectSpec:
    return EffectSpec(category=EffectCategory.DAMAGE, **kwargs)  # type: ignore[arg-type]


def _buff(*, duration: int = 3, **kwargs: object) -> EffectSpec:
    return EffectSpec(category=EffectCategory.BUFF, duration=duration, **kwargs)  # type: ignore[arg-type]


def _debuff(*, duration: int = 2, **kwargs: object) -> EffectSpec:
    return EffectSpec(category=EffectCategory.DEBUFF, duration=duration, **kwargs)  # type: ignore[arg-type]


EFFECT_SPECS: dict[str, EffectSpec] = {
    # --- single target damage ---
    "damage": _damage(),
    "damage_holy": _damage(tags=("holy",)),
    "damage_fire": _damage(tags=("fire",)),
    "damage_nature": _damage(tags=("nature",)),
    "damage_crit": _damage(crit_bonus=25.0),
    "damage_guaranteed_crit": _damage(guaranteed_crit=True),
    "damage_pierce": _damage(pierce=0.5),
    "damage_savage": _damage(damage_scale=1.5, tags=("inaccurate",)),
    "damage_reckless": _damage(damage_scale=1.35, self_damage_taken=25.0),
    "damage_multi": _damage(hits=3),
    "damage_dot": _damage(dot_turns=3),
    "damage_stun": _damage(stun_turns=1),
    "damage_slow": _damage(target_modifiers=(M("initiative_percent", -30.0),), duration=2),
    # Финт - не удар, а обман: бьёт вполсилы и сбивает противнику прицел. Тег у
    # него точность, потому что целятся тут не в цель, а в её ошибку.
    "damage_feint": _damage(
        damage_scale=0.85, target_modifiers=(M("accuracy_percent", -25.0),), duration=2
    ),
    "damage_execute": _damage(execute_scaling=0.6),
    # Темп - это очередь удара буквально: инициатива собирает очередь боя, и
    # поднявший её ходит раньше (``combat._order_for``, ADR 0021).
    "damage_initiative": _damage(self_modifiers=(M("initiative_percent", 40.0),), duration=2),
    "damage_steal": _damage(special="steal_gold"),
    "damage_companion": _damage(dot_turns=3, damage_scale=0.6),
    "damage_and_heal": _damage(aoe=True, special="full_heal"),
    # --- area damage ---
    "damage_aoe": _damage(aoe=True),
    "damage_aoe_fire": _damage(aoe=True, tags=("fire",)),
    "damage_aoe_holy": _damage(aoe=True, tags=("holy",)),
    "damage_aoe_nature": _damage(aoe=True, tags=("nature",)),
    "damage_aoe_arcane": _damage(aoe=True, tags=("arcane",)),
    "damage_aoe_elemental": _damage(aoe=True, tags=("elemental",)),
    "damage_aoe_slow": _damage(
        aoe=True, tags=("cold",), target_modifiers=(M("initiative_percent", -25.0),), duration=2
    ),
    "damage_aoe_stun": _damage(aoe=True, stun_turns=1),
    "damage_chain": _damage(aoe=True, tags=("elemental",), chain_falloff=0.3),
    # --- healing and shields ---
    "heal": EffectSpec(category=EffectCategory.HEAL),
    "heal_big": EffectSpec(category=EffectCategory.HEAL),
    # Лечение по ходам не лечит сразу: ``power`` - это то, что приходит каждый
    # ход, и приходит оно ``dot_turns`` раз. Раньше срок стоял в описании и не
    # делал ничего - умение лечило один раз и молчало три хода.
    "heal_over_time": EffectSpec(
        category=EffectCategory.HEAL, dot_turns=3, special="heal_over_time"
    ),
    "shield": EffectSpec(category=EffectCategory.SHIELD, shield_turns=3),
    "shield_undying": EffectSpec(
        category=EffectCategory.SHIELD,
        shield_turns=3,
        duration=3,
        self_modifiers=(M(UNDYING, 1.0),),
    ),
    "cleanse_shield": EffectSpec(category=EffectCategory.SHIELD, shield_turns=3, cleanse_count=99),
    # --- cleansing ---
    # Сколько эффектов снимает, решает сила умения, а не это число: оно только
    # говорит, что умение вообще снимает. Стояла двойка намертво, и ранг у
    # «Очищения» не менял ничего (``cleansed_count``).
    "cleanse": EffectSpec(category=EffectCategory.CLEANSE, cleanse_count=2),
    "cleanse_dodge": EffectSpec(
        category=EffectCategory.CLEANSE,
        cleanse_count=1,
        self_modifiers=(M("dodge_percent"),),
        duration=3,
    ),
    # --- self buffs ---
    "buff_damage": _buff(self_modifiers=(M("damage_percent"),)),
    # Ярость за броню: то, что варвар и делает.
    "buff_frenzy": _buff(
        self_modifiers=(M("damage_percent"), M("armor_percent", -30.0)),
    ),
    "buff_armor": _buff(self_modifiers=(M("armor_percent"),)),
    "buff_dodge": _buff(duration=2, self_modifiers=(M("dodge_percent"),)),
    # A power of 30 here means "30 percent less damage taken".
    "buff_damage_taken": _buff(self_modifiers=(M("damage_taken_percent", scale=-1.0),)),
    # Вампиризм на срок - это прибавка себе, а не свойство одного удара:
    # ``lifesteal`` в описании читает только тот удар, которым умение бьёт, а
    # это умение не бьёт вовсе. Три хода подряд оно потому и не делало ничего.
    "buff_lifesteal": _buff(self_modifiers=(M("lifesteal_percent"),)),
    # Ответный выпад отвечает: доля удара по тому, кто ударил
    # (``combat._counterattack``).
    "buff_counter": _buff(self_modifiers=(M(COUNTER),)),
    # Прибавка к характеристикам - число, а не процент: процентов у характеристик
    # в игре нет вовсе. Число берётся из силы умения, и потому растёт с рангом -
    # раньше оно стояло двойкой намертво, и ранг ничего не менял.
    "buff_all_stats": _buff(
        self_modifiers=(
            M("stat_STR"),
            M("stat_AGI"),
            M("stat_END"),
            M("stat_INT"),
            M("stat_WIS"),
            M("stat_CHA"),
            M("stat_LCK"),
        ),
    ),
    "buff_avatar": _buff(
        duration=4,
        self_modifiers=(M("damage_percent"), M(UNSTUNNABLE, 1.0)),
    ),
    "buff_form_bear": _buff(self_modifiers=(M("health_percent"), M("armor_percent"))),
    "buff_form_wolf": _buff(self_modifiers=(M("damage_percent"), M("initiative_percent"))),
    "buff_evade_full": EffectSpec(
        category=EffectCategory.SPECIAL, special="evade_next", recharges=True
    ),
    "buff_free_cast": EffectSpec(
        category=EffectCategory.SPECIAL, special="free_cast", recharges=True
    ),
    "buff_cooldown_reset": EffectSpec(
        category=EffectCategory.SPECIAL, special="cooldown_reset", recharges=True
    ),
    # --- debuffs (power is the size of the penalty, hence scale=-1) ---
    "debuff_damage": _debuff(aoe=True, target_modifiers=(M("damage_percent", scale=-1.0),)),
    "debuff_accuracy": _debuff(aoe=True, target_modifiers=(M("accuracy_percent", scale=-1.0),)),
    "debuff_vulnerable": _debuff(duration=3, target_modifiers=(M("damage_taken_percent"),)),
    "debuff_death_mark": _debuff(duration=4, target_modifiers=(M("damage_taken_percent"),)),
    "debuff_root": _debuff(
        target_modifiers=(
            M("initiative_percent", scale=-1.0),
            M("accuracy_percent", scale=-1.0),
        ),
    ),
    # Drawing the blow onto yourself is a guard, whatever the category says.
    # Переключать цель не на что: противник и так бьёт по игроку, поэтому от
    # провокации остаётся ровно то, что она делает, - удар слабее.
    "taunt": _debuff(
        target_modifiers=(M("damage_percent", scale=-1.0),),
        tag=ActionTag.GUARD,
    ),
    # --- special ---
    "avoid_combat": EffectSpec(category=EffectCategory.SPECIAL, special="avoid_combat"),
}


def tag_of(spec: EffectSpec) -> ActionTag:
    """Which trace an effect leaves.

    A blow presses; a blow that is aimed - at armour, at a weak spot, at a mark -
    or that leaves the target hindered is precision; everything that mends,
    shields or prepares is a guard. A skill may name its tag outright, in content
    or here, and then nothing is inferred.
    """
    if spec.tag is not None:
        return spec.tag
    if spec.category is EffectCategory.DAMAGE:
        aimed = spec.pierce or spec.guaranteed_crit or spec.crit_bonus or spec.execute_scaling
        hinders = spec.target_modifiers or spec.dot_turns
        return ActionTag.PRECISION if aimed or hinders else ActionTag.PRESS
    if spec.category is EffectCategory.DEBUFF:
        return ActionTag.PRECISION
    return ActionTag.GUARD


def tag_of_skill(skill: Skill) -> ActionTag:
    """The trace a whole skill leaves - content's word first, the effect's second.

    Every class needs all three tags within reach, or a перелом is arithmetically
    impossible for it; that is what the content overrides buy.
    """
    return skill.tag if skill.tag is not None else tag_of(spec_for(skill.effect))


def spec_for(effect: str) -> EffectSpec:
    """Look up an effect spec, failing loudly on unknown content."""
    try:
        return EFFECT_SPECS[effect]
    except KeyError as error:
        msg = f"skill effect {effect!r} has no implementation in EFFECT_SPECS"
        raise KeyError(msg) from error


def known_effects() -> frozenset[str]:
    return frozenset(EFFECT_SPECS)
