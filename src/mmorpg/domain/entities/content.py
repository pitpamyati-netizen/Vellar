"""Static game content as immutable in-memory objects.

Everything here is loaded once at startup from ``content/*.toml`` and never
changes at runtime, so every object is ``frozen`` and ``slots``. Lookups go
through the dict indexes on :class:`GameContent`, which are O(1).

This module is pure data plus lookups - no I/O, no parsing. Parsing lives in
``mmorpg.infrastructure.content.loader``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.craft import Craft, CraftKind, CraftRules, Recipe
from mmorpg.domain.entities.location import EnemyArchetype
from mmorpg.domain.entities.quest import Quest
from mmorpg.domain.entities.stats import StatBlock, StatCode


class SkillKind(StrEnum):
    ACTIVE = "active"
    PASSIVE = "passive"


class OwnerKind(StrEnum):
    CLASS = "class"
    RACE = "race"


class ItemKind(StrEnum):
    EQUIPMENT = "equipment"
    CONSUMABLE = "consumable"
    MATERIAL = "material"


@dataclass(frozen=True, slots=True)
class SkillEdge:
    """One of the two rank-3 modifications of a skill.

    An edge changes how a skill behaves; it never adds a button.
    """

    code: str
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class Skill:
    code: str
    name: str
    owner_kind: OwnerKind
    owner_id: str
    kind: SkillKind
    level: int
    text: str
    effect: str
    power: float
    edges: tuple[SkillEdge, SkillEdge]
    cost: int = 0
    cooldown: int = 0
    target: str = "self"
    scaling: StatCode | None = None
    rank_step: float = 0.15
    #: The trace this skill leaves. Left out, it is read off the effect by
    #: ``skill_effects.tag_of``; content names it where the effect would say the
    #: wrong thing, and where a class would otherwise never see all three tags.
    tag: ActionTag | None = None

    @property
    def owner(self) -> str:
        return f"{self.owner_kind.value}:{self.owner_id}"

    @property
    def is_active(self) -> bool:
        return self.kind is SkillKind.ACTIVE

    def power_at_rank(self, rank: int) -> float:
        """Power grows linearly with rank; rank 1 is the printed value."""
        return self.power * (1.0 + self.rank_step * (rank - 1))

    def edge(self, code: str) -> SkillEdge:
        for edge in self.edges:
            if edge.code == code:
                return edge
        msg = f"skill {self.code} has no edge {code}"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class RacePassive:
    """Always-on racial ability. It never occupies a slot."""

    id: str
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class Race:
    id: str
    name: str
    description: str
    bonuses: StatBlock
    passive: RacePassive
    active_code: str


@dataclass(frozen=True, slots=True)
class ClassResource:
    """The class resource pool - valor, rage, mana and so on."""

    id: str
    name: str
    base: float
    per_level: float
    stat: StatCode
    per_stat: float
    regen_per_turn: float


@dataclass(frozen=True, slots=True)
class HealthCurve:
    base: float
    per_level: float
    per_endurance: float


@dataclass(frozen=True, slots=True)
class CharacterClass:
    id: str
    name: str
    role: str
    description: str
    key_stats: tuple[StatCode, ...]
    bonuses: StatBlock
    resource: ClassResource
    health: HealthCurve


@dataclass(frozen=True, slots=True)
class Trait:
    """A modifier-only character trait. Traits never grant skills or buttons."""

    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    modifiers: Mapping[str, float]
    text: str


@dataclass(frozen=True, slots=True)
class ItemEffect:
    kind: str
    power: float
    turns: int = 0


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    name: str
    kind: ItemKind
    slot: str
    rarity: str
    level: int
    price: int
    text: str
    modifiers: Mapping[str, float]
    skill_modifiers: Mapping[str, float]
    stack: int = 1
    effect: ItemEffect | None = None
    #: Что это за сырьё - "травы", "руда", "шкуры", "обломки". Пусто у всего,
    #: что сырьём не является, и у сырья, которое годится отовсюду.
    source: str = ""

    @property
    def is_equipment(self) -> bool:
        return self.kind is ItemKind.EQUIPMENT


@dataclass(frozen=True, slots=True)
class Rarity:
    id: str
    name: str
    weight: int
    price_factor: float


@dataclass(frozen=True, slots=True)
class Npc:
    """Человек, который стоит в городе и с которым можно заговорить.

    Житель — не услуга и не лавка: он ничего не продаёт и ничем не торгует. Всё,
    что он делает, — стоит на своём месте, называет своё занятие и держит подряды,
    которые сам же и раздаёт (``domain/rules/overlay.py``). Приходит он только от
    смотрителя: в ``content/`` жителей нет, потому что мир пишется правками, а не
    перезапуском.
    """

    id: str
    city_id: str
    name: str
    role: str = ""
    text: str = ""

    @property
    def title(self) -> str:
        """Как его называют одной строкой: имя и занятие, если оно названо."""
        return f"{self.name}, {self.role}" if self.role else self.name


@dataclass(frozen=True, slots=True)
class TurningOption:
    """Один ответ на счётный вопрос Оборота."""

    id: str
    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Turning:
    """Оборот: вопрос, который Палата задаёт игре, и ответы, между которыми
    считают голоса.

    Сам вопрос ничего не решает в правилах: он собирает счёт. Что записано в
    книгу Палаты, видно на экране и уходит в канал, а числа правит тот, кто
    считает итог цикла (``docs/endgame.md``).
    """

    id: str
    name: str
    question: str
    text: str = ""
    options: tuple[TurningOption, ...] = ()

    def has_option(self, option_id: str) -> bool:
        return any(option.id == option_id for option in self.options)

    def option(self, option_id: str) -> TurningOption:
        for option in self.options:
            if option.id == option_id:
                return option
        raise KeyError(option_id)


@dataclass(frozen=True, slots=True)
class Location:
    id: str
    slot: int
    name: str
    biome: str
    level_min: int
    level_max: int
    city_id: str
    # Whether players may attack each other here. Off everywhere by default: a
    # place where somebody can take your gold has to say so before you walk in
    # (``domain/rules/pvp.py``).
    pvp: bool = False

    def covers(self, level: int) -> bool:
        return self.level_min <= level <= self.level_max


@dataclass(frozen=True, slots=True)
class City:
    id: str
    order: int
    name: str
    description: str
    level_min: int
    level_max: int
    unlock_level: int
    unlock_requires: tuple[str, ...]
    services: tuple[str, ...]
    locations: tuple[Location, ...]

    def location(self, slot: int) -> Location:
        for location in self.locations:
            if location.slot == slot:
                return location
        msg = f"city {self.id} has no location slot {slot}"
        raise KeyError(msg)

    def has_location(self, slot: int) -> bool:
        return any(location.slot == slot for location in self.locations)

    @property
    def biomes(self) -> frozenset[str]:
        """What kind of ground lies around this city.

        The city itself has no biome - what it has is five locations, and their
        biomes are what a gathering craft can work here (``domain/rules/crafts``).
        """
        return frozenset(location.biome for location in self.locations)


@dataclass(frozen=True, slots=True)
class ProgressionRules:
    """Structural numbers shared by content and rules."""

    max_character_level: int
    base_stat_value: int
    free_points_at_creation: int
    stat_points_per_level: int
    active_unlock_levels: tuple[int, ...]
    passive_unlock_levels: tuple[int, ...]
    active_slots: int
    passive_slots: int
    racial_slots: int
    traits_at_creation: int
    max_rank: int
    edge_rank: int
    skill_point_per_level: int


@dataclass(frozen=True, slots=True)
class GameContent:
    """Everything static, indexed for O(1) access."""

    races: tuple[Race, ...]
    classes: tuple[CharacterClass, ...]
    traits: tuple[Trait, ...]
    items: tuple[Item, ...]
    skills: tuple[Skill, ...]
    cities: tuple[City, ...]
    rarities: tuple[Rarity, ...]
    enemy_archetypes: tuple[EnemyArchetype, ...]
    elite_titles: tuple[str, ...]
    quests: tuple[Quest, ...]
    crafts: tuple[Craft, ...]
    recipes: tuple[Recipe, ...]
    craft_rules: CraftRules
    trait_categories: Mapping[str, str]
    inverted_modifiers: frozenset[str]
    rules: ProgressionRules
    npcs: tuple[Npc, ...]
    turnings: tuple[Turning, ...]
    #: Какой Оборот открыт сейчас. Пусто - Палата ничего не спрашивает.
    open_turning_id: str

    _races_by_id: Mapping[str, Race]
    _classes_by_id: Mapping[str, CharacterClass]
    _traits_by_id: Mapping[str, Trait]
    _items_by_id: Mapping[str, Item]
    _skills_by_code: Mapping[str, Skill]
    _skills_by_owner: Mapping[str, tuple[Skill, ...]]
    _cities_by_id: Mapping[str, City]
    _rarities_by_id: Mapping[str, Rarity]
    _quests_by_id: Mapping[str, Quest]
    _crafts_by_id: Mapping[str, Craft]
    _recipes_by_id: Mapping[str, Recipe]
    _npcs_by_id: Mapping[str, Npc]
    _turnings_by_id: Mapping[str, Turning]

    @classmethod
    def build(
        cls,
        *,
        races: Sequence[Race],
        classes: Sequence[CharacterClass],
        traits: Sequence[Trait],
        items: Sequence[Item],
        skills: Sequence[Skill],
        cities: Sequence[City],
        rarities: Sequence[Rarity],
        enemy_archetypes: Sequence[EnemyArchetype],
        elite_titles: Sequence[str],
        trait_categories: Mapping[str, str],
        inverted_modifiers: frozenset[str],
        rules: ProgressionRules,
        craft_rules: CraftRules,
        quests: Sequence[Quest] = (),
        crafts: Sequence[Craft] = (),
        recipes: Sequence[Recipe] = (),
        npcs: Sequence[Npc] = (),
        turnings: Sequence[Turning] = (),
        open_turning_id: str = "",
    ) -> GameContent:
        """Build the registry and its indexes."""
        by_owner: dict[str, list[Skill]] = {}
        for skill in skills:
            by_owner.setdefault(skill.owner, []).append(skill)

        return cls(
            races=tuple(races),
            classes=tuple(classes),
            traits=tuple(traits),
            items=tuple(items),
            skills=tuple(skills),
            cities=tuple(cities),
            rarities=tuple(rarities),
            enemy_archetypes=tuple(enemy_archetypes),
            elite_titles=tuple(elite_titles),
            quests=tuple(quests),
            crafts=tuple(crafts),
            recipes=tuple(recipes),
            craft_rules=craft_rules,
            trait_categories=MappingProxyType(dict(trait_categories)),
            inverted_modifiers=inverted_modifiers,
            rules=rules,
            npcs=tuple(npcs),
            turnings=tuple(turnings),
            open_turning_id=open_turning_id,
            _races_by_id=MappingProxyType({race.id: race for race in races}),
            _classes_by_id=MappingProxyType({klass.id: klass for klass in classes}),
            _traits_by_id=MappingProxyType({trait.id: trait for trait in traits}),
            _items_by_id=MappingProxyType({item.id: item for item in items}),
            _skills_by_code=MappingProxyType({skill.code: skill for skill in skills}),
            _skills_by_owner=MappingProxyType(
                {owner: tuple(found) for owner, found in by_owner.items()}
            ),
            _cities_by_id=MappingProxyType({city.id: city for city in cities}),
            _rarities_by_id=MappingProxyType({rarity.id: rarity for rarity in rarities}),
            _quests_by_id=MappingProxyType({quest.id: quest for quest in quests}),
            _crafts_by_id=MappingProxyType({craft.id: craft for craft in crafts}),
            _recipes_by_id=MappingProxyType({recipe.id: recipe for recipe in recipes}),
            _npcs_by_id=MappingProxyType({npc.id: npc for npc in npcs}),
            _turnings_by_id=MappingProxyType({turning.id: turning for turning in turnings}),
        )

    # --- lookups -----------------------------------------------------

    def race(self, race_id: str) -> Race:
        return self._races_by_id[race_id]

    def character_class(self, class_id: str) -> CharacterClass:
        return self._classes_by_id[class_id]

    def trait(self, trait_id: str) -> Trait:
        return self._traits_by_id[trait_id]

    def item(self, item_id: str) -> Item:
        return self._items_by_id[item_id]

    def has_item(self, item_id: str) -> bool:
        return item_id in self._items_by_id

    def skill(self, code: str) -> Skill:
        return self._skills_by_code[code]

    def city(self, city_id: str) -> City:
        return self._cities_by_id[city_id]

    def has_city(self, city_id: str) -> bool:
        return city_id in self._cities_by_id

    def rarity(self, rarity_id: str) -> Rarity:
        return self._rarities_by_id[rarity_id]

    def quest(self, quest_id: str) -> Quest:
        return self._quests_by_id[quest_id]

    def has_quest(self, quest_id: str) -> bool:
        return quest_id in self._quests_by_id

    def quests_in(self, city_id: str) -> tuple[Quest, ...]:
        """Contracts a city hands out, easiest first."""
        return tuple(
            sorted(
                (quest for quest in self.quests if quest.city_id == city_id),
                key=lambda quest: (quest.level, quest.id),
            )
        )

    def npc(self, npc_id: str) -> Npc:
        return self._npcs_by_id[npc_id]

    def has_npc(self, npc_id: str) -> bool:
        return npc_id in self._npcs_by_id

    def npcs_in(self, city_id: str) -> tuple[Npc, ...]:
        """Кто стоит в этом городе, по имени."""
        return tuple(
            sorted((npc for npc in self.npcs if npc.city_id == city_id), key=lambda npc: npc.name)
        )

    def quests_of(self, npc_id: str) -> tuple[Quest, ...]:
        """Подряды, которые раздаёт этот житель, от лёгкого к тяжёлому."""
        return tuple(
            sorted(
                (quest for quest in self.quests if quest.giver_id == npc_id),
                key=lambda quest: (quest.level, quest.id),
            )
        )

    # --- Обороты ------------------------------------------------------

    def turning(self, turning_id: str) -> Turning:
        return self._turnings_by_id[turning_id]

    def has_turning(self, turning_id: str) -> bool:
        return turning_id in self._turnings_by_id

    def open_turning(self) -> Turning | None:
        """Вопрос, на который сейчас отвечают, или ``None``.

        Содержимое переживает сохранённое состояние: вопрос, которого больше нет
        в файлах, не открыт (``Claude.md``, правило 8).
        """
        if not self.open_turning_id or not self.has_turning(self.open_turning_id):
            return None
        return self.turning(self.open_turning_id)

    def craft(self, craft_id: str) -> Craft:
        return self._crafts_by_id[craft_id]

    def has_craft(self, craft_id: str) -> bool:
        return craft_id in self._crafts_by_id

    def recipe(self, recipe_id: str) -> Recipe:
        return self._recipes_by_id[recipe_id]

    def crafts_of_kind(self, kind: CraftKind) -> tuple[Craft, ...]:
        return tuple(craft for craft in self.crafts if craft.kind is kind)

    def recipes_of(self, craft_id: str) -> tuple[Recipe, ...]:
        """Recipes a craft knows, easiest first - the order the screen lists them."""
        return tuple(
            sorted(
                (recipe for recipe in self.recipes if recipe.craft_id == craft_id),
                key=lambda recipe: (recipe.rank, recipe.id),
            )
        )

    def has_skill(self, code: str) -> bool:
        return code in self._skills_by_code

    def skills_of(self, owner: str, kind: SkillKind | None = None) -> tuple[Skill, ...]:
        """Skills belonging to ``class:<id>`` or ``race:<id>``."""
        found = self._skills_by_owner.get(owner, ())
        if kind is None:
            return found
        return tuple(skill for skill in found if skill.kind is kind)

    def class_skills_up_to(self, class_id: str, level: int, kind: SkillKind) -> tuple[Skill, ...]:
        """Class skills the character has unlocked at ``level``."""
        return tuple(
            skill
            for skill in self.skills_of(f"{OwnerKind.CLASS.value}:{class_id}", kind)
            if skill.level <= level
        )

    def racial_active(self, race_id: str) -> Skill:
        return self.skill(self.race(race_id).active_code)

    def traits_in_category(self, category: str) -> tuple[Trait, ...]:
        return tuple(trait for trait in self.traits if trait.category == category)

    def city_by_order(self, order: int) -> City:
        for city in self.cities:
            if city.order == order:
                return city
        msg = f"no city with order {order}"
        raise KeyError(msg)

    def cities_available_at(self, level: int) -> tuple[City, ...]:
        return tuple(city for city in self.cities if city.unlock_level <= level)

    def is_bonus(self, modifier_key: str, value: float) -> bool:
        """Whether ``value`` helps the character for this modifier key.

        Most keys are "higher is better", but a handful - shop prices, damage taken -
        are the other way round. Screens and balance tests both need to know which.
        """
        if modifier_key in self.inverted_modifiers:
            return value < 0
        return value > 0
