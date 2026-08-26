"""Неизменное содержимое игры как объекты в памяти.

Всё здесь загружается один раз на старте из ``content/*.toml`` и на ходу не
меняется, поэтому каждый объект - ``frozen`` и ``slots``. Поиск идёт через
словарные указатели :class:`GameContent`, а это O(1).

Модуль - чистые данные плюс поиск: ни ввода-вывода, ни разбора. Разбор живёт в
``mmorpg.infrastructure.content.loader``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.craft import Craft, CraftKind, CraftRules, Recipe
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.dice import MAX_SPREAD, Dice
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
class EdgeEffect:
    """Поправка, которую грань вносит в умение.

    Живёт здесь, а не среди правил, потому что это содержимое: строка из
    ``skills.toml``, прочитанная загрузчиком. Что с ней делать — знает
    ``domain/rules/edges.py``, и там же сказано, зачем она вообще нужна.

    Всё в процентах или в ходах: теми же единицами описано и само умение
    (``Claude.md``, правило 7 — абсолютных чисел в содержимом нет).
    """

    #: Прибавка к силе умения и поправка к стоимости, в процентах.
    power: float = 0.0
    cost: float = 0.0
    #: Ходы: откат, срок действия, урон по времени, пропуск хода.
    cooldown: int = 0
    duration: int = 0
    dot_turns: int = 0
    stun_turns: int = 0
    #: Сколько ударов добавилось и какой силы каждый, в процентах от урона умения.
    hits: int = 0
    hit_power: float = 100.0
    #: Доля урона второй цели; задевает ли всех.
    splash: float = 0.0
    aoe: bool = False
    #: Прибавки в процентах: игнорируемая броня, шанс крита, вампиризм.
    pierce: float = 0.0
    crit: float = 0.0
    lifesteal: float = 0.0
    #: Сколько отрицательных эффектов снимается сверх снятого умением.
    cleanse: int = 0
    #: Лечение и барьер сверх основного действия, в процентах от максимума
    #: здоровья.
    heal: float = 0.0
    barrier: float = 0.0
    #: Модификаторы сверх наложенных умением: ключи из ``traits.toml``.
    self_modifiers: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    target_modifiers: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def empty(self) -> bool:
        """Ничего не меняет: такой грани в содержимом быть не должно."""
        return self == EdgeEffect()


@dataclass(frozen=True, slots=True)
class SkillEdge:
    """Одна из двух правок умения, открывающихся на третьем ранге.

    Грань меняет то, как умение себя ведёт, и никогда не добавляет кнопку.

    ``effect`` - то, что она меняет; объявляется в ``skills.toml`` и исполняется
    ``domain/rules/edges.py``. Именно он делает ``text`` правдой: долгое время обе
    грани каждого умения описывали словами собственное поведение, а движок выдавал
    всем одни и те же двадцать процентов силы или одну и ту же скидку.
    """

    code: str
    name: str
    text: str
    effect: EdgeEffect = field(default_factory=lambda: EdgeEffect())


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
    #: След, который оставляет это умение. Не назван - выводится из эффекта через
    #: ``skill_effects.tag_of``; содержимое называет его там, где эффект соврал бы, и
    #: там, где класс иначе никогда не увидел бы все три тега.
    tag: ActionTag | None = None
    #: Рода оружия, с которыми умение работает. Пусто - работает с любым и без
    #: оружия вовсе; выстрел без лука и удар в спину без кинжала - нет.
    weapon_types: tuple[str, ...] = ()
    #: Свои кости умения - то, что оно добавляет сверх броска оружия. Пусто -
    #: умение целиком стоит на оружии, и его ``power`` это доля от его броска.
    dice: Dice | None = None

    @property
    def owner(self) -> str:
        return f"{self.owner_kind.value}:{self.owner_id}"

    @property
    def is_active(self) -> bool:
        return self.kind is SkillKind.ACTIVE

    def power_at_rank(self, rank: int) -> float:
        """Сила растёт с рангом линейно; ранг 1 - это написанное значение."""
        return self.power * (1.0 + self.rank_step * (rank - 1))

    def edge(self, code: str) -> SkillEdge:
        for edge in self.edges:
            if edge.code == code:
                return edge
        msg = f"skill {self.code} has no edge {code}"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class RacePassive:
    """Always-on racial ability. It never occupies a slot.

    ``modifiers`` — то, что она делает. Долго здесь были только ``id``, ``name``
    и ``text``: шестнадцать способностей, названных вслух при создании
    персонажа, и ни одной, которую движок считал бы (``Roadmap.md``). Ключи —
    из того же словаря, что у особенностей, и проверяются по
    ``modifiers.EFFECTIVE_KEYS``: прибавка, которой никто не считает, — это не
    прибавка, а обещание (``Claude.md``, правило 7).
    """

    id: str
    name: str
    text: str
    modifiers: Mapping[str, float] = field(default_factory=dict)


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
    """Ресурс класса - доблесть, ярость, мана и так далее."""

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
    #: Одна строка о том, чем этот класс бьёт и что за этим ударом стоит. Её
    #: пишут в ``classes.toml``: экран умеет назвать ключевую характеристику, но
    #: не умеет объяснить, почему она ключевая.
    power: str
    key_stats: tuple[StatCode, ...]
    bonuses: StatBlock
    resource: ClassResource
    health: HealthCurve
    #: Рода оружия и доспеха, которые класс умеет носить. Пусто - умеет всё:
    #: содержимое переживает код, и класс, заведённый до этих списков, не должен
    #: оказаться голым.
    weapon_types: tuple[str, ...] = ()
    armor_types: tuple[str, ...] = ()

    def can_wield(self, weapon_type: str) -> bool:
        return not self.weapon_types or weapon_type in self.weapon_types

    def can_wear(self, armor_type: str) -> bool:
        return not self.armor_types or armor_type in self.armor_types


@dataclass(frozen=True, slots=True)
class Trait:
    """Черта персонажа, которая только прибавляет. Черта не даёт ни умений, ни кнопок."""

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
    """Вещь. Описания у неё нет: имя, род и числа - это всё, что она о себе знает.

    Вещи выпадают, куются и лежат на прилавке сотнями, и фраза «одна фраза о
    вещи» у каждой из них была бы либо выдумкой на месте, либо одной и той же
    фразой на сотню предметов. Что вещь такое, отвечают её род (``weapon_type``,
    ``armor_type``), слот и прибавки - и отвечают числом, а не настроением.
    """

    id: str
    name: str
    kind: ItemKind
    slot: str
    rarity: str
    level: int
    price: int
    modifiers: Mapping[str, float]
    skill_modifiers: Mapping[str, float]
    stack: int = 1
    effect: ItemEffect | None = None
    #: Урон оружия — числом в границах, а не долей чего-то. Пусто у всего, что не
    #: оружие.
    damage: Dice | None = None
    #: Броня — тоже число, и тоже своё. До этого доспех умел лишь множить броню,
    #: которая росла из выносливости, и не значил ничего.
    armor: int = 0
    #: Прибавки к характеристикам — числом. Их даёт редкость, а не вид вещи:
    #: обычная не даёт ни одной, необычная одну, остальные две.
    stat_bonuses: Mapping[str, int] = field(default_factory=dict)
    #: Что это за сырьё - "травы", "руда", "шкуры", "обломки". Пусто у всего,
    #: что сырьём не является, и у сырья, которое годится отовсюду.
    source: str = ""
    #: Род оружия - только у того, что надевается в руку.
    weapon_type: str = ""
    #: Род доспеха - только у того, что прикрывает тело, голову, руки или ноги.
    armor_type: str = ""

    @property
    def is_equipment(self) -> bool:
        return self.kind is ItemKind.EQUIPMENT

    @property
    def is_weapon(self) -> bool:
        return bool(self.weapon_type)

    @property
    def is_armor(self) -> bool:
        return bool(self.armor_type)


@dataclass(frozen=True, slots=True)
class EquipSlot:
    """Место, куда вещь надевается, и сколько брони с этого места вообще снимают.

    ``armor_share`` - доля от того, что даёт нагрудник: голова, руки и ноги
    прикрывают меньше, а оружие и украшение не прикрывают ничего. Без этой доли
    четыре мелких предмета одевали бы игрока лучше, чем латный доспех.
    """

    id: str
    name: str
    armor_share: float = 0.0


@dataclass(frozen=True, slots=True)
class WeaponType:
    """Род оружия: кинжал, меч, лук. Тем, кто чем дерётся, и решается, что ему доступно.

    ``damage`` - доля от стандартного удара. Единица - это голые руки: оружие
    бывает лучше их, но не бывает хуже, иначе кинжал оказался бы обузой. Всё
    остальное, чем один род отличается от другого, - ``modifiers``: кинжал даёт
    инициативу, топор - вес удара, и это записано числом, а не в названии.
    """

    id: str
    name: str
    #: Кости этого рода на первой ступени: среднее удара и форма броска. Границы
    #: удара задаёт не этот бросок, а ``spread`` (``entities/dice.py``).
    dice: Dice
    #: Размах: во сколько раз верхняя граница удара выше нижней. Это и есть
    #: характер рода — меч ровный, булава широкая, — и он не меняется от того,
    #: что вещь нашлась ступенью выше. Выше ``dice.MAX_SPREAD`` не поднимается
    #: никто: там кончается решение и начинается монетка.
    spread: float = MAX_SPREAD
    #: Род оружия — ещё и существительное, от которого строится имя вещи:
    #: «Крепкий меч», но «Крепкая булава». m, f, n или p (множественное).
    gender: str = "m"
    #: Каким родом урона бьёт это оружие: копьё колет, меч рубит, булава дробит.
    #: Отсюда его берёт обычный удар героя, и по нему считается сопротивление
    #: цели (``entities/damage.py``).
    damage_type: DamageType = DamageType.SLASHING
    modifiers: Mapping[str, float] = field(default_factory=dict)

    def damage_at(self, factor: float) -> Dice:
        """Кости этого рода на вещи, которая во столько раз крупнее первой."""
        return self.dice.scaled(factor, spread=self.spread)


@dataclass(frozen=True, slots=True)
class ArmorType:
    """Род доспеха: ткань, лёгкий, средний, тяжёлый.

    ``armor`` - доля брони относительно лёгкого доспеха того же уровня. Именно
    она и делает броню бронёй: до неё вся защита росла из одной выносливости, а
    надетое меняло её на проценты от почти нуля.
    """

    id: str
    name: str
    armor: float = 1.0
    modifiers: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GearTier:
    """Ступень снаряжения: с какого уровня и каким словом её называют.

    Ступеней двенадцать на все триста уровней, и вещь бывает только на ступени —
    промежуточных уровней у снаряжения нет. Это не упрощение, а то, что делает
    имя вещи именем: «Крепкий меч» — один и тот же меч у всех, кто его носит, а
    не одно название на двадцать разных мечей.
    """

    level: int
    #: Прилагательное в четырёх родах: m, f, n, p.
    names: Mapping[str, str]

    def named(self, gender: str) -> str:
        return self.names.get(gender, self.names.get("m", ""))


@dataclass(frozen=True, slots=True)
class SpecialProperty:
    """Особое свойство легендарной и реликтовой вещи: один ключ и его величина."""

    key: str
    value: float


@dataclass(frozen=True, slots=True)
class GearArchetype:
    """Вид снаряжения, из которого делается вещь: «меч», «кираса», «оберег».

    Сами вещи в ``content/`` не пишут. Их триста шестьдесят на двенадцати
    ступенях и пять редкостей у каждой — почти две тысячи; написанные руками, они
    были бы файлом, который никто не правит. Пишется вид, а вещь собирается из
    вида, ступени и редкости, как противник собирается из породы и уровня
    (``domain/procgen/items.py``).
    """

    id: str
    #: Существительное, с которого начинается имя: «меч», «кольчуга», «поножи».
    noun: str
    #: Род существительного: m, f, n, p. От него зависит прилагательное ступени.
    gender: str
    slot: str
    weapon_type: str = ""
    armor_type: str = ""


@dataclass(frozen=True, slots=True)
class Rarity:
    """Насколько вещь редка — и что редкость за собой несёт.

    Редкость это не цвет строчки: обычная вещь не даёт характеристик вовсе,
    необычная даёт одну, редкая две, легендарная две и особое свойство сверх них.
    Реликтовая даёт то же, что легендарная, но её числа считаются не от уровня
    вещи, а от уровня героя, — она растёт вместе с ним и потому не устаревает.
    """

    id: str
    name: str
    weight: int
    price_factor: float
    #: Сколько характеристик прибавляет вещь этой редкости.
    stats: int = 0
    #: Есть ли у вещи особое свойство сверх характеристик.
    special: bool = False
    #: Считать ли числа вещи от уровня героя, а не от уровня самой вещи.
    scaling: bool = False
    #: Чем имя вещи этой редкости отличается от обычной: «редкой работы».
    #: Без этого две вещи одного вида читались бы одной и той же строкой, а
    #: кнопки в списке различаются только текстом.
    mark: str = ""


@dataclass(frozen=True, slots=True)
class Npc:
    """Человек, который стоит в городе и с которым можно заговорить.

    Житель — не услуга и не лавка: он ничего не продаёт и ничем не торгует. Всё,
    что он делает, — стоит на своём месте, называет своё занятие и держит задания,
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
    """Один ответ в голосовании Палаты."""

    id: str
    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Turning:
    """Голосование: вопрос, который Палата задаёт игре, и ответы, между которыми
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
    # Можно ли здесь нападать друг на друга. По умолчанию нельзя нигде: место, где у
    # тебя могут забрать золото, обязано сказать об этом до того, как ты туда вошёл
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
        """Какая земля лежит вокруг этого города.

        У самого города биома нет: у него есть пять локаций, и биомы этих локаций
        решают, какое собирающее ремесло здесь работает (``domain/rules/crafts``).
        """
        return frozenset(location.biome for location in self.locations)


@dataclass(frozen=True, slots=True)
class ProgressionRules:
    """Опорные числа, общие для содержимого и правил."""

    max_character_level: int
    base_stat_value: int
    free_points_at_creation: int
    stat_points_per_level: int
    active_unlock_levels: tuple[int, ...]
    passive_unlock_levels: tuple[int, ...]
    active_slots: int
    racial_slots: int
    traits_at_creation: int
    max_rank: int
    edge_rank: int
    skill_point_per_level: int


@dataclass(frozen=True, slots=True)
class GameContent:
    """Всё неизменное, разложенное по указателям для доступа за O(1)."""

    races: tuple[Race, ...]
    classes: tuple[CharacterClass, ...]
    traits: tuple[Trait, ...]
    items: tuple[Item, ...]
    skills: tuple[Skill, ...]
    cities: tuple[City, ...]
    rarities: tuple[Rarity, ...]
    enemy_archetypes: tuple[EnemyArchetype, ...]
    elite_titles: tuple[str, ...]
    slots: tuple[EquipSlot, ...]
    weapon_types: tuple[WeaponType, ...]
    armor_types: tuple[ArmorType, ...]
    gear_tiers: tuple[GearTier, ...]
    gear_archetypes: tuple[GearArchetype, ...]
    special_properties: tuple[SpecialProperty, ...]
    quests: tuple[Quest, ...]
    crafts: tuple[Craft, ...]
    recipes: tuple[Recipe, ...]
    craft_rules: CraftRules
    trait_categories: Mapping[str, str]
    inverted_modifiers: frozenset[str]
    rules: ProgressionRules
    npcs: tuple[Npc, ...]
    turnings: tuple[Turning, ...]
    #: Какое голосование открыто сейчас. Пусто - Палата ничего не спрашивает.
    open_turning_id: str

    _races_by_id: Mapping[str, Race]
    _classes_by_id: Mapping[str, CharacterClass]
    _traits_by_id: Mapping[str, Trait]
    _items_by_id: Mapping[str, Item]
    _skills_by_code: Mapping[str, Skill]
    _skills_by_owner: Mapping[str, tuple[Skill, ...]]
    _cities_by_id: Mapping[str, City]
    _rarities_by_id: Mapping[str, Rarity]
    _slots_by_id: Mapping[str, EquipSlot]
    _gear_by_id: Mapping[str, GearArchetype]
    _weapon_types_by_id: Mapping[str, WeaponType]
    _armor_types_by_id: Mapping[str, ArmorType]
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
        slots: Sequence[EquipSlot] = (),
        weapon_types: Sequence[WeaponType] = (),
        armor_types: Sequence[ArmorType] = (),
        gear_tiers: Sequence[GearTier] = (),
        gear_archetypes: Sequence[GearArchetype] = (),
        special_properties: Sequence[SpecialProperty] = (),
        quests: Sequence[Quest] = (),
        crafts: Sequence[Craft] = (),
        recipes: Sequence[Recipe] = (),
        npcs: Sequence[Npc] = (),
        turnings: Sequence[Turning] = (),
        open_turning_id: str = "",
    ) -> GameContent:
        """Собрать реестр и его указатели."""
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
            slots=tuple(slots),
            weapon_types=tuple(weapon_types),
            armor_types=tuple(armor_types),
            gear_tiers=tuple(gear_tiers),
            gear_archetypes=tuple(gear_archetypes),
            special_properties=tuple(special_properties),
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
            _slots_by_id=MappingProxyType({slot.id: slot for slot in slots}),
            _gear_by_id=MappingProxyType({gear.id: gear for gear in gear_archetypes}),
            _weapon_types_by_id=MappingProxyType({kind.id: kind for kind in weapon_types}),
            _armor_types_by_id=MappingProxyType({kind.id: kind for kind in armor_types}),
            _quests_by_id=MappingProxyType({quest.id: quest for quest in quests}),
            _crafts_by_id=MappingProxyType({craft.id: craft for craft in crafts}),
            _recipes_by_id=MappingProxyType({recipe.id: recipe for recipe in recipes}),
            _npcs_by_id=MappingProxyType({npc.id: npc for npc in npcs}),
            _turnings_by_id=MappingProxyType({turning.id: turning for turning in turnings}),
        )

    # --- указатели ---------------------------------------------------

    def race(self, race_id: str) -> Race:
        return self._races_by_id[race_id]

    def has_race(self, race_id: str) -> bool:
        return race_id in self._races_by_id

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

    def has_rarity(self, rarity_id: str) -> bool:
        return rarity_id in self._rarities_by_id

    def gear_archetype(self, archetype_id: str) -> GearArchetype:
        return self._gear_by_id[archetype_id]

    def has_gear_archetype(self, archetype_id: str) -> bool:
        return archetype_id in self._gear_by_id

    def slot(self, slot_id: str) -> EquipSlot:
        return self._slots_by_id[slot_id]

    def has_slot(self, slot_id: str) -> bool:
        return slot_id in self._slots_by_id

    def weapon_type(self, type_id: str) -> WeaponType:
        return self._weapon_types_by_id[type_id]

    def has_weapon_type(self, type_id: str) -> bool:
        return type_id in self._weapon_types_by_id

    def armor_type(self, type_id: str) -> ArmorType:
        return self._armor_types_by_id[type_id]

    def has_armor_type(self, type_id: str) -> bool:
        return type_id in self._armor_types_by_id

    def quest(self, quest_id: str) -> Quest:
        return self._quests_by_id[quest_id]

    def has_quest(self, quest_id: str) -> bool:
        return quest_id in self._quests_by_id

    def quests_in(self, city_id: str) -> tuple[Quest, ...]:
        """Задания, которые выдаёт город, от простых к сложным."""
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
        """Задания, которые раздаёт этот житель, от лёгкого к тяжёлому."""
        return tuple(
            sorted(
                (quest for quest in self.quests if quest.giver_id == npc_id),
                key=lambda quest: (quest.level, quest.id),
            )
        )

    # --- голосования ------------------------------------------------------

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
        """Рецепты ремесла, от простых к сложным - в том порядке, в каком их перечисляет экран."""
        return tuple(
            sorted(
                (recipe for recipe in self.recipes if recipe.craft_id == craft_id),
                key=lambda recipe: (recipe.rank, recipe.id),
            )
        )

    def has_skill(self, code: str) -> bool:
        return code in self._skills_by_code

    def skills_of(self, owner: str, kind: SkillKind | None = None) -> tuple[Skill, ...]:
        """Умения, принадлежащие ``class:<id>`` или ``race:<id>``."""
        found = self._skills_by_owner.get(owner, ())
        if kind is None:
            return found
        return tuple(skill for skill in found if skill.kind is kind)

    def class_skills_up_to(self, class_id: str, level: int, kind: SkillKind) -> tuple[Skill, ...]:
        """Умения класса, открытые персонажу на уровне ``level``."""
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
        """Помогает ли персонажу ``value`` по этому ключу прибавки.

        Большинство ключей - «чем больше, тем лучше», но горстка (цены в лавке,
        полученный урон) наоборот. Знать, какой из них какой, нужно и экранам, и
        тестам баланса.
        """
        if modifier_key in self.inverted_modifiers:
            return value < 0
        return value > 0
