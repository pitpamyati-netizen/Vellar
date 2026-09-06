"""Сбор прибавок из всех источников в один свёрток.

Черты, изученные пассивные умения, снаряжение и действующие эффекты говорят на
одном языке (``traits.toml [meta].modifier_keys``). Проценты из разных
источников складываются и применяются **один раз** в конце, поэтому порядок
никогда не меняет итог, а применить один источник дважды нельзя по самой
постройке.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.damage import RESIST_KEYS
from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import houses as house_rules
from mmorpg.domain.rules import milestones as milestone_rules
from mmorpg.domain.rules import repair
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.domain.rules.curves import softened

STAT_MODIFIER_PREFIX = "stat_"

#: Насколько характеристики класса усиливают его лечение и как быстро эта прибавка
#: перестаёт расти (ADR 0058, 0068). Убывающая отдача здесь не украшение:
#: мудрость жреца доходит до семисот, и прямая линия обещала бы ему сорок с лишним
#: процентов сверх всего прочего - ровно та же ошибка, что число вместо доли.
#: Предел не достигается никогда, и вложенное считается всегда.
HEALING_SOFTENER = 30.0
HEALING_CEILING = 30.0

#: Ключи, которые движок действительно читает.
#:
#: Словарь ``traits.toml [meta].modifier_keys`` шире: он и есть словарь - в нём
#: лежат в том числе ключи, под которые механики пока нет. Прибавка под таким
#: ключом не прибавка, а обещание (``Claude.md``, правило 7), и умение, которое
#: её обещает, не работает, как бы честно ни звучал его текст. Поэтому у умений
#: и граней проверяется не словарь, а этот список (``tests/content``).
#:
#: Ключ попадает сюда, когда его кто-то считает, и уходит отсюда, когда перестаёт.
EFFECTIVE_KEYS: frozenset[str] = frozenset(
    {
        # бой: свой удар
        "damage_percent",
        "physical_damage_percent",
        "magic_damage_percent",
        "single_target_damage_percent",
        "aoe_damage_percent",
        "first_turn_damage_percent",
        "low_health_damage_percent",
        "wounded_target_damage_percent",
        "elite_damage_percent",
        "beast_damage_percent",
        "undead_damage_percent",
        "humanoid_damage_percent",
        "dot_damage_percent",
        "crit_chance_percent",
        "crit_damage_percent",
        "lifesteal_percent",
        # бой: чужой удар
        "damage_taken_percent",
        "armor_percent",
        # Броня числом, а не процентом: столько её даёт закрывшемуся собственный
        # уровень (``rules/combat.DEFEND_ARMOR_PER_LEVEL``), и проценты доспеха
        # её не трогают - обещано ровно это число.
        "armor_flat",
        "dodge_percent",
        "accuracy_percent",
        "initiative_percent",
        "reflect_percent",
        "flee_chance_percent",
        # запасы
        "health_percent",
        "resource_percent",
        "resource_regen_percent",
        "regen_per_turn_percent",
        "cost_reduction_percent",
        "cooldown_reduction_percent",
        "healing_done_percent",
        "healing_taken_percent",
        # что остаётся после боя
        "exp_percent",
        "gold_percent",
        "drop_rate_percent",
        "rarity_percent",
        "quest_reward_percent",
        "event_reward_percent",
        # город и ремесло
        "shop_price_percent",
        "sell_price_percent",
        "reputation_percent",
        "craft_quality_percent",
        "gather_yield_percent",
        "salvage_yield_percent",
    }
    # Сопротивления: по одному на каждый род урона плюс два на половины -
    # физическую и магическую. Считаются оба, и складываются
    # (``combat.incoming_damage_factor``).
    | RESIST_KEYS
    | {f"{STAT_MODIFIER_PREFIX}{code.value}" for code in StatCode}
)


def merge(*bundles: Mapping[str, float]) -> dict[str, float]:
    """Сложить свёртки прибавок ключ к ключу."""
    total: dict[str, float] = {}
    for bundle in bundles:
        for key, value in bundle.items():
            total[key] = total.get(key, 0.0) + value
    return total


def trait_modifiers(content: GameContent, trait_ids: Iterable[str]) -> dict[str, float]:
    return merge(*(content.trait(trait_id).modifiers for trait_id in trait_ids))


def milestone_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Что дают взятые вехи характеристик (ADR 0068).

    Веха платит обычной чертой, поэтому здесь нечего складывать особым образом:
    те же ключи, тот же ``merge``, тот же словарь. Считается веха от вложенного,
    а не от итогового - иначе этот свёрток спрашивал бы сам себя
    (``domain/rules/milestones.py``).
    """
    return trait_modifiers(content, milestone_rules.trait_ids(content, character))


def scaling_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Выходы классовой сетки, которые живут прибавкой, а не своим числом.

    Пока такой один - ``healing``: насколько характеристики этого класса
    усиливают его лечение. Он не может считаться там же, где здоровье и запас:
    лечение - это процент, а проценты в Vellar складываются в один свёрток и
    применяются один раз (ADR 0007). Значит и этот выход обязан войти в свёрток,
    а не умножить что-то у себя в углу.

    Считается от **вложенного**, как и веха, и по той же причине: свёрток не
    может спрашивать сам себя (``domain/rules/milestones.py``).
    """
    klass = subclass_rules.class_of(content, character)
    raw = klass.summed(milestone_rules.invested_stats(content, character), "healing")
    healing = softened(raw, HEALING_SOFTENER, HEALING_CEILING)
    return {"healing_done_percent": round(healing, 2)} if healing else {}


def subclass_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Что дают взятые ветки специализации (ADR 0074).

    Тот же пассивный свёрток, что у техники дома и расовой способности: всё, что
    ветка обещает прибавкой, лежит в общем словаре и проверяется по
    ``EFFECTIVE_KEYS``. Умение ветки идёт другой дорогой - через панель.
    """
    return subclass_rules.modifiers(content, character)


