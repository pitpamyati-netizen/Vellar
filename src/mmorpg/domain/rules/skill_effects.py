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
    self_damage_taken: float = 0.0
    dot_turns: int = 0
    stun_turns: int = 0
    # Healing: power is a flat amount unless heal_percent is set, in which case it
    # is a percentage of maximum health.
    heal_percent: bool = False
    execute_scaling: float = 0.0
    chain_falloff: float = 0.0
    self_modifiers: tuple[ModifierSpec, ...] = ()
    target_modifiers: tuple[ModifierSpec, ...] = ()
    duration: int = 0
    cleanse_count: int = 0
    special: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


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
    "damage_execute": _damage(execute_scaling=1.0),
    "damage_initiative": _damage(special="haste_self"),
    "damage_steal": _damage(special="steal_gold"),
    "damage_companion": _damage(dot_turns=3, damage_scale=0.6, special="companion"),
    "damage_and_heal": _damage(aoe=True, special="full_heal"),
    # --- area damage ---
    "damage_aoe": _damage(aoe=True),
    "damage_aoe_fire": _damage(aoe=True, tags=("fire",)),
    "damage_aoe_holy": _damage(aoe=True, tags=("holy",)),
    "damage_aoe_nature": _damage(aoe=True, tags=("nature",)),
    "damage_aoe_arcane": _damage(aoe=True, tags=("arcane",)),
    "damage_aoe_elemental": _damage(aoe=True, tags=("elemental",)),
    "damage_aoe_slow": _damage(
        aoe=True, target_modifiers=(M("initiative_percent", -25.0),), duration=2
    ),
    "damage_aoe_stun": _damage(aoe=True, stun_turns=1),
    "damage_chain": _damage(aoe=True, chain_falloff=0.3),
    # --- healing and shields ---
    "heal": EffectSpec(category=EffectCategory.HEAL),
    "heal_big": EffectSpec(category=EffectCategory.HEAL),
    "heal_percent": EffectSpec(category=EffectCategory.HEAL, heal_percent=True),
    "heal_over_time": EffectSpec(
        category=EffectCategory.HEAL, dot_turns=3, special="heal_over_time"
    ),
    "shield": EffectSpec(category=EffectCategory.SHIELD),
    "cleanse_shield": EffectSpec(
        category=EffectCategory.SHIELD, cleanse_count=99, special="cleanse_and_shield"
    ),
    # --- cleansing ---
    "cleanse": EffectSpec(category=EffectCategory.CLEANSE, cleanse_count=2),
    "cleanse_dodge": EffectSpec(
        category=EffectCategory.CLEANSE,
        cleanse_count=1,
        self_modifiers=(M("dodge_percent"),),
        duration=3,
    ),
    # --- self buffs ---
    "buff_damage": _buff(self_modifiers=(M("damage_percent"),)),
    "buff_armor": _buff(self_modifiers=(M("armor_percent"),)),
    "buff_dodge": _buff(duration=2, self_modifiers=(M("dodge_percent"),)),
    # A power of 30 here means "30 percent less damage taken".
    "buff_damage_taken": _buff(self_modifiers=(M("damage_taken_percent", scale=-1.0),)),
    "buff_lifesteal": _buff(lifesteal=0.3),
    "buff_counter": _buff(special="counter"),
    "buff_all_stats": _buff(
        self_modifiers=(
            M("stat_STR", 2.0),
            M("stat_AGI", 2.0),
            M("stat_END", 2.0),
            M("stat_INT", 2.0),
            M("stat_WIS", 2.0),
            M("stat_CHA", 2.0),
            M("stat_LCK", 2.0),
        ),
    ),
    "buff_avatar": _buff(
        duration=4, self_modifiers=(M("damage_percent", 100.0),), special="unstunnable"
    ),
    "buff_form_bear": _buff(self_modifiers=(M("health_percent"), M("armor_percent"))),
    "buff_form_wolf": _buff(self_modifiers=(M("damage_percent"), M("initiative_percent"))),
    "buff_evade_full": EffectSpec(category=EffectCategory.SPECIAL, special="evade_next"),
    "buff_free_cast": EffectSpec(category=EffectCategory.SPECIAL, special="free_cast"),
    "buff_cooldown_reset": EffectSpec(category=EffectCategory.SPECIAL, special="cooldown_reset"),
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
    "taunt": _debuff(target_modifiers=(M("damage_percent", scale=-1.0),), special="taunt"),
    # --- special ---
    "avoid_combat": EffectSpec(category=EffectCategory.SPECIAL, special="avoid_combat"),
}


def spec_for(effect: str) -> EffectSpec:
    """Look up an effect spec, failing loudly on unknown content."""
    try:
        return EFFECT_SPECS[effect]
    except KeyError as error:
        msg = f"skill effect {effect!r} has no implementation in EFFECT_SPECS"
        raise KeyError(msg) from error


def known_effects() -> frozenset[str]:
    return frozenset(EFFECT_SPECS)
