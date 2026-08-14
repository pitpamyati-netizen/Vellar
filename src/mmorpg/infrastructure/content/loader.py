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
from typing import Any

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.content import (
    CharacterClass,
    City,
    ClassResource,
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
)
from mmorpg.domain.entities.location import EnemyArchetype, EnemyKind
from mmorpg.domain.entities.quest import ObjectiveKind, Quest
from mmorpg.domain.entities.stats import StatBlock, StatCode

CONTENT_FILES = (
    "world.toml",
    "races.toml",
    "classes.toml",
    "traits.toml",
    "skills.toml",
    "items.toml",
    "enemies.toml",
    "quests.toml",
)

# Node kinds a "search" contract may ask for. Kept as strings rather than as an
# import of NodeKind, because content speaks the content vocabulary.
SEARCHABLE_NODES = frozenset({"gather", "cache", "shrine", "event"})

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

    races = _parse_races(raw["races.toml"], skills_by_code, problems)
    classes = _parse_classes(raw["classes.toml"], problems)
    traits = _parse_traits(raw["traits.toml"], modifier_keys, set(categories), problems)
    items, rarities = _parse_items(raw["items.toml"], modifier_keys, skills_by_code, problems)
    cities = _parse_world(raw["world.toml"], problems)
    item_ids = {item.id for item in items}
    enemies, elite_titles = _parse_enemies(raw["enemies.toml"], item_ids, problems)
    _validate_enemies(enemies, cities, problems)
    quests = _parse_quests(raw["quests.toml"], item_ids, cities, problems)

    rules = _build_rules(raw, problems)

    _validate_races(races, problems)
    _validate_classes(classes, skills, rules, problems)
    _validate_traits(traits, problems)
    _validate_world(cities, rules, problems)

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
        enemy_archetypes=enemies,
        elite_titles=elite_titles,
        quests=quests,
        trait_categories=categories,
        inverted_modifiers=inverted_modifiers,
        rules=rules,
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
        edges = (
            SkillEdge(
                code=f"{code}_a", name=str(raw_edges[0]["name"]), text=str(raw_edges[0]["text"])
            ),
            SkillEdge(
                code=f"{code}_b", name=str(raw_edges[1]["name"]), text=str(raw_edges[1]["text"])
            ),
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
                edges=edges,
                cost=int(entry.get("cost", 0)),
                cooldown=int(entry.get("cooldown", 0)),
                target=target,
                scaling=scaling,
                rank_step=float(entry.get("rank_step", default_step)),
                tag=tag,
            )
        )
    return tuple(parsed)


# --- races -----------------------------------------------------------


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


def _parse_classes(raw: Mapping[str, Any], problems: list[str]) -> tuple[CharacterClass, ...]:
    parsed: list[CharacterClass] = []
    for entry in raw.get("class", ()):
        class_id = str(entry.get("id", ""))
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
                key_stats=key_stats,
                bonuses=bonuses,
                resource=resource,
                health=health,
            )
        )
    return tuple(parsed)


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


def _parse_items(
    raw: Mapping[str, Any],
    modifier_keys: frozenset[str],
    skills_by_code: Mapping[str, Skill],
    problems: list[str],
) -> tuple[tuple[Item, ...], tuple[Rarity, ...]]:
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
    slot_ids = {str(entry["id"]) for entry in meta.get("slots", ())} | {"none"}

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
                text=str(entry.get("text", "")),
                modifiers=modifiers,
                skill_modifiers=skill_modifiers,
                stack=int(entry.get("stack", 1)),
                effect=effect,
            )
        )
    _check_unique((item.id for item in parsed), "items.toml", problems)
    return tuple(parsed), rarities


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


def _parse_quests(
    raw: Mapping[str, Any],
    item_ids: set[str],
    cities: Sequence[City],
    problems: list[str],
) -> tuple[Quest, ...]:
    """Contracts. A broken contract is a dead end for a player, so it is refused.

    Everything a contract points at has to exist before the game starts: the city
    that hands it out, the item it pays with, and the contract it follows.
    """
    known_cities = {city.id for city in cities}
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
            allowed = SEARCHABLE_NODES if objective is ObjectiveKind.SEARCH else enemy_kinds
            if target_kind not in allowed:
                problems.append(
                    f"quests.toml: {quest_id} narrows to unknown target {target_kind!r}"
                )
        reward_item = str(entry.get("reward_item", ""))
        if reward_item and reward_item not in item_ids:
            problems.append(f"quests.toml: {quest_id} pays with unknown item {reward_item!r}")
        if int(entry.get("target_count", 0)) < 1:
            problems.append(f"quests.toml: {quest_id} counts to less than one")

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


# --- helpers ---------------------------------------------------------


def _check_unique(values: Any, source: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            problems.append(f"{source}: duplicate entry {value!r}")
        seen.add(value)