def house_modifiers(content: GameContent, character: Character) -> Mapping[str, float]:
    """Что даёт техника дома, в котором игрок состоит. Пусто — он ни в каком.

    Ровно как расовая способность: пассивный свёрток, включённый членством
    (``domain/rules/houses.py``, ADR 0049).
    """
    return house_rules.technique_modifiers(content, character)


def race_modifiers(content: GameContent, character: Character) -> Mapping[str, float]:
    """Что даёт расовая пассивная способность.

    Шестнадцать рас, шестнадцать названных вслух способностей - и ни одна из них
    не делала ничего: ``RacePassive`` был идентификатором, именем и текстом, а
    сюда не заглядывал никто (``Roadmap.md``, «Что осталось»). Текст при этом
    игрок читал при создании персонажа и выбирал по нему расу.
    """
    if not content.has_race(character.race_id):
        return {}
    return content.race(character.race_id).passive.modifiers


def equipment_modifiers(
    content: GameContent, item_ids: Iterable[str], hero_level: int = 0, class_id: str = ""
) -> dict[str, float]:
    """Что даёт надетое: своими числами, своим родом и тем, чего стоит чужое.

    Прибавки к характеристикам лежат на вещи числом (``Item.stat_bonuses``) — их
    даёт редкость, — и попадают сюда теми же ключами ``stat_STR``, которыми
    говорят особенности: словарь один на всех, и складывать его умеет один и тот
    же ``merge``.

    Надетое переживает содержимое так же, как панель: вещь, которой больше нет,
    ничего не даёт и ничего не роняет.
    """
    worn = tuple(item_ids)
    bundles: list[Mapping[str, float]] = []
    for item_id in worn:
        item = gear.worn_item(content, item_id, hero_level)
        if item is None:
            continue
        bundles.append(item.modifiers)
        bundles.append(
            {
                f"{STAT_MODIFIER_PREFIX}{code}": float(value)
                for code, value in item.stat_bonuses.items()
            }
        )
        # Именной аффикс лежит на вещи и виден на карточке любому, кто её поднял,
        # а работает только у своего класса (ADR 0071). Поэтому он складывается
        # здесь, а не в самой вещи: вещь выводится из своего имени и ни от кого
        # не зависит (ADR 0059), а свёрток - это уже про того, кто её надел.
        named = content.class_affix(item.class_affix_id) if item.class_affix_id else None
        if named is not None and named.class_id == class_id:
            bundles.append({named.key: named.value})
    bundles.append(gear.type_modifiers(content, worn))
    return merge(*bundles)


def passive_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Прибавки от каждого изученного пассивного умения.

    Изучено - значит работает: слотов у пассивных умений нет (ADR 0016).

    Ранг пассивного умения виден только здесь: у пассивного нет ни хода, ни
    отката, ни цены, и всё, что ранг ему прибавляет, - размер самой прибавки.
    """
    bundles: list[Mapping[str, float]] = []
    # Изученное переживает содержимое: умения, которого больше нет, здесь просто
    # нет (``Claude.md``, правило 8) - ``known_passives`` отбирает по реестру.
    for skill in skill_rules.known_passives(content, character):
        rank = character.loadout.rank_of(skill.code)
        bundles.append({skill.effect: skill.power_at_rank(rank)})
    return merge(*bundles)


def collect_modifiers(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
) -> dict[str, float]:
    """Все прибавки, действующие на персонажа прямо сейчас."""
    working = repair.working_ids(content, character)
    return merge(
        trait_modifiers(content, character.trait_ids),
        # Вехи характеристик - такие же черты, только выданные порогом, а не
        # выбором при создании (ADR 0068).
        milestone_modifiers(content, character),
        scaling_modifiers(content, character),
        subclass_modifiers(content, character),
        race_modifiers(content, character),
        house_modifiers(content, character),
        passive_modifiers(content, character),
        # Сломанное надетое не даёт ничего: ни прибавок, ни рода, ни цены за
        # чужой род - сточенная до конца вещь считается снятой, пока её не
        # починят (``domain/rules/repair.py``, ADR 0057).
        equipment_modifiers(content, working, character.level, character.class_id),
        # Чужая вещь не запрещена — она дорога, и цена берётся здесь же, вместе
        # со всем остальным, что на персонаже сейчас висит.
        gear.proficiency_penalty(content, character, working),
        effects.modifiers() if effects is not None else {},
    )


def stat_bonuses(modifiers: Mapping[str, float]) -> StatBlock:
    """Вынуть из свёртка плоские прибавки к характеристикам (``stat_STR`` и подобные)."""
    values: dict[str, int] = {}
    for key, value in modifiers.items():
        if not key.startswith(STAT_MODIFIER_PREFIX):
            continue
        code = key.removeprefix(STAT_MODIFIER_PREFIX)
        if code in {stat.value for stat in StatCode}:
            values[code] = values.get(code, 0) + int(value)
    return StatBlock.from_mapping(values)


def percent(modifiers: Mapping[str, float], key: str) -> float:
    """Процентная прибавка как множитель: 12 -> 1.12, -8 -> 0.92."""
    return 1.0 + modifiers.get(key, 0.0) / 100.0


def flat(modifiers: Mapping[str, float], key: str) -> float:
    return modifiers.get(key, 0.0)
