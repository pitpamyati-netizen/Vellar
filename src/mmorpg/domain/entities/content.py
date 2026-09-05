"""Неизменное содержимое игры как объекты в памяти.

Всё здесь загружается один раз на старте из ``content/*.toml`` и на ходу не
меняется, поэтому каждый объект - ``frozen`` и ``slots``. Поиск идёт через
словарные указатели :class:`GameContent`, а это O(1).

Модуль - чистые данные плюс поиск: ни ввода-вывода, ни разбора. Разбор живёт в
``mmorpg.infrastructure.content.loader``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.craft import Craft, CraftKind, CraftRules, Recipe
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.dice import MAX_SPREAD, Dice
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import EnemyArchetype
from mmorpg.domain.entities.quest import Quest
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.entities.statuses import StatusKind


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
    cost: int = 0
    cooldown: int = 0
    target: str = "self"
    scaling: StatCode | None = None
    rank_step: float = 0.15
    #: Рода оружия, с которыми умение работает. Пусто - работает с любым и без
    #: оружия вовсе; выстрел без лука и удар в спину без кинжала - нет.
    weapon_types: tuple[str, ...] = ()
    #: Умение бьёт только из незаметности (``StatusKind.UNSEEN``). Отказ до хода,
    #: как не то оружие: ход не тратится, следа нет (ADR 0050).
    requires_stealth: bool = False
    #: Свои кости умения - то, что оно добавляет сверх броска оружия. Пусто -
    #: умение целиком стоит на оружии, и его ``power`` это доля от его броска.
    dice: Dice | None = None
    #: Развилка, в которой стоит это умение. Умения одной развилки открываются на
    #: одном уровне, а изучить из них можно только одно, пока не разберёшь взятое
    #: у наставника. Пусто - умение ни с чем не спорит.
    fork: str = ""

    @property
    def owner(self) -> str:
        return f"{self.owner_kind.value}:{self.owner_id}"

    @property
    def is_active(self) -> bool:
        return self.kind is SkillKind.ACTIVE

    def power_at_rank(self, rank: int) -> float:
        """Сила растёт с рангом линейно; ранг 1 - это написанное значение."""
        return self.power * (1.0 + self.rank_step * (rank - 1))


@dataclass(frozen=True, slots=True)
class RacePassive:
    """Расовая способность: работает всегда и слота не занимает.

    ``modifiers`` - то, что она делает. Ключи из того же словаря, что у
    особенностей, и проверяются по ``modifiers.EFFECTIVE_KEYS``: прибавка,
    которой никто не считает, - это обещание, а не прибавка (``Claude.md``,
    правило 7).
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
class HouseTechnique:
    """Фирменный приём дома: пассивный свёрток прибавок, как расовая способность.

    Активных умений и слотов не трогает. Ключи ``modifiers`` — из того же
    словаря, что у особенностей и рас, и проверяются по ``EFFECTIVE_KEYS``:
    прибавка, которой движок не считает, — обещание, а не механика (ADR 0049).
    """

    id: str
    name: str
    text: str
    modifiers: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class House:
    """Один из семи великих домов. ``seats`` — два города, которые он держит."""

    id: str
    name: str
    seats: tuple[str, ...]
    technique: HouseTechnique


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

    Вещи выпадают, куются и лежат на прилавке сотнями, и фраза у каждой была бы
    выдумкой на месте. Что вещь такое, отвечают её род (``weapon_type``,
    ``armor_type``), слот и прибавки - числом, а не настроением.
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
    #: Броня — тоже число, и тоже своё: доспех её даёт, а не множит выведенную из
    #: выносливости.
    armor: int = 0
    #: Прибавки к характеристикам — числом. Их даёт редкость, а не вид вещи:
    #: обычная не даёт ни одной, необычная одну, остальные больше.
    stat_bonuses: Mapping[str, int] = field(default_factory=dict)
    #: Ключи аффиксов, выпавших этой вещи великими: та же прибавка, взятая выше
    #: своего потолка (``procgen/items.GREAT_FACTOR``, ADR 0059). Редкая удача,
    #: и потому её называют на карточке вещи отдельной строкой.
    great: tuple[str, ...] = ()
    #: Что это за сырьё - "травы", "руда", "шкуры", "обломки". Пусто у всего,
    #: что сырьём не является, и у сырья, которое годится отовсюду.
    source: str = ""
    #: Род оружия - только у того, что надевается в руку.
    weapon_type: str = ""
    #: Род доспеха - только у того, что прикрывает тело, голову, руки или ноги.
    armor_type: str = ""
    #: Род инструмента - только у того, что надевается в слот «Инструмент».
    #: Инструментом берут сырьё в локации, и род решает, какое именно
    #: (``domain/rules/tools.py``, ADR 0056).
    tool_type: str = ""
    #: Сколько работы вещь выдержит: инструмент - сборов, снаряжение - боёв.
    #: Ноль у всего, что не носят: прочность в Vellar есть только у того, что
    #: стачивается о работу (ADR 0056, 0057).
    durability: int = 0

    @property
    def is_equipment(self) -> bool:
        return self.kind is ItemKind.EQUIPMENT

    @property
    def is_tool(self) -> bool:
        return bool(self.tool_type)

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
    """Род оружия: кинжал, меч, лук. Им и решается, что бойцу доступно.

    ``damage`` - доля от стандартного удара. Единица - это голые руки: оружие
    бывает лучше их, но не бывает хуже. Всё остальное, чем один род отличается
    от другого, - ``modifiers``: кинжал даёт инициативу, топор - вес удара.
    """

    id: str
    name: str
    #: Кости этого рода на первой ступени: среднее удара и форма броска. Границы
    #: удара задаёт не этот бросок, а ``spread`` (``entities/dice.py``).
    dice: Dice
    #: Размах: во сколько раз верхняя граница удара выше нижней. Это и есть
    #: характер рода — меч ровный, булава широкая, — и от ступени вещи он не
    #: зависит. Выше ``dice.MAX_SPREAD`` не поднимается никто.
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
    """Род доспеха: ткань, кожа, кольчуга, латы.

    ``armor`` - доля брони относительно кожаного доспеха того же уровня. Она и
    делает броню бронёй: иначе вся защита росла бы из одной выносливости.
    """

    id: str
    name: str
    armor: float = 1.0
    modifiers: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GearTier:
    """Ступень снаряжения: с какого уровня и каким словом её называют.

    Семнадцать ступеней на сто пятьдесят уровней, и вещь бывает только на
    ступени. Это то, что делает имя вещи именем: «Крепкий меч» - один и тот же
    меч у всех, кто его носит (ADR 0052).
    """

    level: int
    #: Прилагательное в четырёх родах: m, f, n, p.
    names: Mapping[str, str]

    def named(self, gender: str) -> str:
        return self.names.get(gender, self.names.get("m", ""))


