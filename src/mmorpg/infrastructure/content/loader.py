"""Чтение ``content/*.toml`` в неизменные объекты домена.

Зовётся ровно один раз, на старте, до того как бот начнёт опрашивать Telegram.
Файлы читаются синхронно нарочно: это происходит раньше, чем цикл событий
начинает что-либо обслуживать. Все беды проверки собираются и называются вместе,
чтобы автор содержимого увидел весь список, а не чинил по одной опечатке за
перезапуск.
"""

from __future__ import annotations

import itertools
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.content import (
    ArmorType,
    CharacterClass,
    City,
    ClassResource,
    DeepDungeon,
    EdgeEffect,
    EquipSlot,
    GameContent,
    GearArchetype,
    GearTier,
    HealthCurve,
    Item,
    ItemEffect,
    ItemKind,
    Location,
    OwnerKind,
    ProgressionRules,
    Race,
    RacePassive,
    Rarity,
    Skill,
    SkillEdge,
    SkillKind,
    SpecialProperty,
    Trait,
    Turning,
    TurningOption,
    WeaponType,
)
from mmorpg.domain.entities.craft import (
    Craft,
    CraftKind,
    CraftRules,
    CraftYield,
    QualityTier,
    Recipe,
    RecipeInput,
)
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.dice import MAX_SPREAD, MIN_SPREAD, Dice
from mmorpg.domain.entities.location import EnemyArchetype, EnemyKind
from mmorpg.domain.entities.quest import ObjectiveKind, Quest
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.rules.equipment import WEAPON_SLOT
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS

CONTENT_FILES = (
    "world.toml",
    "races.toml",
    "classes.toml",
    "traits.toml",
    "skills.toml",
    "items.toml",
    "enemies.toml",
    "quests.toml",
    "crafts.toml",
    "turnings.toml",
)

# Виды узлов, которые может попросить задание на поиск. Держатся строками, а не импортом
# NodeKind, потому что содержимое говорит на языке содержимого.
SEARCHABLE_NODES = frozenset({"gather", "cache", "shrine", "event"})

MINIMUM_CRAFTS = 4
EXPECTED_RACES = 16
EXPECTED_CLASSES = 8

#: Сколько умений у класса. Двадцать боевых на шесть слотов панели - это выбор;
#: сорок пассивных - то, во что уходит очко между боевыми (``docs/skills.md``).
#: Боевых умений на класс: двадцать уровней открытия, и на четырёх из них стоит
#: развилка - два умения на одно место (ADR 0024).
ACTIVES_PER_CLASS = 24
#: Развилок на класс. Столько уровней открытия несут по два умения.
FORKS_PER_CLASS = 4
PASSIVES_PER_CLASS = 20
MINIMUM_TRAITS = 60
EXPECTED_CITIES = 15
LOCATIONS_PER_CITY = 5
RACE_STAT_BUDGET = 3


