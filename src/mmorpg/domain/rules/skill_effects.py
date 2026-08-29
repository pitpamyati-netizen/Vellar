"""Что именно делает каждый эффект умения.

``content/skills.toml`` называет у каждого боевого умения строку ``effect``; здесь
сказано, как движок её исполняет. Эти двое обязаны сходиться, и
``tests/domain/test_combat.py`` падает, если содержимое называет эффект, которого
здесь нет, - это и есть та страховка, что обещана в руководстве по содержимому.

Новое умение кода не требует. Новый **род** поведения требует: одна запись здесь,
и исполнитель уже знает, что с ней делать.

Состояния (``entities/statuses.py``) объявляются здесь же, списком ``inflicts``
для цели и ``holds`` для себя. Раньше половина этого была признаком внутри бойца:
``stunned`` считал ходы, горение отличалось от кровотечения только названием
умения, а «щит» был числом без имени. Теперь всё это - состояния с именами, и
умение просто называет, какое из них вешает и на сколько ходов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.content import Skill
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.statuses import StatusKind


class EffectCategory(StrEnum):
    DAMAGE = "damage"
    HEAL = "heal"
    BARRIER = "barrier"
    BUFF = "buff"
    DEBUFF = "debuff"
    CLEANSE = "cleanse"
    SPECIAL = "special"


@dataclass(frozen=True, slots=True)
class ModifierSpec:
    """Одна прибавка, которую умение накладывает.

    ``value`` называет размер прямо; оставленный пустым, он значит «взять силу
    умения на его нынешнем ранге», умноженную на ``scale``, - так помеха
    превращает силу 30 в штраф -30 процентов.
    """

    key: str
    value: float | None = None
    scale: float = 1.0

    def amount(self, power: float) -> float:
        return self.value if self.value is not None else power * self.scale


def M(key: str, value: float | None = None, scale: float = 1.0) -> ModifierSpec:  # noqa: N802
    """Короткая запись для таблицы ниже."""
    return ModifierSpec(key=key, value=value, scale=scale)


@dataclass(frozen=True, slots=True)
class Inflict:
    """Состояние, которое умение вешает, и на сколько ходов.

    ``value`` называет величину прямо; пустой - величина берётся из силы умения
    (``scale`` её поправляет). У состояний, точащих цель каждый ход, величина
    считается не отсюда, а от удара: горение сильного бойца жжёт сильнее.
    """

    kind: StatusKind
    turns: int = 2
    value: float | None = None
    scale: float = 1.0

    def magnitude(self, power: float) -> float:
        return self.value if self.value is not None else power * self.scale


def S(  # noqa: N802
    kind: StatusKind, turns: int = 2, *, value: float | None = None, scale: float = 1.0
) -> Inflict:
    """Короткая запись состояния для таблицы ниже."""
    return Inflict(kind=kind, turns=turns, value=value, scale=scale)


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """Описание одного эффекта умения, с числами."""

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
    #: Лечение и барьер сверх основного действия, в процентах от максимума
    #: здоровья. Тоже вотчина граней: «дополнительно лечит 10 процентов» у удара.
    bonus_heal: float = 0.0
    bonus_barrier: float = 0.0
    self_damage_taken: float = 0.0
    dot_turns: int = 0
    #: Каким состоянием умение точит цель, когда у него есть ``dot_turns``.
    #: По умолчанию кровотечение; огненное умение жжёт, ядовитое травит.
    dot_status: StatusKind = StatusKind.BLEEDING
    #: Сколько ходов держится барьер. Ноль - берётся общий срок.
    barrier_turns: int = 0
    stun_turns: int = 0
    execute_scaling: float = 0.0
    chain_falloff: float = 0.0
    self_modifiers: tuple[ModifierSpec, ...] = ()
    target_modifiers: tuple[ModifierSpec, ...] = ()
    #: Состояния: на цель и на себя.
    inflicts: tuple[Inflict, ...] = ()
    holds: tuple[Inflict, ...] = ()
    duration: int = 0
    cleanse_count: int = 0
    special: str = ""
    #: Сила ранга ложится в откат, а не в число. Так устроены умения «да или
    #: нет»: «Исчезновение» либо уводит от удара, либо нет, и поднимать в нём
    #: нечего. Ранг у них полгода не менял ровно ничего, и очко, вложенное в
    #: такой ранг, пропадало. Теперь ранг возвращает умение быстрее: сила - это
    #: по-прежнему процент, только процент от того, как часто им пользуются.
    recharges: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    # След, который оставляет эффект. Не назван - читается по категории в
    # `tag_of`; называется там, где категория сказала бы не то.
    tag: ActionTag | None = None

    @property
    def damage_type(self) -> DamageType | None:
        """Род урона, названный самим умением. ``None`` - бьёт своим."""
        for tag in self.tags:
            if tag in _DAMAGE_TYPE_VALUES:
                return DamageType(tag)
        return None


_DAMAGE_TYPE_VALUES = frozenset(one.value for one in DamageType)


# --- служебные пометки -----------------------------------------------
#
# То, что умение вешает на себя не состоянием, а признаком: «не может пасть»,
# «отвечает на удар». Ключи нарочно не из словаря ``traits.toml``: их читает
# только бой, и в расчёт характеристик они попадать не должны.

#: Доля удара, которой боец отвечает тому, кто по нему попал.
COUNTER = "_counter_percent"
#: Пока держится, здоровье не опускается ниже единицы.
UNDYING = "_undying"
#: Пока держится, ход у бойца не отнять ничем.
UNSTUNNABLE = "_unstunnable"


def cleansed_count(spec: EffectSpec, power: float) -> int:
    """Сколько отрицательных эффектов снимет это умение на этой силе.

    У умения, которое только и делает, что снимает эффекты, сила ранга больше
    ложиться некуда: ``cleanse_count`` в спецификации говорит лишь о том, что
    умение снимает вообще. У всех прочих - тех, где снятие идёт довеском к удару
    или барьеру, - число написано в спецификации и от ранга не зависит.
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


