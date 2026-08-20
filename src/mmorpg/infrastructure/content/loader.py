"""Load ``content/*.toml`` into immutable domain objects.

Called exactly once, at startup, before the bot starts polling. Reading files
here is synchronous on purpose: it happens before the event loop serves anything.
Every validation problem is collected and reported together, so a content author
sees the whole list instead of fixing one typo per restart.
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
    EdgeEffect,
    EquipSlot,
    GameContent,
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
from mmorpg.domain.entities.location import EnemyArchetype, EnemyKind
from mmorpg.domain.entities.quest import ObjectiveKind, Quest
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules.equipment import UNARMED_DAMAGE, WEAPON_SLOT

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

# Node kinds a "search" contract may ask for. Kept as strings rather than as an
# import of NodeKind, because content speaks the content vocabulary.
SEARCHABLE_NODES = frozenset({"gather", "cache", "shrine", "event"})

MINIMUM_CRAFTS = 4
EXPECTED_RACES = 16
EXPECTED_CLASSES = 8
MINIMUM_TRAITS = 60
EXPECTED_CITIES = 15
LOCATIONS_PER_CITY = 5
RACE_STAT_BUDGET = 3


class ContentError(RuntimeError):
    """Raised when the content directory fails validation."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        listed = "\n  - ".join(self.problems)
        super().__init__(
            f"content validation failed ({len(self.problems)} problems):\n  - {listed}"
        )