@dataclass(frozen=True, slots=True)
class SpecialProperty:
    """Аффикс вещи: ключ, его опорная величина и слово, которым он зовётся.

    ``word`` входит в имя вещи, которая этот аффикс несёт: «Крепкий меч ярости
    редкой работы». Без него две вещи одного вида и одной редкости читались бы
    одной строкой, то есть на слух не различались бы вовсе (ADR 0059).
    """

    key: str
    value: float
    word: str = ""


@dataclass(frozen=True, slots=True)
class GearArchetype:
    """Вид снаряжения, из которого делается вещь: «меч», «кираса», «оберег».

    Сами вещи в ``content/`` не пишут: видов под сотню, ступеней семнадцать,
    редкостей пять. Пишется вид, а вещь собирается из вида, ступени, редкости и
    оттиска, как противник собирается из породы и уровня
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
    tool_type: str = ""


@dataclass(frozen=True, slots=True)
class ToolType:
    """Род инструмента: кирка, серп, нож свежевателя.

    Инструмент не дерётся и не прикрывает: он решает, что игрок вообще может
    взять руками в локации. ``sources`` - то сырьё, которое им берут («руда»,
    «травы», «шкуры», «обломки», ``Item.source``), а ``craft`` - ремесло, в
    котором эта работа записывается (ADR 0056).
    """

    id: str
    name: str
    craft: str
    sources: tuple[str, ...] = ()

    def takes(self, source: str) -> bool:
        """Берётся ли этим инструментом такое сырьё. Пустое сырьё берут любым."""
        return not source or source in self.sources


@dataclass(frozen=True, slots=True)
class Rarity:
    """Насколько вещь редка — и что редкость за собой несёт.

    Редкость решает, сколько у вещи аффиксов: обычная не несёт ни одного,
    необычная один, редкая два, легендарная три, реликтовая четыре, и у двух
    старших один из аффиксов - особое свойство в полную силу (ADR 0059).
    Числа реликтовой считаются от уровня героя, а не от уровня вещи, поэтому
    она растёт вместе с ним.
    """

    id: str
    name: str
    weight: int
    price_factor: float
    #: Сколько аффиксов несёт вещь этой редкости.
    affixes: int = 0
    #: Есть ли среди них особое свойство - аффикс полной силы.
    special: bool = False
    #: Считать ли числа вещи от уровня героя, а не от уровня самой вещи.
    scaling: bool = False
    #: Чем имя вещи этой редкости отличается от обычной: «редкой работы». Кнопки
    #: в списке различаются только текстом.
    mark: str = ""
    #: Сколько сборов держит инструмент этой редкости. Больше редкость ничего
    #: инструменту не даёт: он не бьёт, не прикрывает и не прибавляет
    #: характеристик (ADR 0056).
    durability: int = 0
    #: Во сколько раз прочнее обычного снаряжение этой редкости. Прочность
    #: считается от уровня вещи, а редкость её только множит (ADR 0057).
    toughness: float = 1.0


@dataclass(frozen=True, slots=True)
class Npc:
    """Человек, который стоит в городе и с которым можно заговорить.

    Житель ничем не торгует: он стоит на своём месте, называет своё занятие и
    держит задания, которые сам же и раздаёт (``domain/rules/overlay.py``).
    Приходит он только от смотрителя - в ``content/`` жителей нет.
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
    """Один ответ в голосовании Большого совета."""

    id: str
    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Turning:
    """Голосование Большого совета: вопрос и ответы, между которыми считают голоса.

    Сам вопрос ничего не решает в правилах - он собирает счёт. Итог виден на
    экране и уходит в канал, а числа правит тот, кто считает итог цикла
    (``docs/endgame.md``).
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
class Dungeon:
    """Одно названное подземелье города (ADR 0041).

    У города список: несколько обычных подземелий вразброс по его полосе и одно
    глубокое (``deep``) на ``city.level_max``, открытое тому, кто добрался до
    последней локации. Уровень у каждого свой и с игроком не растёт: подземелье -
    это место, а не его зеркало (ADR 0019). ``biome`` - из dungeon-ростера
    (``content/enemies.toml``, ``dungeon = true``).
    """

    id: str
    name: str
    flavour: str
    biome: str
    level: int
    deep: bool = False
    #: С какого уровня открыт. Ноль - открыт вместе с городом. Глубокое
    #: подземелье живёт по своему правилу (последняя локация города).
    unlock_level: int = 0


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
    dungeons: tuple[Dungeon, ...]

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

    def dungeon(self, dungeon_id: str) -> Dungeon:
        for one in self.dungeons:
            if one.id == dungeon_id:
                return one
        msg = f"city {self.id} has no dungeon {dungeon_id!r}"
        raise KeyError(msg)

    def has_dungeon(self, dungeon_id: str) -> bool:
        return any(one.id == dungeon_id for one in self.dungeons)

    @property
    def regular_dungeons(self) -> tuple[Dungeon, ...]:
        """Все подземелья, кроме глубокого."""
        return tuple(one for one in self.dungeons if not one.deep)

    @property
    def deep_dungeon(self) -> Dungeon:
        """Глубокое подземелье города - единственное с ``deep = true``."""
        for one in self.dungeons:
            if one.deep:
                return one
        msg = f"city {self.id} has no deep dungeon"
        raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class EnemyAffix:
    """Прозвище-модификатор противника (ADR 0042).

    Прозвище - прилагательное перед именем породы («Иглистый ползун из штрека»).
    Множители запекаются в числа при сборке, как ``Enemy.stakes``. Механика
    делится надвое: ``modifiers`` (ключи из ``modifiers.EFFECTIVE_KEYS``)
    навешиваются эффектом на бойца в начале боя, а ``on_hit_status`` движок
    вешает на цель после состоявшегося удара породы.
    """

    id: str
    adjective: str
    weight: int = 1
    health: float = 1.0
    damage: float = 1.0
    armor: float = 1.0
    initiative: float = 1.0
    gold: float = 1.0
    #: Сколько лишних тел прибавить к стае. «Выводковый» так делает вылазку
    #: гуще, не трогая силу одного противника.
    pack_bonus: int = 0
    modifiers: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    on_hit_status: StatusKind | None = None
    on_hit_turns: int = 0
    on_hit_chance: float = 0.0
    #: Величина навешиваемого состояния. Для яда - урон за ход, для слабости -
    #: проценты урона, для замедления - проценты инициативы (``entities/statuses``).
    on_hit_magnitude: float = 0.0
    #: Прячется: стая с этим прозвищем начинает бой незаметной и уходит из виду
    #: снова через столько своих ходов после того, как её выдали (``rules/combat``,
    #: ADR 0043). Ноль - обычная стая.
    recloak: int = 0

    def effect(self) -> ActiveEffect | None:
        """Постоянная прибавка на весь бой - или ``None``, если механика в ударе."""
        if not self.modifiers:
            return None
        return ActiveEffect(
            id=f"affix:{self.id}",
            name=self.adjective,
            modifiers=MappingProxyType(dict(self.modifiers)),
            turns_left=1,
            beneficial=True,
            permanent=True,
        )


@dataclass(frozen=True, slots=True)
class ProgressionRules:
    """Опорные числа, общие для содержимого и правил."""

    max_character_level: int
    base_stat_value: int
    free_points_at_creation: int
    stat_points_per_level: int
    #: Сколько каждая из семи характеристик растёт сама по себе за уровень: без
    #: этого розданных очков хватало бы на две, а остальные пять стояли бы на
    #: пятёрке создания до потолка полосы (ADR 0058). Рост общий для всех классов;
    #: чем персонаж отличается от соседа, решают розданные очки.
    stat_growth_per_level: float
    active_unlock_levels: tuple[int, ...]
    passive_unlock_levels: tuple[int, ...]
    #: Уровни, на которых боевое умение приходит развилкой: два умения на одно
    #: место, берётся одно. Общие для всех классов, чтобы «на семьдесят пятом
    #: будет выбор» было правдой независимо от того, кем играешь.
    fork_levels: tuple[int, ...]
    active_slots: int
    racial_slots: int
    traits_at_creation: int
    max_rank: int
    #: Через сколько уровней приходит очко умений. Два: очко за каждый уровень
    #: раздавало больше, чем дерево стоит, и «что взять» переставало быть
    #: вопросом (ADR 0067).
    levels_per_skill_point: int
    #: Чего стоит ранг умения. Одно число на все ранги: ранг платит тем, что
    #: умение делает, а не тем, чего он стоит (ADR 0067).
    rank_cost: int

    def innate_stat_value(self, level: int) -> int:
        """Что каждая характеристика имеет на этом уровне до всяких прибавок."""
        return self.base_stat_value + round(self.stat_growth_per_level * max(0, level - 1))

    def full_rank_cost(self) -> int:
        """Во что обходится одно умение, поднятое до предела."""
        return self.rank_cost * self.max_rank

    def skill_points_at(self, level: int) -> int:
        """Сколько очков умений выдано к этому уровню - всего, с первого.

        Очко приходит через уровень, поэтому счёт ведётся делением, а не
        умножением: иначе «каждые два» было бы правдой только в одну сторону.
        """
        return max(0, level) // max(1, self.levels_per_skill_point)


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
    affixes: tuple[EnemyAffix, ...]
    slots: tuple[EquipSlot, ...]
    weapon_types: tuple[WeaponType, ...]
    armor_types: tuple[ArmorType, ...]
    tool_types: tuple[ToolType, ...]
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
    houses: tuple[House, ...]
    #: Какое голосование открыто сейчас. Пусто - совет ничего не спрашивает.
    open_turning_id: str

    _races_by_id: Mapping[str, Race]
    _classes_by_id: Mapping[str, CharacterClass]
    _traits_by_id: Mapping[str, Trait]
    _items_by_id: Mapping[str, Item]
    _skills_by_code: Mapping[str, Skill]
    _skills_by_owner: Mapping[str, tuple[Skill, ...]]
    _cities_by_id: Mapping[str, City]
    _affixes_by_id: Mapping[str, EnemyAffix]
    _rarities_by_id: Mapping[str, Rarity]
    _slots_by_id: Mapping[str, EquipSlot]
    _gear_by_id: Mapping[str, GearArchetype]
    _weapon_types_by_id: Mapping[str, WeaponType]
    _armor_types_by_id: Mapping[str, ArmorType]
    _tool_types_by_id: Mapping[str, ToolType]
    _quests_by_id: Mapping[str, Quest]
    _crafts_by_id: Mapping[str, Craft]
    _recipes_by_id: Mapping[str, Recipe]
    _npcs_by_id: Mapping[str, Npc]
    _turnings_by_id: Mapping[str, Turning]
    _houses_by_id: Mapping[str, House]
    _house_by_city: Mapping[str, House]

    #: Каким словом вещь называет прибавку к характеристике: «меч силача».
    #: Объявлено в ``items.toml [meta].stat_words`` рядом с прочими аффиксами.
    stat_words: Mapping[str, str] = MappingProxyType({})

    #: Чем собрать вещь, которой нет в реестре. Снаряжение собирается из вида,
    #: ступени, редкости и оттиска (``procgen/items.py``), и оттисков у одной вещи
    #: дюжина: реестр держит эталоны, остальное собирается по имени, когда о нём
    #: спросили. Домен сборщика не импортирует - его подаёт загрузчик.
    _assemble: Callable[[GameContent, str], Item | None] | None = None
    #: Собранное по дороге. Кэш, а не хранилище: та же вещь собралась бы заново
    #: с тем же результатом - сборка определена её именем (ADR 0059).
    _forged: dict[str, Item] = field(default_factory=dict)

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
        affixes: Sequence[EnemyAffix] = (),
        inverted_modifiers: frozenset[str],
        rules: ProgressionRules,
        craft_rules: CraftRules,
        slots: Sequence[EquipSlot] = (),
        weapon_types: Sequence[WeaponType] = (),
        armor_types: Sequence[ArmorType] = (),
        tool_types: Sequence[ToolType] = (),
        gear_tiers: Sequence[GearTier] = (),
        gear_archetypes: Sequence[GearArchetype] = (),
        special_properties: Sequence[SpecialProperty] = (),
        quests: Sequence[Quest] = (),
        crafts: Sequence[Craft] = (),
        recipes: Sequence[Recipe] = (),
        npcs: Sequence[Npc] = (),
        turnings: Sequence[Turning] = (),
        houses: Sequence[House] = (),
        open_turning_id: str = "",
        stat_words: Mapping[str, str] | None = None,
        assemble: Callable[[GameContent, str], Item | None] | None = None,
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
            affixes=tuple(affixes),
            slots=tuple(slots),
            weapon_types=tuple(weapon_types),
            armor_types=tuple(armor_types),
            tool_types=tuple(tool_types),
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
            houses=tuple(houses),
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
            _affixes_by_id=MappingProxyType({affix.id: affix for affix in affixes}),
            _rarities_by_id=MappingProxyType({rarity.id: rarity for rarity in rarities}),
            _slots_by_id=MappingProxyType({slot.id: slot for slot in slots}),
            _gear_by_id=MappingProxyType({gear.id: gear for gear in gear_archetypes}),
            _weapon_types_by_id=MappingProxyType({kind.id: kind for kind in weapon_types}),
            _armor_types_by_id=MappingProxyType({kind.id: kind for kind in armor_types}),
            _tool_types_by_id=MappingProxyType({kind.id: kind for kind in tool_types}),
            _quests_by_id=MappingProxyType({quest.id: quest for quest in quests}),
            _crafts_by_id=MappingProxyType({craft.id: craft for craft in crafts}),
            _recipes_by_id=MappingProxyType({recipe.id: recipe for recipe in recipes}),
            _npcs_by_id=MappingProxyType({npc.id: npc for npc in npcs}),
            _turnings_by_id=MappingProxyType({turning.id: turning for turning in turnings}),
            _houses_by_id=MappingProxyType({house.id: house for house in houses}),
            _house_by_city=MappingProxyType(
                {city_id: house for house in houses for city_id in house.seats}
            ),
            stat_words=MappingProxyType(dict(stat_words or {})),
            _assemble=assemble,
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

    def has_trait(self, trait_id: str) -> bool:
        return trait_id in self._traits_by_id

    def item(self, item_id: str) -> Item:
        """Вещь по имени - написанная, собранная эталоном или собранная сейчас.

        Реестр держит эталоны снаряжения (оттиск ноль), а вещь с другим оттиском
        собирается по требованию и запоминается: одна и та же строка всегда даёт
        одну и ту же вещь, поэтому кэш здесь - только про скорость (ADR 0059).
        """
        found = self._items_by_id.get(item_id) or self._forged.get(item_id)
        if found is not None:
            return found
        forged = self._assemble(self, item_id) if self._assemble is not None else None
        if forged is None:
            raise KeyError(item_id)
        self._forged[item_id] = forged
        return forged

    def has_item(self, item_id: str) -> bool:
        if item_id in self._items_by_id or item_id in self._forged:
            return True
        try:
            self.item(item_id)
        except KeyError:
            return False
        return True

    def skill(self, code: str) -> Skill:
        return self._skills_by_code[code]

    def city(self, city_id: str) -> City:
        return self._cities_by_id[city_id]

    def has_city(self, city_id: str) -> bool:
        return city_id in self._cities_by_id

    def affix(self, affix_id: str) -> EnemyAffix:
        return self._affixes_by_id[affix_id]

    def has_affix(self, affix_id: str) -> bool:
        return affix_id in self._affixes_by_id

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

    def tool_type(self, type_id: str) -> ToolType:
        return self._tool_types_by_id[type_id]

    def has_tool_type(self, type_id: str) -> bool:
        return type_id in self._tool_types_by_id

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

    # --- дома ------------------------------------------------------------

    def house(self, house_id: str) -> House:
        return self._houses_by_id[house_id]

    def has_house(self, house_id: str) -> bool:
        return house_id in self._houses_by_id

    def house_of_city(self, city_id: str) -> House | None:
        """Дом, который держит этот город, или ``None`` (Гнездно — ничей)."""
        return self._house_by_city.get(city_id)

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
        полученный урон) наоборот, и знать это нужно и экранам, и тестам баланса.
        """
        if modifier_key in self.inverted_modifiers:
            return value < 0
        return value > 0