def _by_damage_type(*names: str, aoe: bool = False) -> dict[str, EffectSpec]:
    """Удар каждым названным родом урона - одиночный или по всем."""
    prefix = "damage_aoe_" if aoe else "damage_"
    return {f"{prefix}{name}": _damage(aoe=aoe, tags=(name,)) for name in names}


#: Рода урона, которыми умение бьёт по объявлению. Физические четыре и
#: магические одиннадцать - один список, потому что для движка разницы нет:
#: сопротивление считается по роду, а не по половине.
DAMAGE_TAGS = tuple(one.value for one in DamageType)


EFFECT_SPECS: dict[str, EffectSpec] = {
    # --- удар одной цели ---
    "damage": _damage(),
    "damage_crit": _damage(crit_bonus=12.0),
    "damage_guaranteed_crit": _damage(guaranteed_crit=True),
    "damage_pierce": _damage(pierce=0.5),
    "damage_savage": _damage(damage_scale=1.5, tags=("inaccurate",)),
    "damage_reckless": _damage(damage_scale=1.35, self_damage_taken=25.0),
    # Три удара по трети с небольшим: сила названа за удар, а не за нажатие, -
    # иначе «серия ударов» читалась бы как три полноценных удара подряд.
    "damage_multi": _damage(hits=3, damage_scale=0.33),
    "damage_double": _damage(hits=2, damage_scale=0.55),
    "damage_lifesteal": _damage(lifesteal=0.3),
    "damage_execute": _damage(execute_scaling=0.6),
    # Темп - это очередь удара буквально: инициатива собирает очередь боя, и
    # поднявший её ходит раньше (``combat._order_for``, ADR 0021).
    "damage_initiative": _damage(self_modifiers=(M("initiative_percent", 40.0),), duration=2),
    "damage_steal": _damage(special="steal_gold"),
    "damage_companion": _damage(dot_turns=3, damage_scale=0.6),
    "damage_and_heal": _damage(aoe=True, special="full_heal"),
    # Финт - не удар, а обман: бьёт вполсилы и сбивает противнику прицел. Тег у
    # него точность, потому что целятся тут не в цель, а в её ошибку.
    "damage_feint": _damage(
        damage_scale=0.85, target_modifiers=(M("accuracy_percent", -25.0),), duration=2
    ),
    # --- удар по всем ---
    "damage_aoe": _damage(aoe=True),
    "damage_chain": _damage(aoe=True, tags=("air",), chain_falloff=0.3),
    "damage_aoe_execute": _damage(aoe=True, execute_scaling=0.4),
    "damage_aoe_pierce": _damage(aoe=True, pierce=0.4),
    # --- удар, оставляющий состояние ---
    "damage_bleed": _damage(dot_turns=3, dot_status=StatusKind.BLEEDING, tags=("rending",)),
    "damage_burn": _damage(dot_turns=3, dot_status=StatusKind.BURNING, tags=("fire",)),
    "damage_venom": _damage(dot_turns=3, dot_status=StatusKind.POISON, tags=("poison",)),
    "damage_dot": _damage(dot_turns=3, dot_status=StatusKind.BLEEDING),
    "damage_aoe_burn": _damage(
        aoe=True, dot_turns=3, dot_status=StatusKind.BURNING, tags=("fire",)
    ),
    "damage_aoe_venom": _damage(
        aoe=True, dot_turns=3, dot_status=StatusKind.POISON, tags=("poison",)
    ),
    "damage_aoe_bleed": _damage(
        aoe=True, dot_turns=3, dot_status=StatusKind.BLEEDING, tags=("rending",)
    ),
    "damage_stun": _damage(stun_turns=1),
    "damage_aoe_stun": _damage(aoe=True, stun_turns=1),
    "damage_freeze": _damage(tags=("cold",), inflicts=(S(StatusKind.FREEZE, 1),)),
    "damage_aoe_freeze": _damage(aoe=True, tags=("cold",), inflicts=(S(StatusKind.FREEZE, 1),)),
    "damage_fear": _damage(tags=("mental",), inflicts=(S(StatusKind.FEAR, 1),)),
    "damage_aoe_fear": _damage(aoe=True, tags=("mental",), inflicts=(S(StatusKind.FEAR, 1),)),
    "damage_silence": _damage(tags=("arcane",), inflicts=(S(StatusKind.SILENCE, 2),)),
    "damage_aoe_silence": _damage(aoe=True, tags=("arcane",), inflicts=(S(StatusKind.SILENCE, 2),)),
    "damage_slow": _damage(tags=("cold",), inflicts=(S(StatusKind.SLOW, 2, value=30.0),)),
    "damage_aoe_slow": _damage(
        aoe=True, tags=("cold",), inflicts=(S(StatusKind.SLOW, 2, value=25.0),)
    ),
    "damage_weaken": _damage(inflicts=(S(StatusKind.WEAKNESS, 2, value=25.0),)),
    "damage_aoe_weaken": _damage(aoe=True, inflicts=(S(StatusKind.WEAKNESS, 2, value=20.0),)),
    "damage_confuse": _damage(tags=("mental",), inflicts=(S(StatusKind.CONFUSION, 2),)),
    "damage_charm": _damage(tags=("mental",), inflicts=(S(StatusKind.CHARM, 1),)),
    "damage_heal_block": _damage(tags=("negative",), inflicts=(S(StatusKind.HEAL_BLOCK, 3),)),
    "damage_resource_block": _damage(tags=("arcane",), inflicts=(S(StatusKind.RESOURCE_BLOCK, 3),)),
    # --- лечение и барьеры ---
    "heal": EffectSpec(category=EffectCategory.HEAL),
    "heal_big": EffectSpec(category=EffectCategory.HEAL),
    "heal_aoe": EffectSpec(category=EffectCategory.HEAL, aoe=True),
    # Лечение по ходам не лечит сразу: ``power`` - это то, что приходит каждый
    # ход, и приходит оно ``dot_turns`` раз.
    # Состояние вешает сам движок (``combat._mending``): величина у него - доля
    # максимума здоровья за ход, и знает её только он.
    "heal_over_time": EffectSpec(
        category=EffectCategory.HEAL, dot_turns=3, special="heal_over_time"
    ),
    "barrier": EffectSpec(category=EffectCategory.BARRIER, barrier_turns=3),
    "barrier_undying": EffectSpec(
        category=EffectCategory.BARRIER,
        barrier_turns=3,
        duration=3,
        self_modifiers=(M(UNDYING, 1.0),),
    ),
    "cleanse_barrier": EffectSpec(
        category=EffectCategory.BARRIER, barrier_turns=3, cleanse_count=99
    ),
    # --- снятие ---
    # Сколько эффектов снимает, решает сила умения, а не это число: оно только
    # говорит, что умение вообще снимает.
    "cleanse": EffectSpec(category=EffectCategory.CLEANSE, cleanse_count=2),
    "cleanse_dodge": EffectSpec(
        category=EffectCategory.CLEANSE,
        cleanse_count=1,
        self_modifiers=(M("dodge_percent"),),
        duration=3,
    ),
    # --- усиления себе ---
    "buff_damage": _buff(self_modifiers=(M("damage_percent"),)),
    # Ярость за броню: то, что варвар и делает.
    "buff_frenzy": _buff(self_modifiers=(M("damage_percent"), M("armor_percent", -30.0))),
    "buff_armor": _buff(self_modifiers=(M("armor_percent"),)),
    "buff_dodge": _buff(duration=2, self_modifiers=(M("dodge_percent"),)),
    "buff_accuracy": _buff(self_modifiers=(M("accuracy_percent"),)),
    "buff_crit": _buff(self_modifiers=(M("crit_chance_percent"),)),
    # Сила 30 здесь значит «получаемый урон ниже на 30 процентов».
    "buff_damage_taken": _buff(self_modifiers=(M("damage_taken_percent", scale=-1.0),)),
    "buff_lifesteal": _buff(self_modifiers=(M("lifesteal_percent"),)),
    # Ответный выпад отвечает: доля удара по тому, кто ударил.
    "buff_counter": _buff(self_modifiers=(M(COUNTER),)),
    "buff_reflect": _buff(self_modifiers=(M("reflect_percent"),)),
    # Прибавка к характеристикам - число, а не процент: процентов у характеристик
    # в игре нет вовсе.
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
    "buff_avatar": _buff(duration=4, self_modifiers=(M("damage_percent"), M(UNSTUNNABLE, 1.0))),
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
    # --- состояния себе ---
    "buff_empower": _buff(holds=(S(StatusKind.EMPOWER, 3),)),
    "buff_berserk": _buff(holds=(S(StatusKind.BERSERK, 3),)),
    "buff_haste": _buff(holds=(S(StatusKind.HASTE, 3),)),
    "buff_invulnerable": _buff(
        duration=1, holds=(S(StatusKind.INVULNERABILITY, 1),), recharges=True
    ),
    # Уход из виду: держится, пока боец не сделает что-то, кроме защиты, и спадает
    # от первого же долетевшего удара (``combat``, ADR 0043). Умение «да или нет»,
    # поэтому ``recharges``: сила ранга ложится в откат.
    "buff_vanish": _buff(duration=3, holds=(S(StatusKind.UNSEEN, 3),), recharges=True),
    "buff_health_regen": _buff(holds=(S(StatusKind.HEALTH_REGEN, 4),)),
    "buff_resource_regen": _buff(holds=(S(StatusKind.RESOURCE_REGEN, 4),)),
    "buff_second_wind": _buff(
        holds=(S(StatusKind.HEALTH_REGEN, 3), S(StatusKind.RESOURCE_REGEN, 3, scale=0.5)),
    ),
    # --- помехи цели (сила - размер штрафа, отсюда scale=-1) ---
    "debuff_accuracy": _debuff(aoe=True, target_modifiers=(M("accuracy_percent", scale=-1.0),)),
    "debuff_armor": _debuff(target_modifiers=(M("armor_percent", scale=-1.0),)),
    "debuff_vulnerable": _debuff(duration=3, target_modifiers=(M("damage_taken_percent"),)),
    "debuff_death_mark": _debuff(duration=4, target_modifiers=(M("damage_taken_percent"),)),
    "debuff_root": _debuff(
        target_modifiers=(
            M("initiative_percent", scale=-1.0),
            M("accuracy_percent", scale=-1.0),
        ),
    ),
    # --- состояния цели ---
    "debuff_silence": _debuff(inflicts=(S(StatusKind.SILENCE, 2),), recharges=True),
    "debuff_fear": _debuff(inflicts=(S(StatusKind.FEAR, 1),), recharges=True),
    "debuff_charm": _debuff(inflicts=(S(StatusKind.CHARM, 1),), recharges=True),
    "debuff_confusion": _debuff(inflicts=(S(StatusKind.CONFUSION, 2),), recharges=True),
    "debuff_freeze": _debuff(inflicts=(S(StatusKind.FREEZE, 1),), recharges=True),
    "debuff_stun": _debuff(inflicts=(S(StatusKind.STUN, 1),), recharges=True),
    "debuff_weakness": _debuff(inflicts=(S(StatusKind.WEAKNESS, 3),)),
    "debuff_slow": _debuff(inflicts=(S(StatusKind.SLOW, 3),)),
    "debuff_heal_block": _debuff(inflicts=(S(StatusKind.HEAL_BLOCK, 3),), recharges=True),
    "debuff_burning": _debuff(aoe=True, dot_turns=3, dot_status=StatusKind.BURNING, tags=("fire",)),
    "debuff_poison": _debuff(dot_turns=4, dot_status=StatusKind.POISON, tags=("poison",)),
    "debuff_bleeding": _debuff(dot_turns=4, dot_status=StatusKind.BLEEDING, tags=("rending",)),
    "debuff_aoe_slow": _debuff(aoe=True, inflicts=(S(StatusKind.SLOW, 2),)),
    "debuff_aoe_weakness": _debuff(aoe=True, inflicts=(S(StatusKind.WEAKNESS, 2),)),
    "debuff_aoe_fear": _debuff(aoe=True, inflicts=(S(StatusKind.FEAR, 1),), recharges=True),
    # Провокация уводит удар цели на провокатора: движок ведёт вызванного бойца
    # на того, кто крикнул (``combat._forced_target``, ADR 0027). В одиночку
    # переключать некого, поэтому она несёт с собой и то, что видно и там, -
    # провокатор прикрывается броней на те же ходы. Сила ранга ложится в эту
    # броню.
    "taunt": _debuff(
        inflicts=(S(StatusKind.TAUNT, 2),),
        self_modifiers=(M("armor_percent"),),
        tag=ActionTag.GUARD,
    ),
    # То же, но на весь двор: паладин вызывает всех сразу и вдобавок сбивает им
    # удар - это его дело в отряде, встать между стаей и товарищами.
    "taunt_aoe": _debuff(
        aoe=True,
        inflicts=(S(StatusKind.TAUNT, 2),),
        target_modifiers=(M("damage_percent", scale=-1.0),),
        tag=ActionTag.GUARD,
    ),
    # --- особое ---
    "avoid_combat": EffectSpec(category=EffectCategory.SPECIAL, special="avoid_combat"),
}

