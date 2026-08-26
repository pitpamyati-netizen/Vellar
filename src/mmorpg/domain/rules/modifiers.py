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
from mmorpg.domain.rules import edges as edge_rules
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import skills as skill_rules

STAT_MODIFIER_PREFIX = "stat_"

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
    content: GameContent, item_ids: Iterable[str], hero_level: int = 0
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
    bundles.append(gear.type_modifiers(content, worn))
    return merge(*bundles)


def passive_modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Прибавки от каждого изученного пассивного умения.

    Изучено - значит работает. Раньше их укладывали в три слота из шести, и
    очко, вложенное в седьмое пассивное умение, не делало ровно ничего: игрок
    платил за прибавку, которую игра не считала.

    Грань пассивного умения считается здесь и больше нигде: у пассивного умения
    нет ни хода, ни цели, поэтому всё, что грань может ему сделать, - поднять его
    собственную прибавку и добавить свою. Долго не делалось и этого: половина
    выбранных граней в игре была надписью без последствий.
    """
    bundles: list[Mapping[str, float]] = []
    # Изученное переживает содержимое: умения, которого больше нет, здесь просто
    # нет (``Claude.md``, правило 8) - ``known_passives`` отбирает по реестру.
    for skill in skill_rules.known_passives(content, character):
        rank = character.loadout.rank_of(skill.code)
        edge = skill_rules.chosen_edge(character, skill)
        bundles.append({skill.effect: skill.power_at_rank(rank) * edge_rules.power_factor(edge)})
        if edge is not None and edge.self_modifiers:
            bundles.append(dict(edge.self_modifiers))
    return merge(*bundles)


def collect_modifiers(
    content: GameContent,
    character: Character,
    effects: EffectStack | None = None,
) -> dict[str, float]:
    """Все прибавки, действующие на персонажа прямо сейчас."""
    return merge(
        trait_modifiers(content, character.trait_ids),
        race_modifiers(content, character),
        passive_modifiers(content, character),
        equipment_modifiers(content, character.equipment.item_ids(), character.level),
        # Чужая вещь не запрещена — она дорога, и цена берётся здесь же, вместе
        # со всем остальным, что на персонаже сейчас висит.
        gear.proficiency_penalty(content, character),
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