def load_content(content_dir: Path) -> GameContent:
    """Parse and validate the whole content directory.

    Raises:
        ContentError: if any file is missing, malformed or inconsistent.
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
    items, rarities = gear.items, gear.rarities
    races = _parse_races(raw["races.toml"], skills_by_code, problems)
    classes = _parse_classes(raw["classes.toml"], gear, problems)
    traits = _parse_traits(raw["traits.toml"], modifier_keys, set(categories), problems)
    _validate_skill_weapons(skills, classes, gear, problems)
    cities = _parse_world(raw["world.toml"], problems)
    item_ids = {item.id for item in items}
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

    if problems:
        raise ContentError(problems)

    return GameContent.build(
        races=races,
        classes=classes,
        traits=traits,
        items=items,
        skills=skills,
        cities=cities,
        rarities=rarities,
        slots=gear.slots,
        weapon_types=gear.weapon_types,
        armor_types=gear.armor_types,
        enemy_archetypes=enemies,
        elite_titles=elite_titles,
        quests=quests,
        crafts=crafts,
        recipes=recipes,
        craft_rules=craft_rules,
        trait_categories=categories,
        inverted_modifiers=inverted_modifiers,
        rules=rules,
        turnings=turnings,
        open_turning_id=open_turning_id,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:  # pragma: no cover - depends on broken input
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
        active_slots=int(class_meta.get("active_slots", 6)),
        passive_slots=int(class_meta.get("passive_slots", 3)),
        racial_slots=int(class_meta.get("racial_slots", 1)),
        traits_at_creation=int(trait_meta.get("picks_at_creation", 2)),
        max_rank=int(skill_meta.get("max_rank", 5)),
        edge_rank=int(skill_meta.get("edge_rank", 3)),
        skill_point_per_level=int(skill_meta.get("skill_point_per_level", 1)),
    )
    if len(rules.active_unlock_levels) != 8:
        problems.append("classes.toml: [meta].active_unlock_levels must list 8 levels")
    if len(rules.passive_unlock_levels) != 6:
        problems.append("classes.toml: [meta].passive_unlock_levels must list 6 levels")
    if rules.active_slots != 6 or rules.passive_slots != 3 or rules.racial_slots != 1:
        problems.append("classes.toml: the panel is fixed at 6 active, 3 passive and 1 racial slot")
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
            )
        )
    return tuple(parsed)


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
        "shield",
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
        shield=float(raw.get("shield", 0)),
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
        parsed.append(
            Race(
                id=race_id,
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                bonuses=bonuses,
                passive=RacePassive(
                    id=str(passive_raw.get("id", "")),
                    name=str(passive_raw.get("name", "")),
                    text=str(passive_raw.get("text", "")),
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


# --- classes ---------------------------------------------------------


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
        if len(actives) != 8:
            problems.append(f"skills.toml: class {klass.id} has {len(actives)} actives, expected 8")
        if len(passives) != 6:
            problems.append(
                f"skills.toml: class {klass.id} has {len(passives)} passives, expected 6"
            )
        active_levels = tuple(skill.level for skill in actives)
        if len(actives) == 8 and active_levels != rules.active_unlock_levels:
            problems.append(
                f"skills.toml: class {klass.id} unlocks actives at {active_levels}, "
                f"expected {rules.active_unlock_levels}"
            )
        passive_levels = tuple(skill.level for skill in passives)
        if len(passives) == 6 and passive_levels != rules.passive_unlock_levels:
            problems.append(
                f"skills.toml: class {klass.id} unlocks passives at {passive_levels}, "
                f"expected {rules.passive_unlock_levels}"
            )


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


def _parse_items(
    raw: Mapping[str, Any],
    modifier_keys: frozenset[str],
    skills_by_code: Mapping[str, Skill],
    problems: list[str],
) -> ItemContent:
    meta = raw.get("meta", {})
    rarities = tuple(
        Rarity(
            id=str(entry["id"]),
            name=str(entry["name"]),
            weight=int(entry["weight"]),
            price_factor=float(entry["price_factor"]),
        )
        for entry in meta.get("rarities", ())
    )
    rarity_ids = {rarity.id for rarity in rarities}
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

    weapon_types = tuple(
        WeaponType(
            id=str(entry["id"]),
            name=str(entry["name"]),
            damage=float(entry.get("damage", 1.0)),
            modifiers=_type_modifiers(
                f"weapon type {entry.get('id', '')}", entry, modifier_keys, problems
            ),
        )
        for entry in meta.get("weapon_types", ())
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
    # Оружие слабее голых рук - это не выбор, а ошибка, и стоит она игроку боя.
    for kind in weapon_types:
        if kind.damage < UNARMED_DAMAGE:
            problems.append(
                f"items.toml: weapon type {kind.id} hits for {kind.damage}, "
                f"which is weaker than bare hands ({UNARMED_DAMAGE})"
            )

    parsed: list[Item] = []
    for entry in raw.get("item", ()):
        item_id = str(entry.get("id", ""))
        kind_raw = str(entry.get("kind", ""))
        if kind_raw not in {kind.value for kind in ItemKind}:
            problems.append(f"items.toml: {item_id} has unknown kind {kind_raw!r}")
            continue
        rarity = str(entry.get("rarity", ""))
        if rarity not in rarity_ids:
            problems.append(f"items.toml: {item_id} has unknown rarity {rarity!r}")
        slot = str(entry.get("slot", "none"))
        if slot not in slot_ids:
            problems.append(f"items.toml: {item_id} has unknown slot {slot!r}")

        # Род оружия и род доспеха - не украшение карточки: по ним класс решает,
        # даётся ли ему эта вещь, умение - сработает ли оно, а броня - сколько её
        # вообще есть. Вещь без рода была бы вещью, о которой ничего из этого
        # спросить нельзя, поэтому загрузчик её не принимает.
        weapon_type = str(entry.get("weapon_type", ""))
        armor_type = str(entry.get("armor_type", ""))
        if kind_raw == ItemKind.EQUIPMENT.value and slot == WEAPON_SLOT and not weapon_type:
            problems.append(f"items.toml: weapon {item_id} declares no weapon_type")
        if kind_raw == ItemKind.EQUIPMENT.value and slot in armor_slots and not armor_type:
            problems.append(f"items.toml: armour {item_id} declares no armor_type")
        if weapon_type and weapon_type not in weapon_type_ids:
            problems.append(f"items.toml: {item_id} has unknown weapon_type {weapon_type!r}")
        if armor_type and armor_type not in armor_type_ids:
            problems.append(f"items.toml: {item_id} has unknown armor_type {armor_type!r}")
        if weapon_type and slot != WEAPON_SLOT:
            problems.append(f"items.toml: {item_id} is not a weapon but names a weapon_type")
        if armor_type and slot not in armor_slots:
            problems.append(f"items.toml: {item_id} is not armour but names an armor_type")
        if "text" in entry:
            problems.append(
                f"items.toml: {item_id} has a text field; items are generated in their "
                "hundreds and describe themselves by name, kind and numbers"
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
                weapon_type=weapon_type,
                armor_type=armor_type,
                effect=effect,
            )
        )
    _check_unique((item.id for item in parsed), "items.toml", problems)
    return ItemContent(tuple(parsed), rarities, slots, weapon_types, armor_types)


# --- enemies ---------------------------------------------------------


def _parse_enemies(
    raw: Mapping[str, Any],
    item_ids: set[str],
    problems: list[str],
) -> tuple[tuple[EnemyArchetype, ...], tuple[str, ...]]:
    meta = raw.get("meta", {})
    elite_titles = tuple(str(title) for title in meta.get("elite_titles", ()))
    known_kinds = {kind.value for kind in EnemyKind}

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
    """Contracts. A broken contract is a dead end for a player, so it is refused.

    Everything a contract points at has to exist before the game starts: the city
    that hands it out, the item it pays with, and the contract it follows.
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
                    # A contract for made goods names the thing itself, because
                    # that is what the person asking for it would name.
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
                    # Where it is in the ground. No biomes means everywhere, which
                    # is what keeps a craft workable in a city of gardens and sky.
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


# --- helpers ---------------------------------------------------------


def _check_unique(values: Any, source: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            problems.append(f"{source}: duplicate entry {value!r}")
        seen.add(value)