class ContentError(RuntimeError):
    """Бросается, когда каталог содержимого не прошёл проверку."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        listed = "\n  - ".join(self.problems)
        super().__init__(
            f"content validation failed ({len(self.problems)} problems):\n  - {listed}"
        )


def load_content(content_dir: Path) -> GameContent:
    """Разобрать и проверить весь каталог содержимого.

    Бросает:
        ContentError: если какого-то файла нет, он испорчен или противоречив.
    """
    problems: list[str] = []

    missing = [name for name in CONTENT_FILES if not (content_dir / name).is_file()]
    if missing:
        raise ContentError([f"missing content file: {name}" for name in missing])

    raw = {name: _read_toml(content_dir / name) for name in CONTENT_FILES}

    trait_meta = raw["traits.toml"].get("meta", {})
    modifier_keys = frozenset(trait_meta.get("modifier_keys", ()))
    inverted_modifiers = frozenset(trait_meta.get("lower_is_better", ()))
    categories = {entry["id"]: entry["name"] for entry in trait_meta.get("categories", ())}
    unknown_inverted = sorted(inverted_modifiers - modifier_keys)
    if unknown_inverted:
        problems.append(
            f"traits.toml: [meta].lower_is_better lists unknown keys {unknown_inverted}"
        )

    skill_meta = raw["skills.toml"].get("meta", {})
    active_effects = frozenset(skill_meta.get("active_effects", ()))
    targets = frozenset(skill_meta.get("targets", ()))

    skills = _parse_skills(raw["skills.toml"], active_effects, targets, modifier_keys, problems)
    skills_by_code = {skill.code: skill for skill in skills}

    gear = _parse_items(raw["items.toml"], modifier_keys, skills_by_code, problems)
    written, rarities = gear.items, gear.rarities
    races = _parse_races(raw["races.toml"], skills_by_code, problems)
    classes = _parse_classes(raw["classes.toml"], gear, problems)
    traits = _parse_traits(raw["traits.toml"], modifier_keys, set(categories), problems)
    _validate_skill_weapons(skills, classes, gear, problems)
    cities = _parse_world(raw["world.toml"], problems)
    # Снаряжение собирается из видов, ступеней и редкостей. Имена собранных вещей
    # известны сразу, а сами вещи - только когда реестр готов: справочники родов
    # лежат в нём. Проверкам ссылок хватает имён, поэтому они идут как раньше, а
    # сборка ждёт конца.
    item_ids = {item.id for item in written} | item_procgen.catalogue_ids(
        gear.gear_archetypes, gear.gear_tiers, gear.rarities
    )
    enemies, elite_titles = _parse_enemies(raw["enemies.toml"], item_ids, problems)
    _validate_enemies(enemies, cities, problems)
    quests = _parse_quests(
        raw["quests.toml"], item_ids, cities, {enemy.id for enemy in enemies}, problems
    )
    craft_rules = _build_craft_rules(raw["crafts.toml"], problems)
    crafts, recipes = _parse_crafts(raw["crafts.toml"], item_ids, craft_rules, problems)
    turnings, open_turning_id = _parse_turnings(raw["turnings.toml"], problems)

    rules = _build_rules(raw, problems)

    _validate_races(races, problems)
    _validate_classes(classes, skills, rules, problems)
    _validate_traits(traits, problems)
    _validate_world(cities, rules, problems)
    _validate_crafts(crafts, recipes, problems)
    _validate_quest_rewards(quests, gear, problems)

    parts: dict[str, Any] = {
        "races": races,
        "classes": classes,
        "traits": traits,
        "skills": skills,
        "cities": cities,
        "rarities": rarities,
        "slots": gear.slots,
        "weapon_types": gear.weapon_types,
        "armor_types": gear.armor_types,
        "gear_tiers": gear.gear_tiers,
        "gear_archetypes": gear.gear_archetypes,
        "special_properties": gear.special_properties,
        "enemy_archetypes": enemies,
        "elite_titles": elite_titles,
        "quests": quests,
        "crafts": crafts,
        "recipes": recipes,
        "craft_rules": craft_rules,
        "trait_categories": categories,
        "inverted_modifiers": inverted_modifiers,
        "rules": rules,
        "turnings": turnings,
        "open_turning_id": open_turning_id,
    }

    if problems:
        raise ContentError(problems)

    bare = GameContent.build(items=written, **parts)
    return GameContent.build(items=(*written, *item_procgen.catalogue(bare)), **parts)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:  # pragma: no cover - зависит от испорченного ввода
        raise ContentError([f"{path.name}: cannot parse TOML: {error}"]) from error


def _build_rules(raw: Mapping[str, Mapping[str, Any]], problems: list[str]) -> ProgressionRules:
    world_meta = raw["world.toml"].get("meta", {})
    class_meta = raw["classes.toml"].get("meta", {})
    trait_meta = raw["traits.toml"].get("meta", {})
    skill_meta = raw["skills.toml"].get("meta", {})

    rules = ProgressionRules(
        max_character_level=int(world_meta.get("max_character_level", 300)),
        base_stat_value=int(class_meta.get("base_stat_value", 5)),
        free_points_at_creation=int(class_meta.get("free_points_at_creation", 5)),
        stat_points_per_level=int(class_meta.get("stat_points_per_level", 2)),
        active_unlock_levels=tuple(class_meta.get("active_unlock_levels", ())),
        passive_unlock_levels=tuple(class_meta.get("passive_unlock_levels", ())),
        fork_levels=tuple(class_meta.get("fork_levels", ())),
        active_slots=int(class_meta.get("active_slots", 6)),
        racial_slots=int(class_meta.get("racial_slots", 1)),
        traits_at_creation=int(trait_meta.get("picks_at_creation", 2)),
        max_rank=int(skill_meta.get("max_rank", 5)),
        edge_rank=int(skill_meta.get("edge_rank", 3)),
        skill_point_per_level=int(skill_meta.get("skill_point_per_level", 1)),
        rank_costs=tuple(int(value) for value in skill_meta.get("rank_costs", (1, 2, 2, 3, 4))),
        branch_gates=tuple(int(value) for value in skill_meta.get("branch_gates", (0, 20, 50, 90))),
        branch_tier_levels=tuple(
            int(value) for value in skill_meta.get("branch_tier_levels", (1, 61, 154, 227))
        ),
    )
    if len(rules.rank_costs) != rules.max_rank:
        problems.append(
            f"skills.toml: [meta].rank_costs must price all {rules.max_rank} ranks, "
            f"got {len(rules.rank_costs)}"
        )
    if len(rules.branch_gates) != len(rules.branch_tier_levels):
        problems.append(
            "skills.toml: [meta].branch_gates and [meta].branch_tier_levels "
            "must have the same length - one number per tier"
        )
    if rules.branch_gates and rules.branch_gates[0] != 0:
        problems.append("skills.toml: [meta].branch_gates must open the first tier at 0")
    if list(rules.branch_gates) != sorted(rules.branch_gates):
        problems.append("skills.toml: [meta].branch_gates must not fall as tiers rise")
    unlock_count = ACTIVES_PER_CLASS - FORKS_PER_CLASS
    if len(rules.active_unlock_levels) != unlock_count:
        problems.append(
            f"classes.toml: [meta].active_unlock_levels must list {unlock_count} levels"
        )
    if len(rules.fork_levels) != FORKS_PER_CLASS:
        problems.append(f"classes.toml: [meta].fork_levels must list {FORKS_PER_CLASS} levels")
    stray = [level for level in rules.fork_levels if level not in rules.active_unlock_levels]
    if stray:
        problems.append(
            f"classes.toml: [meta].fork_levels names levels {stray} that no active unlock stands on"
        )
    if len(rules.passive_unlock_levels) != PASSIVES_PER_CLASS:
        problems.append(
            f"classes.toml: [meta].passive_unlock_levels must list {PASSIVES_PER_CLASS} levels"
        )
    if rules.active_slots != 6 or rules.racial_slots != 1:
        problems.append("classes.toml: the panel is fixed at 6 active and 1 racial slot")
    return rules


# --- skills ----------------------------------------------------------


def _parse_skills(
    raw: Mapping[str, Any],
    active_effects: frozenset[str],
    targets: frozenset[str],
    modifier_keys: frozenset[str],
    problems: list[str],
) -> tuple[Skill, ...]:
    default_step = float(raw.get("meta", {}).get("default_rank_step", 0.15))
    parsed: list[Skill] = []
    seen: set[str] = set()

    for entry in raw.get("skill", ()):
        code = str(entry.get("code", ""))
        if not code:
            problems.append("skills.toml: a skill has no code")
            continue
        if code in seen:
            problems.append(f"skills.toml: duplicate skill code {code}")
            continue
        seen.add(code)

        owner = str(entry.get("owner", ""))
        if ":" not in owner:
            problems.append(f"skills.toml: {code} has malformed owner {owner!r}")
            continue
        owner_kind_raw, owner_id = owner.split(":", 1)
        if owner_kind_raw not in {kind.value for kind in OwnerKind}:
            problems.append(f"skills.toml: {code} has unknown owner kind {owner_kind_raw!r}")
            continue

        kind_raw = str(entry.get("kind", ""))
        if kind_raw not in {kind.value for kind in SkillKind}:
            problems.append(f"skills.toml: {code} has unknown kind {kind_raw!r}")
            continue
        kind = SkillKind(kind_raw)

        effect = str(entry.get("effect", ""))
        if kind is SkillKind.ACTIVE and effect not in active_effects:
            problems.append(
                f"skills.toml: {code} uses effect {effect!r}, "
                "which is not listed in [meta].active_effects"
            )
        if kind is SkillKind.PASSIVE and effect not in modifier_keys:
            problems.append(
                f"skills.toml: passive {code} uses modifier {effect!r}, "
                "which is not listed in traits.toml [meta].modifier_keys"
            )

        target = str(entry.get("target", "self"))
        if kind is SkillKind.ACTIVE and target not in targets:
            problems.append(f"skills.toml: {code} has unknown target {target!r}")

        scaling_raw = entry.get("scaling")
        scaling: StatCode | None = None
        if scaling_raw is not None:
            if scaling_raw in {code_.value for code_ in StatCode}:
                scaling = StatCode(scaling_raw)
            else:
                problems.append(f"skills.toml: {code} scales with unknown stat {scaling_raw!r}")

        tag_raw = entry.get("tag")
        tag: ActionTag | None = None
        if tag_raw is not None:
            if tag_raw in {value.value for value in ActionTag}:
                tag = ActionTag(tag_raw)
            else:
                problems.append(f"skills.toml: {code} declares unknown tag {tag_raw!r}")

        raw_edges = entry.get("edges", ())
        if len(raw_edges) != 2:
            problems.append(f"skills.toml: {code} must declare exactly 2 edges")
            continue
        edges = tuple(
            SkillEdge(
                code=f"{code}_{letter}",
                name=str(raw["name"]),
                text=str(raw["text"]),
                effect=_edge_effect(code, letter, raw, modifier_keys, problems),
            )
            for letter, raw in zip("ab", raw_edges, strict=True)
        )

        parsed.append(
            Skill(
                code=code,
                fork=str(entry.get("fork", "")),
                name=str(entry["name"]),
                owner_kind=OwnerKind(owner_kind_raw),
                owner_id=owner_id,
                kind=kind,
                level=int(entry.get("level", 1)),
                text=str(entry.get("text", "")),
                effect=effect,
                power=float(entry.get("power", 0)),
                edges=(edges[0], edges[1]),
                cost=int(entry.get("cost", 0)),
                cooldown=int(entry.get("cooldown", 0)),
                target=target,
                scaling=scaling,
                rank_step=float(entry.get("rank_step", default_step)),
                tag=tag,
                weapon_types=tuple(str(value) for value in entry.get("weapons", ())),
                dice=_skill_dice(code, entry, problems),
            )
        )
    return tuple(parsed)


def _skill_dice(code: str, entry: Mapping[str, Any], problems: list[str]) -> Dice | None:
    """Свои кости умения. Пусто - умение целиком стоит на броске оружия."""
    raw = entry.get("dice")
    if raw is None:
        return None
    try:
        return Dice.parse(str(raw))
    except ValueError as error:
        problems.append(f"skills.toml: {code}: {error}")
        return None


# --- races -----------------------------------------------------------


#: Как грань называет свою механику в ``skills.toml``. Ключи - поля
#: ``EdgeEffect``; незнакомый ключ это отказ, а не молчание, потому что опечатка
#: в ключе означала бы грань, которая опять ничего не делает.
_EDGE_KEYS = frozenset(
    {
        "name",
        "text",
        "power",
        "cost",
        "cooldown",
        "duration",
        "dot_turns",
        "stun_turns",
        "hits",
        "hit_power",
        "splash",
        "aoe",
        "pierce",
        "crit",
        "lifesteal",
        "cleanse",
        "heal",
        "barrier",
        "self_modifiers",
        "target_modifiers",
    }
)


def _edge_effect(
    code: str,
    letter: str,
    raw: Mapping[str, Any],
    modifier_keys: frozenset[str],
    problems: list[str],
) -> EdgeEffect:
    """Механика грани из содержимого.

    Грань обязана что-то делать: пустое объявление - ровно та поломка, ради
    которой словарь и заведён, обещание словами без единого числа за ним.
    """
    where = f"skills.toml: {code} edge {letter}"
    unknown = sorted(set(raw) - _EDGE_KEYS)
    if unknown:
        problems.append(f"{where} declares unknown keys {unknown}")

    effect = EdgeEffect(
        power=float(raw.get("power", 0)),
        cost=float(raw.get("cost", 0)),
        cooldown=int(raw.get("cooldown", 0)),
        duration=int(raw.get("duration", 0)),
        dot_turns=int(raw.get("dot_turns", 0)),
        stun_turns=int(raw.get("stun_turns", 0)),
        hits=int(raw.get("hits", 0)),
        hit_power=float(raw.get("hit_power", 100)),
        splash=float(raw.get("splash", 0)),
        aoe=bool(raw.get("aoe", False)),
        pierce=float(raw.get("pierce", 0)),
        crit=float(raw.get("crit", 0)),
        lifesteal=float(raw.get("lifesteal", 0)),
        cleanse=int(raw.get("cleanse", 0)),
        heal=float(raw.get("heal", 0)),
        barrier=float(raw.get("barrier", 0)),
        self_modifiers=_edge_modifiers(where, raw.get("self_modifiers"), modifier_keys, problems),
        target_modifiers=_edge_modifiers(
            where, raw.get("target_modifiers"), modifier_keys, problems
        ),
    )
    if effect.empty:
        problems.append(f"{where} changes nothing: a named edge with no mechanics is the promise")
    return effect


def _edge_modifiers(
    where: str,
    declared: Mapping[str, Any] | None,
    modifier_keys: frozenset[str],
    problems: list[str],
) -> Mapping[str, float]:
    """Модификаторы грани. Словарь тот же, что у особенностей и снаряжения."""
    if not declared:
        return MappingProxyType({})
    strange = sorted(set(declared) - modifier_keys)
    if strange:
        problems.append(f"{where} names unknown modifiers {strange}")
    return MappingProxyType(
        {name: float(value) for name, value in declared.items() if name in modifier_keys}
    )


def _parse_races(
    raw: Mapping[str, Any],
    skills_by_code: Mapping[str, Skill],
    problems: list[str],
) -> tuple[Race, ...]:
    parsed: list[Race] = []
    for entry in raw.get("race", ()):
        race_id = str(entry.get("id", ""))
        try:
            bonuses = StatBlock.from_mapping(entry.get("bonuses", {}))
        except KeyError as error:
            problems.append(f"races.toml: {race_id}: {error}")
            continue

        active_code = str(entry.get("active", ""))
        active = skills_by_code.get(active_code)
        if active is None:
            problems.append(f"races.toml: {race_id} points at unknown active skill {active_code!r}")
        elif active.kind is not SkillKind.ACTIVE or active.owner != f"race:{race_id}":
            problems.append(
                f"races.toml: {race_id} active {active_code!r} must be an active skill "
                f'owned by "race:{race_id}"'
            )

        passive_raw = entry.get("passive", {})
        passive_id = str(passive_raw.get("id", ""))
        # Прибавки расовой способности проверяются по тому, что движок считает,
        # а не по словарю: словарь шире нарочно (``Claude.md``, правило 7).
        passive_modifiers: dict[str, float] = {}
        for key, value in dict(passive_raw.get("modifiers", {})).items():
            if key not in EFFECTIVE_KEYS:
                problems.append(
                    f"races.toml: {race_id} passive {passive_id!r} promises {key!r}, "
                    "and nothing in the engine counts it"
                )
                continue
            passive_modifiers[str(key)] = float(value)
        if not passive_modifiers:
            problems.append(
                f"races.toml: {race_id} passive {passive_id!r} does nothing: "
                "a named ability with no modifiers is a promise, not an ability"
            )
        parsed.append(
            Race(
                id=race_id,
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                bonuses=bonuses,
                passive=RacePassive(
                    id=passive_id,
                    name=str(passive_raw.get("name", "")),
                    text=str(passive_raw.get("text", "")),
                    modifiers=passive_modifiers,
                ),
                active_code=active_code,
            )
        )
    return tuple(parsed)


def _validate_races(races: Sequence[Race], problems: list[str]) -> None:
    if len(races) != EXPECTED_RACES:
        problems.append(f"races.toml: expected {EXPECTED_RACES} races, found {len(races)}")
    _check_unique((race.id for race in races), "races.toml", problems)

    for race in races:
        allowance = RACE_STAT_BUDGET + race.bonuses.penalty_total
        if race.bonuses.positive_total > allowance:
            problems.append(
                f"races.toml: {race.id} spends {race.bonuses.positive_total} positive points "
                f"but its budget is {allowance}"
            )
        if race.bonuses.total > RACE_STAT_BUDGET:
            problems.append(
                f"races.toml: {race.id} has a net bonus of {race.bonuses.total}, "
                f"the cap is {RACE_STAT_BUDGET}"
            )
        if not race.passive.name:
            problems.append(f"races.toml: {race.id} has no passive ability")


# --- классы ---------------------------------------------------------


def _parse_classes(
    raw: Mapping[str, Any], gear: ItemContent, problems: list[str]
) -> tuple[CharacterClass, ...]:
    weapon_type_ids = {kind.id for kind in gear.weapon_types}
    armor_type_ids = {kind.id for kind in gear.armor_types}
    parsed: list[CharacterClass] = []
    for entry in raw.get("class", ()):
        class_id = str(entry.get("id", ""))
        weapon_types = tuple(str(value) for value in entry.get("weapons", ()))
        armor_types = tuple(str(value) for value in entry.get("armor", ()))
        # Класс, который не умеет носить ничего, - это класс, который дерётся
        # голыми руками и в рубахе. Такого в игре нет, и молчаливой опечаткой он
        # тоже быть не должен.
        if not weapon_types:
            problems.append(f"classes.toml: {class_id} names no weapons it can wield")
        if not armor_types:
            problems.append(f"classes.toml: {class_id} names no armour it can wear")
        for type_id in weapon_types:
            if type_id not in weapon_type_ids:
                problems.append(f"classes.toml: {class_id} wields unknown weapon {type_id!r}")
        for type_id in armor_types:
            if type_id not in armor_type_ids:
                problems.append(f"classes.toml: {class_id} wears unknown armour {type_id!r}")
        try:
            bonuses = StatBlock.from_mapping(entry.get("bonuses", {}))
            key_stats = tuple(StatCode(code) for code in entry.get("key_stats", ()))
            resource_raw = entry["resource"]
            resource = ClassResource(
                id=str(resource_raw["id"]),
                name=str(resource_raw["name"]),
                base=float(resource_raw["base"]),
                per_level=float(resource_raw["per_level"]),
                stat=StatCode(resource_raw["stat"]),
                per_stat=float(resource_raw["per_stat"]),
                regen_per_turn=float(resource_raw["regen_per_turn"]),
            )
            health_raw = entry["health"]
            health = HealthCurve(
                base=float(health_raw["base"]),
                per_level=float(health_raw["per_level"]),
                per_endurance=float(health_raw["per_endurance"]),
            )
        except (KeyError, ValueError) as error:
            problems.append(f"classes.toml: {class_id}: {error}")
            continue

        parsed.append(
            CharacterClass(
                id=class_id,
                name=str(entry["name"]),
                role=str(entry.get("role", "")),
                description=str(entry.get("description", "")),
                power=str(entry.get("power", "")),
                key_stats=key_stats,
                bonuses=bonuses,
                resource=resource,
                health=health,
                weapon_types=weapon_types,
                armor_types=armor_types,
            )
        )
    return tuple(parsed)


def _validate_skill_weapons(
    skills: Sequence[Skill],
    classes: Sequence[CharacterClass],
    gear: ItemContent,
    problems: list[str],
) -> None:
    """Умение, которое просит оружие, должно просить оружие своего класса.

    Иначе выходит кнопка, которая не сработает никогда: разбойник не возьмёт
    двуручник, и удар в спину, попросивший двуручник, - это шесть слотов на
    пятерых. Проверка дешёвая, а ошибка тихая, поэтому она здесь.
    """
    known = {kind.id for kind in gear.weapon_types}
    by_id = {klass.id: klass for klass in classes}
    for skill in skills:
        for type_id in skill.weapon_types:
            if type_id not in known:
                problems.append(f"skills.toml: {skill.code} asks for unknown weapon {type_id!r}")
        if skill.owner_kind is not OwnerKind.CLASS or not skill.weapon_types:
            continue
        klass = by_id.get(skill.owner_id)
        if klass is None:
            continue
        stray = sorted(set(skill.weapon_types) - set(klass.weapon_types))
        if stray and klass.weapon_types:
            problems.append(
                f"skills.toml: {skill.code} asks for {stray}, which {klass.id} never wields"
            )


#: Сколько заданий подряд должна вести цепочка, чтобы за её конец давали
#: реликтовую вещь. Реликтовое растёт вместе с героем и не устаревает никогда:
#: платить за него надо либо логовом, либо длинной дорогой, и никак иначе.
RELIC_CHAIN_LENGTH = 4


def _validate_quest_rewards(
    quests: Sequence[Quest],
    gear: ItemContent,
    problems: list[str],
) -> None:
    """Реликтовое даётся только за конец длинной цепочки — или не даётся вовсе."""
    relics = {rarity.id for rarity in gear.rarities if rarity.scaling}
    if not relics:
        return
    by_id = {quest.id: quest for quest in quests}
    followed = {quest.follows for quest in quests if quest.follows}

    for quest in quests:
        parsed = item_procgen.parse_gear_id(quest.reward_item)
        if parsed is None or parsed[2] not in relics:
            continue
        if quest.id in followed:
            problems.append(
                f"quests.toml: {quest.id} pays with a relic but is not the end of its chain"
            )
        length, walked = 1, quest
        seen = {quest.id}
        while walked.follows and walked.follows in by_id and walked.follows not in seen:
            seen.add(walked.follows)
            walked = by_id[walked.follows]
            length += 1
        if length < RELIC_CHAIN_LENGTH:
            problems.append(
                f"quests.toml: {quest.id} pays with a relic after {length} contracts; "
                f"a relic is worth {RELIC_CHAIN_LENGTH}"
            )


def _validate_classes(
    classes: Sequence[CharacterClass],
    skills: Sequence[Skill],
    rules: ProgressionRules,
    problems: list[str],
) -> None:
    if len(classes) != EXPECTED_CLASSES:
        problems.append(f"classes.toml: expected {EXPECTED_CLASSES} classes, found {len(classes)}")
    _check_unique((klass.id for klass in classes), "classes.toml", problems)

    for klass in classes:
        # Без этой строки экран характеристик способен сказать только «ключевая
        # характеристика: интеллект» - и игрок вправе спросить, при чём тут она.
        if not klass.power:
            problems.append(f"classes.toml: {klass.id} has no power line")
        owner = f"{OwnerKind.CLASS.value}:{klass.id}"
        owned = [skill for skill in skills if skill.owner == owner]
        actives = sorted(
            (skill for skill in owned if skill.kind is SkillKind.ACTIVE), key=lambda s: s.level
        )
        passives = sorted(
            (skill for skill in owned if skill.kind is SkillKind.PASSIVE), key=lambda s: s.level
        )
        if len(actives) != ACTIVES_PER_CLASS:
            problems.append(
                f"skills.toml: class {klass.id} has {len(actives)} actives, "
                f"expected {ACTIVES_PER_CLASS}"
            )
        if len(passives) != PASSIVES_PER_CLASS:
            problems.append(
                f"skills.toml: class {klass.id} has {len(passives)} passives, "
                f"expected {PASSIVES_PER_CLASS}"
            )
        active_levels = tuple(skill.level for skill in actives)
        expected_actives = tuple(sorted((*rules.active_unlock_levels, *rules.fork_levels)))
        if len(actives) == ACTIVES_PER_CLASS and active_levels != expected_actives:
            problems.append(
                f"skills.toml: class {klass.id} unlocks actives at {active_levels}, "
                f"expected {expected_actives}"
            )
        passive_levels = tuple(skill.level for skill in passives)
        if len(passives) == PASSIVES_PER_CLASS and passive_levels != rules.passive_unlock_levels:
            problems.append(
                f"skills.toml: class {klass.id} unlocks passives at {passive_levels}, "
                f"expected {rules.passive_unlock_levels}"
            )
        _check_branches(klass.id, (*actives, *passives), rules, problems)
        _check_forks(klass.id, actives, rules, problems)


def _check_branches(
    class_id: str,
    owned: Sequence[Skill],
    rules: ProgressionRules,
    problems: list[str],
) -> None:
    """Ветвь названа у каждого умения, и каждая ступень достижима.

    Ступень, гейт которой дороже всего, что лежит в ветви ниже неё, - это кнопка,
    которая не нажмётся никогда (``Claude.md``, правило 9). Проверяется здесь, а
    не в бою: содержимое обязано быть проходимым до того, как в него сыграют.
    """
    nameless = [skill.code for skill in owned if skill.branch is None]
    if nameless:
        problems.append(
            f"skills.toml: class {class_id} leaves {len(nameless)} skills without a branch, "
            f"first {nameless[0]}"
        )
        return

    full = rules.full_rank_cost()
    for branch in ActionTag:
        in_branch = [skill for skill in owned if skill.branch is branch]
        if not in_branch:
            problems.append(f"skills.toml: class {class_id} has no skills in branch {branch.value}")
            continue
        if not any(rules.tier_of_level(skill.level) == 1 for skill in in_branch):
            problems.append(
                f"skills.toml: class {class_id} branch {branch.value} opens above tier 1 "
                "and can never be entered"
            )
        for tier in range(2, len(rules.branch_gates) + 1):
            if not any(rules.tier_of_level(skill.level) == tier for skill in in_branch):
                continue
            # Развилка даёт очкам одно место, а не два: считаем её один раз.
            below = {
                skill.fork or skill.code
                for skill in in_branch
                if rules.tier_of_level(skill.level) < tier
            }
            if len(below) * full < rules.gate_for_tier(tier):
                problems.append(
                    f"skills.toml: class {class_id} branch {branch.value} gates tier {tier} "
                    f"behind {rules.gate_for_tier(tier)} points, but only "
                    f"{len(below) * full} can be spent below it"
                )


def _check_forks(
    class_id: str,
    actives: Sequence[Skill],
    rules: ProgressionRules,
    problems: list[str],
) -> None:
    """Развилка - ровно два умения, один уровень, одна ветвь.

    Развилка из одного умения - это обычное умение с лишним словом на экране;
    развилка из трёх - панель, которая не влезает в сообщение.
    """
    groups: dict[str, list[Skill]] = {}
    for skill in actives:
        if skill.fork:
            groups.setdefault(skill.fork, []).append(skill)
        elif skill.level in rules.fork_levels:
            problems.append(
                f"skills.toml: {skill.code} stands on fork level {skill.level} "
                "without declaring a fork"
            )
    if len(groups) != FORKS_PER_CLASS:
        problems.append(
            f"skills.toml: class {class_id} declares {len(groups)} forks, "
            f"expected {FORKS_PER_CLASS}"
        )
    for fork, members in sorted(groups.items()):
        if len(members) != 2:
            problems.append(f"skills.toml: fork {fork} holds {len(members)} skills, expected 2")
            continue
        first, second = members
        if first.level != second.level:
            problems.append(f"skills.toml: fork {fork} spans levels {first.level}/{second.level}")
        elif first.level not in rules.fork_levels:
            problems.append(
                f"skills.toml: fork {fork} stands on level {first.level}, "
                f"which is not one of {rules.fork_levels}"
            )
        if first.branch is not second.branch:
            problems.append(f"skills.toml: fork {fork} spans two branches")


# --- traits ----------------------------------------------------------


def _parse_traits(
    raw: Mapping[str, Any],
    modifier_keys: frozenset[str],
    categories: set[str],
    problems: list[str],
) -> tuple[Trait, ...]:
    parsed: list[Trait] = []
    for entry in raw.get("trait", ()):
        trait_id = str(entry.get("id", ""))
        category = str(entry.get("category", ""))
        if category not in categories:
            problems.append(f"traits.toml: {trait_id} has unknown category {category!r}")

        modifiers = {str(key): float(value) for key, value in entry.get("modifiers", {}).items()}
        unknown = sorted(set(modifiers) - modifier_keys)
        if unknown:
            problems.append(f"traits.toml: {trait_id} uses unknown modifiers {unknown}")
        if not modifiers:
            problems.append(f"traits.toml: {trait_id} has no modifiers")

        parsed.append(
            Trait(
                id=trait_id,
                name=str(entry["name"]),
                category=category,
                tags=tuple(str(tag) for tag in entry.get("tags", ())),
                modifiers=modifiers,
                text=str(entry.get("text", "")),
            )
        )
    return tuple(parsed)


def _validate_traits(traits: Sequence[Trait], problems: list[str]) -> None:
    if len(traits) < MINIMUM_TRAITS:
        problems.append(
            f"traits.toml: expected at least {MINIMUM_TRAITS} traits, found {len(traits)}"
        )
    _check_unique((trait.id for trait in traits), "traits.toml", problems)
    _check_unique((trait.name for trait in traits), "traits.toml (names)", problems)


# --- items -----------------------------------------------------------


class ItemContent(NamedTuple):
    """Всё, что читается из ``items.toml``: вещи и справочники, которыми они себя называют."""

    items: tuple[Item, ...]
    rarities: tuple[Rarity, ...]
    slots: tuple[EquipSlot, ...]
    weapon_types: tuple[WeaponType, ...]
    armor_types: tuple[ArmorType, ...]
    gear_tiers: tuple[GearTier, ...]
    gear_archetypes: tuple[GearArchetype, ...]
    special_properties: tuple[SpecialProperty, ...]


#: Рода существительных, в которых объявляются прилагательные ступеней.
GENDERS = ("m", "f", "n", "p")


def _weapon_damage_type(type_id: str, entry: Mapping[str, Any], problems: list[str]) -> DamageType:
    """Чем этот род оружия бьёт. Не объявлено - рубящий: так бьёт большинство."""
    raw = str(entry.get("damage_type", "")).strip()
    if not raw:
        problems.append(f"items.toml: weapon type {type_id} does not say what damage it deals")
        return DamageType.SLASHING
    if raw not in {one.value for one in DamageType}:
        problems.append(f"items.toml: weapon type {type_id} deals unknown damage {raw!r}")
        return DamageType.SLASHING
    return DamageType(raw)


def _type_modifiers(
    where: str,
    entry: Mapping[str, Any],
    modifier_keys: frozenset[str],
    problems: list[str],
) -> dict[str, float]:
    """Прибавки рода оружия или доспеха - тем же словарём, что у вещи и особенности."""
    bundle = {str(key): float(value) for key, value in entry.get("modifiers", {}).items()}
    unknown = sorted(set(bundle) - modifier_keys)
    if unknown:
        problems.append(f"items.toml: {where} uses unknown modifiers {unknown}")
    return bundle


def _parse_rarities(meta: Mapping[str, Any], problems: list[str]) -> tuple[Rarity, ...]:
    parsed: list[Rarity] = []
    for entry in meta.get("rarities", ()):
        rarity = Rarity(
            id=str(entry["id"]),
            name=str(entry["name"]),
            weight=int(entry["weight"]),
            price_factor=float(entry["price_factor"]),
            stats=int(entry.get("stats", 0)),
            special=bool(entry.get("special", False)),
            scaling=bool(entry.get("scaling", False)),
            mark=str(entry.get("mark", "")),
        )
        # Две вещи одного вида и разной редкости должны называться по-разному:
        # кнопка в списке - это её текст, и две одинаковые кнопки на экране
        # неразличимы на слух (правила доступности 6).
        if not rarity.mark and rarity.id != "common":
            problems.append(f"items.toml: rarity {rarity.id} has no mark to tell its things apart")
        parsed.append(rarity)
    return tuple(parsed)


def _parse_tiers(meta: Mapping[str, Any], problems: list[str]) -> tuple[GearTier, ...]:
    parsed: list[GearTier] = []
    for entry in meta.get("tiers", ()):
        level = int(entry.get("level", 0))
        names = {gender: str(entry[gender]) for gender in GENDERS if gender in entry}
        missing = sorted(set(GENDERS) - set(names))
        if missing:
            problems.append(f"items.toml: tier {level} has no adjective for {missing}")
        parsed.append(GearTier(level=level, names=names))
    if not parsed:
        problems.append("items.toml: no gear tiers declared")
    elif parsed[0].level != 1:
        problems.append("items.toml: the first gear tier must start at level 1")
    levels = [tier.level for tier in parsed]
    if levels != sorted(set(levels)):
        problems.append("items.toml: gear tiers must climb, and no two may share a level")
    return tuple(parsed)


def _parse_gear(
    raw: Mapping[str, Any],
    slot_ids: set[str],
    armor_slots: set[str],
    weapon_type_ids: set[str],
    armor_type_ids: set[str],
    problems: list[str],
) -> tuple[GearArchetype, ...]:
    parsed: list[GearArchetype] = []
    for entry in raw.get("gear", ()):
        gear_id = str(entry.get("id", ""))
        slot = str(entry.get("slot", ""))
        weapon_type = str(entry.get("weapon_type", ""))
        armor_type = str(entry.get("armor_type", ""))
        gender = str(entry.get("gender", ""))

        if slot not in slot_ids or slot == "none":
            problems.append(f"items.toml: gear {gear_id} has unknown slot {slot!r}")
        if gender not in GENDERS:
            problems.append(f"items.toml: gear {gear_id} has unknown gender {gender!r}")
        if slot == WEAPON_SLOT and weapon_type not in weapon_type_ids:
            problems.append(f"items.toml: weapon {gear_id} has unknown weapon_type {weapon_type!r}")
        if slot in armor_slots and armor_type not in armor_type_ids:
            problems.append(f"items.toml: armour {gear_id} has unknown armor_type {armor_type!r}")
        if weapon_type and slot != WEAPON_SLOT:
            problems.append(f"items.toml: gear {gear_id} is not a weapon but names a weapon_type")
        if armor_type and slot not in armor_slots:
            problems.append(f"items.toml: gear {gear_id} is not armour but names an armor_type")

        parsed.append(
            GearArchetype(
                id=gear_id,
                noun=str(entry.get("noun", "")),
                gender=gender if gender in GENDERS else "m",
                slot=slot,
                weapon_type=weapon_type,
                armor_type=armor_type,
            )
        )
    _check_unique((gear.id for gear in parsed), "items.toml (gear)", problems)
    return tuple(parsed)


def _parse_items(
    raw: Mapping[str, Any],
    modifier_keys: frozenset[str],
    skills_by_code: Mapping[str, Skill],
    problems: list[str],
) -> ItemContent:
    meta = raw.get("meta", {})
    rarities = _parse_rarities(meta, problems)
    rarity_ids = {rarity.id for rarity in rarities}
    tiers = _parse_tiers(meta, problems)

    slots = tuple(
        EquipSlot(
            id=str(entry["id"]),
            name=str(entry["name"]),
            armor_share=float(entry.get("armor_share", 0.0)),
        )
        for entry in meta.get("slots", ())
    )
    slot_ids = {slot.id for slot in slots} | {"none"}
    armor_slots = {slot.id for slot in slots if slot.armor_share > 0}

    weapon_types: list[WeaponType] = []
    for entry in meta.get("weapon_types", ()):
        type_id = str(entry.get("id", ""))
        try:
            dice = Dice.parse(str(entry.get("dice", "")))
        except ValueError as error:
            problems.append(f"items.toml: weapon type {type_id}: {error}")
            continue
        gender = str(entry.get("gender", "m"))
        if gender not in GENDERS:
            problems.append(f"items.toml: weapon type {type_id} has unknown gender {gender!r}")
        # Размах читается как объявлен, но выше потолка не пускается: «в полтора
        # раза» - это правило игры, а не пожелание содержимому. Объявленное сверх
        # него - ошибка автора, и она называется вслух, а не зажимается молча.
        spread = float(entry.get("spread", MAX_SPREAD))
        if not MIN_SPREAD <= spread <= MAX_SPREAD:
            problems.append(
                f"items.toml: weapon type {type_id} has spread {spread}, "
                f"allowed is {MIN_SPREAD}..{MAX_SPREAD}"
            )
        weapon_types.append(
            WeaponType(
                id=type_id,
                name=str(entry["name"]),
                dice=dice,
                spread=min(MAX_SPREAD, max(MIN_SPREAD, spread)),
                gender=gender if gender in GENDERS else "m",
                damage_type=_weapon_damage_type(type_id, entry, problems),
                modifiers=_type_modifiers(f"weapon type {type_id}", entry, modifier_keys, problems),
            )
        )
    armor_types = tuple(
        ArmorType(
            id=str(entry["id"]),
            name=str(entry["name"]),
            armor=float(entry.get("armor", 1.0)),
            modifiers=_type_modifiers(
                f"armor type {entry.get('id', '')}", entry, modifier_keys, problems
            ),
        )
        for entry in meta.get("armor_types", ())
    )
    weapon_type_ids = {kind.id for kind in weapon_types}
    armor_type_ids = {kind.id for kind in armor_types}

    special_properties: list[SpecialProperty] = []
    for entry in meta.get("special_properties", ()):
        key = str(entry.get("key", ""))
        if key not in modifier_keys:
            problems.append(f"items.toml: special property {key!r} is not a known modifier")
        special_properties.append(SpecialProperty(key=key, value=float(entry.get("value", 0))))

    gear_archetypes = _parse_gear(
        raw, slot_ids, armor_slots, weapon_type_ids, armor_type_ids, problems
    )

    parsed: list[Item] = []
    for entry in raw.get("item", ()):
        item_id = str(entry.get("id", ""))
        kind_raw = str(entry.get("kind", ""))
        if kind_raw not in {kind.value for kind in ItemKind}:
            problems.append(f"items.toml: {item_id} has unknown kind {kind_raw!r}")
            continue
        # Снаряжение руками больше не пишут: оно собирается из вида, ступени и
        # редкости, иначе одна написанная вещь молча не имела бы ни урона, ни
        # брони, ни характеристик.
        if kind_raw == ItemKind.EQUIPMENT.value:
            problems.append(
                f"items.toml: {item_id} is equipment written by hand; "
                "declare a [[gear]] kind instead"
            )
        rarity = str(entry.get("rarity", ""))
        if rarity not in rarity_ids:
            problems.append(f"items.toml: {item_id} has unknown rarity {rarity!r}")
        slot = str(entry.get("slot", "none"))
        if slot not in slot_ids:
            problems.append(f"items.toml: {item_id} has unknown slot {slot!r}")
        if "text" in entry:
            problems.append(
                f"items.toml: {item_id} has a text field; items are generated in their "
                "thousands and describe themselves by name, kind and numbers"
            )

        modifiers = {str(key): float(value) for key, value in entry.get("modifiers", {}).items()}
        unknown = sorted(set(modifiers) - modifier_keys)
        if unknown:
            problems.append(f"items.toml: {item_id} uses unknown modifiers {unknown}")

        skill_modifiers = {
            str(key): float(value) for key, value in entry.get("skill_modifiers", {}).items()
        }
        for skill_code in skill_modifiers:
            if skill_code not in skills_by_code:
                problems.append(f"items.toml: {item_id} modifies unknown skill {skill_code!r}")

        effect_raw = entry.get("effect")
        effect = (
            ItemEffect(
                kind=str(effect_raw["kind"]),
                power=float(effect_raw["power"]),
                turns=int(effect_raw.get("turns", 0)),
            )
            if effect_raw is not None
            else None
        )
        if kind_raw == ItemKind.CONSUMABLE.value and effect is None:
            problems.append(f"items.toml: consumable {item_id} has no effect")

        parsed.append(
            Item(
                id=item_id,
                name=str(entry["name"]),
                kind=ItemKind(kind_raw),
                slot=slot,
                rarity=rarity,
                level=int(entry.get("level", 1)),
                price=int(entry.get("price", 0)),
                modifiers=modifiers,
                skill_modifiers=skill_modifiers,
                stack=int(entry.get("stack", 1)),
                source=str(entry.get("source", "")),
                effect=effect,
            )
        )
    _check_unique((item.id for item in parsed), "items.toml", problems)
    return ItemContent(
        tuple(parsed),
        rarities,
        slots,
        tuple(weapon_types),
        armor_types,
        tiers,
        gear_archetypes,
        tuple(special_properties),
    )


# --- противники -----------------------------------------------------


def _parse_enemies(
    raw: Mapping[str, Any],
    item_ids: set[str],
    problems: list[str],
) -> tuple[tuple[EnemyArchetype, ...], tuple[str, ...]]:
    meta = raw.get("meta", {})
    elite_titles = tuple(str(title) for title in meta.get("elite_titles", ()))
    known_kinds = {kind.value for kind in EnemyKind}
    known_elements = {one.value for one in DamageType}

    parsed: list[EnemyArchetype] = []
    for entry in raw.get("enemy", ()):
        enemy_id = str(entry.get("id", ""))
        kind_raw = str(entry.get("kind", ""))
        if kind_raw not in known_kinds:
            problems.append(f"enemies.toml: {enemy_id} has unknown kind {kind_raw!r}")
            continue
        loot = tuple(str(item) for item in entry.get("loot", ()))
        for item_id in loot:
            if item_id not in item_ids:
                problems.append(f"enemies.toml: {enemy_id} drops unknown item {item_id!r}")

        element_raw = str(entry.get("element", ""))
        if element_raw and element_raw not in known_elements:
            problems.append(
                f"enemies.toml: {enemy_id} strikes with unknown element {element_raw!r}"
            )
            element_raw = ""

        parsed.append(
            EnemyArchetype(
                id=enemy_id,
                name=str(entry["name"]),
                kind=EnemyKind(kind_raw),
                biomes=tuple(str(biome) for biome in entry.get("biomes", ())),
                health=float(entry.get("health", 1.0)),
                damage=float(entry.get("damage", 1.0)),
                armor=float(entry.get("armor", 1.0)),
                initiative=float(entry.get("initiative", 1.0)),
                loot=loot,
                element=DamageType(element_raw) if element_raw else None,
            )
        )
    _check_unique((enemy.id for enemy in parsed), "enemies.toml", problems)
    return tuple(parsed), elite_titles


def _parse_turnings(raw: Mapping[str, Any], problems: list[str]) -> tuple[tuple[Turning, ...], str]:
    """Голосования Палаты и то из них, что открыто сейчас.

    Вопрос без ответов - это тупик на экране, поэтому их требуется не меньше
    двух. Открытым может быть только вопрос, который в файле есть: имя, за
    которым ничего нет, ловится здесь, а не на экране у игрока.
    """
    parsed: list[Turning] = []
    for entry in raw.get("turning", ()):
        turning_id = str(entry.get("id", ""))
        if not turning_id:
            problems.append("turnings.toml: an entry has no id")
            continue
        options = tuple(
            TurningOption(
                id=str(option.get("id", "")),
                name=str(option.get("name", "")),
                text=str(option.get("text", "")),
            )
            for option in entry.get("options", ())
        )
        if len(options) < 2:
            problems.append(f"turnings.toml: {turning_id} must offer at least 2 options")
        if any(not option.id or not option.name for option in options):
            problems.append(f"turnings.toml: {turning_id} has an option without an id or a name")
        _check_unique((option.id for option in options), f"turnings.toml: {turning_id}", problems)
        question = str(entry.get("question", ""))
        if not question:
            problems.append(f"turnings.toml: {turning_id} asks nothing")
        parsed.append(
            Turning(
                id=turning_id,
                name=str(entry.get("name", turning_id)),
                question=question,
                text=str(entry.get("text", "")),
                options=options,
            )
        )
    _check_unique((turning.id for turning in parsed), "turnings.toml", problems)

    open_id = str(raw.get("meta", {}).get("open", ""))
    if open_id and all(turning.id != open_id for turning in parsed):
        problems.append(f"turnings.toml: [meta].open names unknown turning {open_id!r}")
        open_id = ""
    return tuple(parsed), open_id


def _parse_quests(
    raw: Mapping[str, Any],
    item_ids: set[str],
    cities: Sequence[City],
    enemy_ids: set[str],
    problems: list[str],
) -> tuple[Quest, ...]:
    """Задания. Сломанное задание - тупик для игрока, поэтому его не пропускают.

    Всё, на что задание показывает, обязано существовать до старта игры: город,
    который его выдаёт, вещь, которой оно платит, и задание, за которым оно идёт.
    """
    known_cities = {city.id for city in cities}
    slots_by_city = {city.id: {location.slot for location in city.locations} for city in cities}
    known_objectives = {kind.value for kind in ObjectiveKind}
    enemy_kinds = {kind.value for kind in EnemyKind}

    parsed: list[Quest] = []
    for entry in raw.get("quest", ()):
        quest_id = str(entry.get("id", ""))
        city_id = str(entry.get("city", ""))
        objective_raw = str(entry.get("objective", ""))
        if city_id not in known_cities:
            problems.append(f"quests.toml: {quest_id} belongs to unknown city {city_id!r}")
            continue
        if objective_raw not in known_objectives:
            problems.append(f"quests.toml: {quest_id} has unknown objective {objective_raw!r}")
            continue

        objective = ObjectiveKind(objective_raw)
        target_kind = str(entry.get("target_kind", ""))
        if target_kind:
            allowed: frozenset[str]
            match objective:
                case ObjectiveKind.SEARCH:
                    allowed = SEARCHABLE_NODES
                case ObjectiveKind.CRAFT:
                    # Задание на сделанные вещи называет саму вещь, потому что именно
                    # так назвал бы её тот, кто её просит.
                    allowed = frozenset(item_ids)
                case _:
                    # Порода целиком - или один названный противник: «пятеро
                    # кабанов» это не «пятеро зверей», и оба условия законны
                    # (``domain/rules/quests._named``).
                    allowed = frozenset(enemy_kinds | enemy_ids)
            if target_kind not in allowed:
                problems.append(
                    f"quests.toml: {quest_id} narrows to unknown target {target_kind!r}"
                )
        reward_item = str(entry.get("reward_item", ""))
        if reward_item and reward_item not in item_ids:
            problems.append(f"quests.toml: {quest_id} pays with unknown item {reward_item!r}")
        if int(entry.get("target_count", 0)) < 1:
            problems.append(f"quests.toml: {quest_id} counts to less than one")

        # Куда идти - часть задания, а не догадка игрока. Локация проверяется
        # здесь: задание, посылающее в несуществующее место, хуже, чем никакое.
        location_slot = int(entry.get("location", 0))
        if location_slot and location_slot not in slots_by_city.get(city_id, set()):
            problems.append(
                f"quests.toml: {quest_id} sends the player to location {location_slot}, "
                f"which city {city_id!r} does not have"
            )
            location_slot = 0
        if location_slot and objective is ObjectiveKind.CRAFT:
            problems.append(f"quests.toml: {quest_id} is a craft and needs no location")
            location_slot = 0

        parsed.append(
            Quest(
                id=quest_id,
                city_id=city_id,
                level=int(entry.get("level", 1)),
                name=str(entry["name"]),
                giver=str(entry["giver"]),
                intro=str(entry.get("intro", "")),
                terms=str(entry["terms"]),
                objective=objective,
                target_count=int(entry.get("target_count", 1)),
                target_kind=target_kind,
                reward_gold=int(entry.get("reward_gold", 0)),
                reward_experience=int(entry.get("reward_experience", 0)),
                reward_item=reward_item,
                follows=str(entry.get("follows", "")),
                location_slot=location_slot,
            )
        )

    _check_unique((quest.id for quest in parsed), "quests.toml", problems)
    known = {quest.id for quest in parsed}
    for quest in parsed:
        if quest.follows and quest.follows not in known:
            problems.append(f"quests.toml: {quest.id} follows unknown contract {quest.follows!r}")
        if quest.follows == quest.id:
            problems.append(f"quests.toml: {quest.id} follows itself")
    return tuple(parsed)


def _validate_enemies(
    enemies: Sequence[EnemyArchetype],
    cities: Sequence[City],
    problems: list[str],
) -> None:
    if not enemies:
        problems.append("enemies.toml: no enemy archetypes defined")
        return
    biomes = {location.biome for city in cities for location in city.locations}
    for biome in sorted(biomes):
        if not any(enemy.fits(biome) for enemy in enemies):
            problems.append(f"enemies.toml: biome {biome!r} has no enemy archetype")


# --- world -----------------------------------------------------------


def _parse_world(raw: Mapping[str, Any], problems: list[str]) -> tuple[City, ...]:
    parsed: list[City] = []
    for entry in raw.get("city", ()):
        city_id = str(entry.get("id", ""))
        locations = tuple(
            Location(
                id=str(loc["id"]),
                slot=int(loc["slot"]),
                name=str(loc["name"]),
                biome=str(loc.get("biome", "")),
                level_min=int(loc["level_min"]),
                level_max=int(loc["level_max"]),
                city_id=city_id,
                pvp=bool(loc.get("pvp", False)),
            )
            for loc in entry.get("location", ())
        )
        raw_deep = entry.get("deep_dungeon", {})
        deep = DeepDungeon(
            name=str(raw_deep.get("name", "")),
            flavour=str(raw_deep.get("flavour", "")),
        )
        parsed.append(
            City(
                id=city_id,
                order=int(entry.get("order", 0)),
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                level_min=int(entry["level_min"]),
                level_max=int(entry["level_max"]),
                unlock_level=int(entry.get("unlock_level", 1)),
                unlock_requires=tuple(str(item) for item in entry.get("unlock_requires", ())),
                services=tuple(str(item) for item in entry.get("services", ())),
                locations=locations,
                deep_dungeon=deep,
            )
        )
    return tuple(parsed)


def _validate_world(cities: Sequence[City], rules: ProgressionRules, problems: list[str]) -> None:
    if len(cities) != EXPECTED_CITIES:
        problems.append(f"world.toml: expected {EXPECTED_CITIES} cities, found {len(cities)}")
    _check_unique((city.id for city in cities), "world.toml", problems)

    known_ids = {city.id for city in cities}
    covered: set[int] = set()

    for city in cities:
        if len(city.locations) != LOCATIONS_PER_CITY:
            problems.append(
                f"world.toml: {city.id} has {len(city.locations)} locations, "
                f"expected {LOCATIONS_PER_CITY}"
            )
        slots = [location.slot for location in city.locations]
        if slots != list(range(1, len(city.locations) + 1)):
            problems.append(f"world.toml: {city.id} has non-sequential location slots {slots}")

        if not city.deep_dungeon.name or not city.deep_dungeon.flavour:
            problems.append(f"world.toml: {city.id} is missing [city.deep_dungeon] name/flavour")

        for requirement in city.unlock_requires:
            if (
                requirement.startswith("city:")
                and requirement.removeprefix("city:") not in known_ids
            ):
                problems.append(f"world.toml: {city.id} requires unknown {requirement}")

        previous: Location | None = None
        for location in city.locations:
            if location.level_min > location.level_max:
                problems.append(f"world.toml: {city.id}/{location.id} has an inverted level range")
            if previous is not None:
                if location.level_min > previous.level_max:
                    problems.append(
                        f"world.toml: gap between {previous.id} and {location.id} in {city.id}"
                    )
                if (
                    location.level_min <= previous.level_min
                    or location.level_max <= previous.level_max
                ):
                    problems.append(
                        f"world.toml: {city.id}/{location.id} does not increase monotonically"
                    )
            covered.update(range(location.level_min, location.level_max + 1))
            previous = location

        if city.locations:
            if city.locations[0].level_min != city.level_min:
                problems.append(f"world.toml: {city.id} first location does not start at level_min")
            if city.locations[-1].level_max != city.level_max:
                problems.append(f"world.toml: {city.id} last location does not end at level_max")

    expected_levels = set(range(1, rules.max_character_level + 1))
    uncovered = sorted(expected_levels - covered)
    if uncovered:
        problems.append(
            f"world.toml: levels without any location: {uncovered[:10]}"
            + (" ..." if len(uncovered) > 10 else "")
        )

    ordered = sorted(cities, key=lambda city: city.order)
    for earlier, later in itertools.pairwise(ordered):
        if later.level_min <= earlier.level_min or later.level_max <= earlier.level_max:
            problems.append(f"world.toml: city bands must increase: {earlier.id} then {later.id}")
        if later.level_min > earlier.level_max:
            problems.append(f"world.toml: gap between city {earlier.id} and {later.id}")


# --- crafts ----------------------------------------------------------


def _build_craft_rules(raw: Mapping[str, Any], problems: list[str]) -> CraftRules:
    meta = raw.get("meta", {})
    qualities = tuple(
        QualityTier(
            id=str(entry["id"]),
            name=str(entry["name"]),
            extra=int(entry.get("extra", 0)),
            refund_percent=int(entry.get("refund_percent", 0)),
        )
        for entry in meta.get("qualities", ())
    )
    rules = CraftRules(
        max_rank=int(meta.get("max_rank", 5)),
        experience_per_rank=int(meta.get("experience_per_rank", 100)),
        rank_names=tuple(str(name) for name in meta.get("rank_names", ())),
        gather_base=int(meta.get("gather_base", 2)),
        gather_per_rank=int(meta.get("gather_per_rank", 1)),
        gather_experience=int(meta.get("gather_experience", 8)),
        qualities=qualities,
        good_chance_base=float(meta.get("good_chance_base", 0.0)),
        good_chance_per_rank=float(meta.get("good_chance_per_rank", 0.0)),
        fine_chance_base=float(meta.get("fine_chance_base", 0.0)),
        fine_chance_per_rank=float(meta.get("fine_chance_per_rank", 0.0)),
    )
    if rules.experience_per_rank < 1:
        problems.append("crafts.toml: [meta].experience_per_rank must be at least 1")
    if len(rules.rank_names) != rules.max_rank:
        problems.append(
            f"crafts.toml: [meta].rank_names must name all {rules.max_rank} ranks, "
            f"found {len(rules.rank_names)}"
        )
    if {tier.id for tier in qualities} != {"plain", "good", "fine"}:
        problems.append("crafts.toml: [meta].qualities must be exactly plain, good and fine")
    return rules


def _parse_crafts(
    raw: Mapping[str, Any],
    item_ids: set[str],
    rules: CraftRules,
    problems: list[str],
) -> tuple[tuple[Craft, ...], tuple[Recipe, ...]]:
    known_kinds = {kind.value for kind in CraftKind}
    crafts: list[Craft] = []
    for entry in raw.get("craft", ()):
        craft_id = str(entry.get("id", ""))
        kind_raw = str(entry.get("kind", ""))
        if kind_raw not in known_kinds:
            problems.append(f"crafts.toml: {craft_id} has unknown kind {kind_raw!r}")
            continue
        stat_raw = str(entry.get("stat", ""))
        if stat_raw not in {code.value for code in StatCode}:
            problems.append(f"crafts.toml: {craft_id} leans on unknown stat {stat_raw!r}")
            continue

        yields: list[CraftYield] = []
        for produced in entry.get("yields", ()):
            item_id = str(produced.get("item", ""))
            if item_id not in item_ids:
                problems.append(f"crafts.toml: {craft_id} gathers unknown item {item_id!r}")
                continue
            yields.append(
                CraftYield(
                    item_id=item_id,
                    level=int(produced.get("level", 1)),
                    # Где оно лежит в земле. Нет биомов - значит, везде, и это то, что
                    # позволяет ремеслу работать в городе садов и неба.
                    biomes=tuple(str(biome) for biome in produced.get("biomes", ())),
                )
            )

        kind = CraftKind(kind_raw)
        if kind is CraftKind.GATHERING and not yields:
            problems.append(f"crafts.toml: gathering craft {craft_id} brings nothing back")
        if kind is CraftKind.MAKING and yields:
            problems.append(f"crafts.toml: making craft {craft_id} cannot gather")

        crafts.append(
            Craft(
                id=craft_id,
                name=str(entry["name"]),
                kind=kind,
                stat=StatCode(stat_raw),
                description=str(entry.get("description", "")),
                yields=tuple(yields),
            )
        )

    craft_ids = {craft.id for craft in crafts}
    making = {craft.id for craft in crafts if craft.kind is CraftKind.MAKING}
    recipes: list[Recipe] = []
    for entry in raw.get("recipe", ()):
        recipe_id = str(entry.get("id", ""))
        craft_id = str(entry.get("craft", ""))
        if craft_id not in craft_ids:
            problems.append(f"crafts.toml: {recipe_id} belongs to unknown craft {craft_id!r}")
            continue
        if craft_id not in making:
            problems.append(f"crafts.toml: {recipe_id} hangs on a gathering craft {craft_id!r}")
            continue

        rank = int(entry.get("rank", 1))
        if not 1 <= rank <= rules.max_rank:
            problems.append(
                f"crafts.toml: {recipe_id} asks for rank {rank}, outside 1..{rules.max_rank}"
            )

        inputs: list[RecipeInput] = []
        for need in entry.get("inputs", ()):
            item_id = str(need.get("item", ""))
            count = int(need.get("count", 0))
            if item_id not in item_ids:
                problems.append(f"crafts.toml: {recipe_id} needs unknown item {item_id!r}")
                continue
            if count < 1:
                problems.append(f"crafts.toml: {recipe_id} needs less than one {item_id}")
                continue
            inputs.append(RecipeInput(item_id=item_id, count=count))
        if not inputs:
            problems.append(f"crafts.toml: {recipe_id} needs no materials at all")

        output_raw = entry.get("output", {})
        output_id = str(output_raw.get("item", ""))
        if output_id not in item_ids:
            problems.append(f"crafts.toml: {recipe_id} makes unknown item {output_id!r}")

        recipes.append(
            Recipe(
                id=recipe_id,
                craft_id=craft_id,
                rank=rank,
                inputs=tuple(inputs),
                output_id=output_id,
                output_count=max(1, int(output_raw.get("count", 1))),
                experience=int(entry.get("experience", 0)),
            )
        )
    return tuple(crafts), tuple(recipes)


def _validate_crafts(
    crafts: Sequence[Craft], recipes: Sequence[Recipe], problems: list[str]
) -> None:
    if len(crafts) < MINIMUM_CRAFTS:
        problems.append(
            f"crafts.toml: expected at least {MINIMUM_CRAFTS} crafts, found {len(crafts)}"
        )
    _check_unique((craft.id for craft in crafts), "crafts.toml", problems)
    _check_unique((craft.name for craft in crafts), "crafts.toml (names)", problems)
    _check_unique((recipe.id for recipe in recipes), "crafts.toml (recipes)", problems)

    for craft in crafts:
        if craft.kind is CraftKind.MAKING and not any(
            recipe.craft_id == craft.id for recipe in recipes
        ):
            problems.append(f"crafts.toml: making craft {craft.id} has no recipes")


# --- вспомогательное ------------------------------------------------


def _check_unique(values: Any, source: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            problems.append(f"{source}: duplicate entry {value!r}")
        seen.add(value)
