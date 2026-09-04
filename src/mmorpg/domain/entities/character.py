"""Персонаж и то, что у него действительно хранится.

Здесь живут только *сырые* значения: розданные очки характеристик, уровень,
опыт, выбранные черты, набор умений и снаряжение. Итоги - здоровье, броня, урон
- не хранятся никогда: их пересчитывает из этих сырых значений
``mmorpg.domain.rules.stats``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from mmorpg.domain.entities.craft import CraftLog
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatBlock

ACTIVE_SLOTS = 6


@dataclass(frozen=True, slots=True)
class SkillLoadout:
    """Что игрок положил в постоянную панель.

    ``actives`` всегда содержит 6 записей; пустой слот — это ``None``, и он держит
    свой номер, поэтому умение не меняет положения.

    Постоянных слотов здесь нет. Постоянное умение нечем нажать, у него нет ни
    хода, ни цели, и «поместить его в слот» означало только одно: три из шести
    изученных не работали, а игрок платил очки за надпись. Изучено - значит
    работает (``domain/rules/modifiers.passive_modifiers``).
    """

    actives: tuple[str | None, ...] = (None,) * ACTIVE_SLOTS
    racial: str | None = None
    ranks: Mapping[str, int] = field(default_factory=dict)
    edges: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.actives) != ACTIVE_SLOTS:
            msg = f"the panel has exactly {ACTIVE_SLOTS} active slots"
            raise ValueError(msg)
        # Умение, лежащее в панели, - это умение, которое персонаж знает. Без этого
        # стартовое умение дралось как ранг первый, а экран умений всё предлагал изучить
        # его за очко: одно и то же умение, разом изученное и нет. Приведение к норме
        # здесь чинит и персонажей, сохранённых прежними выпусками.
        in_panel = self.equipped_actives()
        if self.racial is not None:
            in_panel = (*in_panel, self.racial)
        missing = [code for code in in_panel if code not in self.ranks]
        if missing:
            ranks = dict(self.ranks)
            for code in missing:
                ranks[code] = 1
            object.__setattr__(self, "ranks", MappingProxyType(ranks))

    def rank_of(self, skill_code: str) -> int:
        """Ранг умения; единица, как только умение вообще изучено."""
        return self.ranks.get(skill_code, 1)

    def edge_of(self, skill_code: str) -> str | None:
        return self.edges.get(skill_code)

    def equipped_actives(self) -> tuple[str, ...]:
        return tuple(code for code in self.actives if code is not None)

    def with_active(self, slot: int, skill_code: str | None) -> SkillLoadout:
        actives = list(self.actives)
        actives[slot] = skill_code
        return replace(self, actives=tuple(actives))

    def with_rank(self, skill_code: str, rank: int) -> SkillLoadout:
        ranks = dict(self.ranks)
        ranks[skill_code] = rank
        return replace(self, ranks=MappingProxyType(ranks))

    def with_edge(self, skill_code: str, edge_code: str) -> SkillLoadout:
        edges = dict(self.edges)
        edges[skill_code] = edge_code
        return replace(self, edges=MappingProxyType(edges))


@dataclass(frozen=True, slots=True)
class Equipment:
    """Идентификаторы вещей по слотам. Пустого слота просто нет."""

    items: Mapping[str, str] = field(default_factory=dict)

    def item_in(self, slot: str) -> str | None:
        return self.items.get(slot)

    def equip(self, slot: str, item_id: str) -> Equipment:
        return Equipment(MappingProxyType({**self.items, slot: item_id}))

    def unequip(self, slot: str) -> Equipment:
        remaining = {key: value for key, value in self.items.items() if key != slot}
        return Equipment(MappingProxyType(remaining))

    def item_ids(self) -> tuple[str, ...]:
        return tuple(self.items.values())


@dataclass(frozen=True, slots=True)
class ToolWear:
    """Сколько работы каждый инструмент уже отработал.

    Прочность считается по имени вещи, а не по образцу: сумка Vellar держит
    идентификаторы и число, а не сами предметы, поэтому две одинаковые кирки -
    это одна и та же кирка дважды (``entities/craft.QualityTier`` держится того
    же правила). Сточенный инструмент исчезает, и запись о нём уходит вместе с
    ним (ADR 0056).
    """

    used: Mapping[str, int] = field(default_factory=dict)

    def spent(self, item_id: str) -> int:
        return self.used.get(item_id, 0)

    def worn(self, item_id: str, amount: int = 1) -> ToolWear:
        return ToolWear(
            MappingProxyType({**self.used, item_id: self.spent(item_id) + max(0, amount)})
        )

    def cleared(self, item_id: str) -> ToolWear:
        return ToolWear(
            MappingProxyType({key: value for key, value in self.used.items() if key != item_id})
        )


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    item_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Character:
    """Персонаж игрока. Всё здесь сохраняется как есть."""

    id: int
    user_id: int
    name: str
    race_id: str
    class_id: str
    level: int = 1
    experience: int = 0
    gold: int = 0
    allocated: StatBlock = field(default_factory=StatBlock)
    trait_ids: tuple[str, ...] = ()
    loadout: SkillLoadout = field(default_factory=SkillLoadout)
    equipment: Equipment = field(default_factory=Equipment)
    city_id: str = "farhold"
    unspent_stat_points: int = 0
    unspent_skill_points: int = 0
    # Раны переживают бой: игрок уходит из локации таким, каким вышел из последнего
    # узла, и платит за то, чтобы его залатали. Ноль значит «как новенький» - именно
    # таков только что созданный персонаж, см. ``health_or``.
    health: int = 0
    bank_gold: int = 0
    quests: QuestLog = field(default_factory=QuestLog)
    # Работа, уже сделанная в ремесле. Ранг не хранится никогда - его отсчитывает
    # обратно от этого опыта ``mmorpg.domain.rules.crafts``.
    crafts: CraftLog = field(default_factory=CraftLog)
    # Сколько сточено у каждого инструмента. Хранится, потому что износ - это не
    # производное: его нельзя пересчитать ни из чего (``domain/rules/tools.py``).
    tools: ToolWear = field(default_factory=ToolWear)
    # Что помнит арена: два счётчика для таблицы сезона и золото, которое она с тебя
    # держит. Победа платится из этого залога и никогда из ниоткуда - это и не даёт
    # арене печатать золото (``domain/rules/arena.py``).
    arena_wins: int = 0
    arena_losses: int = 0
    arena_credit: int = 0
    # В каком великом доме состоит игрок (``domain/rules/houses.py``, ADR 0049).
    # Пусто - ни в каком. Даёт доступ к технике дома; уход под новое имя не трогает.
    house_id: str = ""
    # Конец пути (``domain/rules/turning.py``). ``remorts`` - сколько раз игрок
    # брал у Престола новое имя: каждый уход сбрасывал уровень до первого и оставлял
    # нажитое. ``turning_cycle``/``turning_answer`` - ответ, который он дал на
    # открытый вопрос Большого совета, и на какой именно вопрос.
    remorts: int = 0
    turning_cycle: str = ""
    turning_answer: str = ""
    # Какие шаги обучения уже позади, битовой маской (``mmorpg.domain.rules.tutorial``).
    # Ноль - игрок, который только что пришёл.
    tutorial: int = 0
    # Смотритель игры, а не персонаж посильнее: флаг говорит лишь о том, что экран
    # смотрителя открывается ему. Кто смотритель, решает ADMIN_IDS в окружении; эта
    # колонка повторяет то решение, чтобы экраны читали его без объекта настроек.
    is_admin: bool = False

    def health_or(self, maximum: int) -> int:
        """Текущее здоровье, зажатое в границы, которые допускают нынешние итоги.

        Снаряжение и уровни двигают максимум между боями, поэтому сохранённое число -
        всегда лишь заявка: ему верят до максимума и никогда ниже единицы, потому что
        персонажем на нуле играть невозможно.
        """
        if self.health <= 0:
            return maximum
        return max(1, min(self.health, maximum))

    def with_health(self, health: int, maximum: int) -> Character:
        return replace(self, health=max(1, min(health, maximum)))

    def with_experience(self, gained: int) -> Character:
        return replace(self, experience=self.experience + gained)

    def with_level(self, level: int, *, stat_points: int, skill_points: int) -> Character:
        return replace(
            self,
            level=level,
            unspent_stat_points=self.unspent_stat_points + stat_points,
            unspent_skill_points=self.unspent_skill_points + skill_points,
        )

    def with_gold(self, delta: int) -> Character:
        return replace(self, gold=max(0, self.gold + delta))

    def with_crafts(self, crafts: CraftLog) -> Character:
        return replace(self, crafts=crafts)

    def with_arena_result(self, *, won: bool) -> Character:
        if won:
            return replace(self, arena_wins=self.arena_wins + 1)
        return replace(self, arena_losses=self.arena_losses + 1)

    def with_arena_credit(self, held: int) -> Character:
        """Записать, сколько с тебя держит арена. Никогда не меньше нуля."""
        return replace(self, arena_credit=max(0, held))

    def with_house(self, house_id: str) -> Character:
        """Вступить в дом или (пустой ``house_id``) выйти из него."""
        return replace(self, house_id=house_id)

    def with_turning_answer(self, cycle: str, option: str) -> Character:
        """Ответить на голосование. Ответ всегда назван вместе с вопросом:
        голос, поданный за прошлый цикл, в этом не считается."""
        return replace(self, turning_cycle=cycle, turning_answer=option)

    def as_admin(self, is_admin: bool) -> Character:
        return replace(self, is_admin=is_admin)