# Удары каждым родом урона: пятнадцать одиночных и пятнадцать по всем. Пишутся
# не руками по той же причине, по какой не пишется снаряжение, - это один и тот
# же удар, отличающийся только объявленным родом.
EFFECT_SPECS.update(_by_damage_type(*DAMAGE_TAGS))
EFFECT_SPECS.update(_by_damage_type(*DAMAGE_TAGS, aoe=True))


def tag_of(spec: EffectSpec) -> ActionTag:
    """Какой след оставляет эффект.

    Удар - натиск; удар прицельный - в броню, в слабое место, в метку - или
    оставляющий цель в помехах - точность; всё, что лечит, укрывает или готовит,
    - оборона. Умение может назвать свой тег прямо, в содержимом или здесь, и
    тогда ничего не выводится.
    """
    if spec.tag is not None:
        return spec.tag
    if spec.category is EffectCategory.DAMAGE:
        aimed = spec.pierce or spec.guaranteed_crit or spec.crit_bonus or spec.execute_scaling
        hinders = spec.target_modifiers or spec.dot_turns or spec.inflicts or spec.stun_turns
        return ActionTag.PRECISION if aimed or hinders else ActionTag.PRESS
    if spec.category is EffectCategory.DEBUFF:
        return ActionTag.PRECISION
    return ActionTag.GUARD


def tag_of_skill(skill: Skill) -> ActionTag:
    """След целого умения - слово содержимого первым, эффекта вторым.

    Каждому классу нужны все три тега в пределах досягаемости, иначе перелом для
    него арифметически невозможен; это и покупают пометки в содержимом.
    """
    return skill.tag if skill.tag is not None else tag_of(spec_for(skill.effect))


def spec_for(effect: str) -> EffectSpec:
    """Найти описание эффекта, громко упав на незнакомом содержимом."""
    try:
        return EFFECT_SPECS[effect]
    except KeyError as error:
        msg = f"skill effect {effect!r} has no implementation in EFFECT_SPECS"
        raise KeyError(msg) from error


def known_effects() -> frozenset[str]:
    return frozenset(EFFECT_SPECS)
