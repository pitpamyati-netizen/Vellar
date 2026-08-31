"""Бой: стороны, бойцы, очередь.

Бой в Велларе один на все случаи: узел локации, спуск, арена, поединок на
вольной земле, отряд против стаи и отряд против отряда - это одна и та же
сущность с разным составом сторон. Раньше их было две - «игрок» и «враги», - и
всё, что не укладывалось в одного игрока, укладывалось в подделку: чужой
персонаж приходил в бой слепком, потому что второго игрока движку было некуда
положить (ADR 0021).

Здесь второго игрока есть куда положить. :class:`Combatant` - это боец, и
разница между героем и волком в нём одна: за героем стоит персонаж, а за волком
- порода. Разница между живым игроком и слепком тоже одна: у живого ход ждёт
нажатия, за слепком ходит движок.

Всё неизменяемо: ход возвращает новое состояние и список того, что случилось.
События машинные, не словесные, - русские фразы пишет слой представления.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType

from mmorpg.domain.entities.damage import UNARMED, DamageType
from mmorpg.domain.entities.effects import EffectStack
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank
from mmorpg.domain.entities.statuses import StatusKind

#: Сколько бойцов помещается на одной стороне. Пятеро: столько же человек
#: помещается в отряд (``rules/party.MAX_MEMBERS``). Больше - и строй уже не
#: удержать в голове с одного прочтения (``docs/accessibility``).
MAX_SIDE = 5

#: Две стороны, и больше их не бывает: «все против всех» - это не бой, а
#: сообщение об ошибке, прочитанное вслух.
ATTACKERS = 0
DEFENDERS = 1


class BattleOutcome(StrEnum):
    """Чем кончился бой - для боя целиком, а не для одной его стороны."""

    ONGOING = "ongoing"
    #: Одна сторона повержена. Кто именно - в ``BattleState.winner``.
    DECIDED = "decided"
    #: Сторона вышла из боя: сбежала, сдалась или ушла со связи.
    FLED = "fled"
    #: Разошлись миром - умением, которое кончает бой разговором.
    AVOIDED = "avoided"

    @property
    def is_over(self) -> bool:
        return self is not BattleOutcome.ONGOING


class Verdict(StrEnum):
    """Чем бой кончился для одного участника. Это и читает его экран."""

    ONGOING = "ongoing"
    VICTORY = "victory"
    DEFEAT = "defeat"
    FLED = "fled"
    AVOIDED = "avoided"

    @property
    def is_over(self) -> bool:
        return self is not Verdict.ONGOING


class CombatantKind(StrEnum):
    """Кто это. За героем стоит персонаж, за противником - порода."""

    HERO = "hero"
    MONSTER = "monster"


class ActionKind(StrEnum):
    ATTACK = "attack"
    #: Закрыться: ход уходит целиком на оборону, зато чужой удар и находит реже,
    #: и стоит дешевле (``rules/combat.DEFEND_*``).
    DEFEND = "defend"
    SKILL = "skill"
    RACIAL = "racial"
    ITEM = "item"
    FLEE = "flee"
    #: Сменить цель. Ходом не считается: ничего не произошло
    #: (``Claude.md``, правило 3).
    FOCUS = "focus"
    #: Выйти из боя, признав поражение. Единственный способ кончить поединок,
    #: который бросили с той стороны: таймеров в игре нет, и ждать чужого
    #: нажатия можно бесконечно (ADR 0021).
    YIELD = "yield"


class ActionTag(StrEnum):
    """След, который оставляет ход: напор, заслон или финт.

    Три РАЗНЫХ вещи без круга контр (ADR 0050): напор - натиск (бьёшь сильнее, но
    открыт до своего следующего хода: удар по тебе мимо брони, твой ответ
    вполсилы), заслон - «Защититься» (двойная броня, урон вполовину), финт -
    объявленный породой не уклоняется. Русские слова для них пишет слой
    представления; член ``PRECISION`` читается как «финт».
    """

    PRESS = "press"
    GUARD = "guard"
    PRECISION = "precision"


TRACE_LENGTH = 3


@dataclass(frozen=True, slots=True)
class Trace:
    """Последние теги бойца, свежий - последним.

    Хранятся ``TRACE_LENGTH``: больше правила и не спрашивают. Повтор тега даёт
    разгон и усиливает удар.
    """

    tags: tuple[ActionTag, ...] = ()

    @property
    def last(self) -> ActionTag | None:
        return self.tags[-1] if self.tags else None

    @property
    def streak(self) -> int:
        """Сколько одинаковых тегов закрывают след."""
        count = 0
        for tag in reversed(self.tags):
            if tag is not self.last:
                break
            count += 1
        return count

    def push(self, tag: ActionTag) -> Trace:
        return Trace(tags=(*self.tags, tag)[-TRACE_LENGTH:])

    def breaks_with(self, tag: ActionTag) -> bool:
        """Станут ли последние три тега все разными, если добавить ``tag``.

        Это не контра (круга «тег X бьёт тег Y» больше нет, ADR 0050): это
        награда за непредсказуемость - три разных удара подряд, и противник не
        поспевает с ответом.
        """
        recent = (*self.tags[-(TRACE_LENGTH - 1) :], tag)
        return len(recent) == TRACE_LENGTH and len(set(recent)) == TRACE_LENGTH


class EventKind(StrEnum):
    DAMAGE = "damage"
    MISS = "miss"
    DODGE = "dodge"
    CRIT = "crit"
    HEAL = "heal"
    #: Барьер: раньше он назывался щитом и был просто числом на бойце.
    BARRIER = "barrier"
    EFFECT_APPLIED = "effect_applied"
    #: Состояние наложено, снято, кончилось.
    STATUS_APPLIED = "status_applied"
    STATUS_ENDED = "status_ended"
    CLEANSED = "cleansed"
    STUNNED = "stunned"
    #: Удар не дошёл вовсе: цель неуязвима.
    IMMUNE = "immune"
    #: Умением не воспользоваться: на бойце молчание.
    SILENCED = "silenced"
    RESOURCE = "resource"
    DEFEATED = "defeated"
    FLED = "fled"
    FLEE_FAILED = "flee_failed"
    YIELDED = "yielded"
    AVOIDED = "avoided"
    NOT_ENOUGH_RESOURCE = "not_enough_resource"
    ON_COOLDOWN = "on_cooldown"
    WRONG_WEAPON = "wrong_weapon"
    #: Умению нужна незаметность, а её нет (ADR 0050).
    NEEDS_STEALTH = "needs_stealth"
    EMPTY_SLOT = "empty_slot"
    NO_TARGET = "no_target"
    TURN_SKIPPED = "turn_skipped"
    MOMENTUM = "momentum"
    #: Брешь: враг объявил напор и открылся - удар по нему мимо брони.
    BREACH = "breach"
    #: Разнобой: три разных тега подряд, противник не поспел с ответом.
    BREAKTHROUGH = "breakthrough"
    ROUND = "round"


@dataclass(frozen=True, slots=True)
class BattleEvent:
    """Одно случившееся, в машинном виде.

    Имена лежат рядом с номерами нарочно: экран читает имя, а «вы» вместо имени
    ставит по номеру - в бою четверых иначе не разобрать, кого ударили.
    """

    kind: EventKind
    actor_id: int = 0
    target_id: int = 0
    actor: str = ""
    target: str = ""
    amount: int = 0
    skill_name: str = ""
    effect_name: str = ""
    turns: int = 0


@dataclass(frozen=True, slots=True)
class BattleAction:
    """Что боец решил сделать в свой ход."""

    kind: ActionKind
    slot: int | None = None
    item_id: str | None = None
    #: Кого бьём - номер бойца в этом бою. Ноль значит «того, на кого смотрю».
    target: int = 0


@dataclass(frozen=True, slots=True)
class Combatant:
    """Один боец: и герой, и волк, и чужой персонаж под управлением движка.

    ``live`` - ждёт ли ход нажатия. Живой игрок ждёт; слепок, стоящий за
    противником арены, - нет, за него ходит движок теми же правилами. Разница
    между ними только эта: слепок дерётся своим оружием и своими умениями, а не
    выдуманным числом урона, как было до ADR 0021.
    """

    id: int
    side: int
    kind: CombatantKind
    name: str
    level: int
    max_health: int
    health: int
    max_resource: int = 0
    resource: int = 0
    resource_name: str = ""
    initiative: float = 0.0
    live: bool = False
    #: Персонаж за героем: по нему движок читает умения, оружие и прибавки.
    character_id: int = 0
    #: Кому писать, когда наступит его ход. Ноль - писать некому.
    user_id: int = 0
    #: Порода за противником. У героя пусто.
    enemy: Enemy | None = None
    effects: EffectStack = field(default_factory=EffectStack)
    cooldowns: Mapping[str, int] = field(default_factory=dict)
    #: Сколько урона держит барьер. Само состояние - в ``effects``: барьер
    #: сгорает вместе с тем, кто его поставил (``StatusKind.BARRIER``).
    barrier: int = 0
    #: Чем бьёт этот боец без умения. У противника род урона лежит в породе, у
    #: героя - в оружии, и ставится при сборке бойца.
    damage_type: DamageType | None = None
    free_cast: bool = False
    evade_charges: int = 0
    trace: Trace = field(default_factory=Trace)
    #: На кого этот боец смотрит. Ноль - ни на кого пока.
    focus: int = 0
    #: Ушёл из боя сам: сбежал или сдался. Не то же, что пал.
    left: bool = False
    #: Его застали на замахе напора: удар по нему проходит мимо брони, а его
    #: собственный ближайший удар доходит вполсилы. Снимается в конце его же
    #: хода - брешь живёт ровно до ответа (ADR 0050).
    breached: bool = False

    @property
    def alive(self) -> bool:
        return self.health > 0 and not self.left

    @property
    def is_hero(self) -> bool:
        return self.kind is CombatantKind.HERO

    @property
    def rank(self) -> EnemyRank:
        """Ступень противника. Герой - обычная: приключенец не хозяин логова."""
        return self.enemy.rank if self.enemy is not None else EnemyRank.NORMAL

    @property
    def race_kind(self) -> str:
        """Порода для прибавок вроде «урон по нежити». Герой - гуманоид."""
        return self.enemy.kind.value if self.enemy is not None else EnemyKind.HUMANOID.value

    @property
    def element(self) -> DamageType:
        """Род урона этого бойца, когда он бьёт без умения."""
        if self.enemy is not None:
            return self.enemy.element
        return self.damage_type if self.damage_type is not None else UNARMED

    @property
    def controlled(self) -> StatusKind | None:
        """Состояние, отнимающее у бойца ход. ``None`` - ход за ним."""
        held = self.effects.control()
        return held.status if held is not None else None

    def cooldown_of(self, skill_code: str) -> int:
        return self.cooldowns.get(skill_code, 0)

    def with_cooldown(self, skill_code: str, turns: int) -> Combatant:
        cooldowns = {key: value for key, value in self.cooldowns.items() if value > 0}
        if turns > 0:
            cooldowns[skill_code] = turns
        return replace(self, cooldowns=MappingProxyType(cooldowns))

    def tick_cooldowns(self) -> Combatant:
        cooldowns = {key: value - 1 for key, value in self.cooldowns.items() if value - 1 > 0}
        return replace(self, cooldowns=MappingProxyType(cooldowns))

    def damaged(self, amount: int) -> tuple[Combatant, int]:
        """Урон идёт сперва в барьер. Второй член - сколько дошло до здоровья."""
        absorbed = min(self.barrier, amount)
        to_health = amount - absorbed
        return (
            replace(
                self,
                barrier=self.barrier - absorbed,
                health=max(0, self.health - to_health),
            ),
            to_health,
        )

    def healed(self, amount: int) -> tuple[Combatant, int]:
        restored = min(max(0, amount), self.max_health - self.health)
        return replace(self, health=self.health + restored), restored


@dataclass(frozen=True, slots=True)
class BattleState:
    """Бой целиком. Неизменяем: ход возвращает новый.

    ``order`` - очередь на этот круг, собранная по инициативе, ``cursor`` -
    место в ней. Очередь и есть то единственное, что инициатива делает: кто
    быстрее, тот бьёт раньше, и в бою четверых это решает больше, чем любая
    прибавка к урону (ADR 0021).
    """

    combatants: tuple[Combatant, ...]
    order: tuple[int, ...]
    cursor: int = 0
    round: int = 1
    outcome: BattleOutcome = BattleOutcome.ONGOING
    #: Сторона, за которой поле. ``-1`` - бой не кончен или кончен вничью.
    winner: int = -1
    events: tuple[BattleEvent, ...] = ()
    #: Что забирает победившая сторона у побеждённой стаи. У поединка платы нет:
    #: её считает ``domain/rules/pvp.py`` по кошелькам, а не по телам.
    experience: int = 0
    gold: int = 0
    loot: tuple[str, ...] = ()

    @property
    def is_over(self) -> bool:
        return self.outcome.is_over

    def by_id(self, combatant_id: int) -> Combatant | None:
        for one in self.combatants:
            if one.id == combatant_id:
                return one
        return None

    def side_of(self, combatant_id: int) -> int:
        one = self.by_id(combatant_id)
        return one.side if one is not None else -1

    @property
    def active(self) -> Combatant | None:
        """Чей сейчас ход. ``None`` - бой кончен или ходить некому."""
        if self.is_over or not self.order:
            return None
        return self.by_id(self.order[self.cursor % len(self.order)])

    @property
    def awaiting(self) -> Combatant | None:
        """Живой игрок, чьего нажатия ждёт бой. ``None`` - ждать некого."""
        current = self.active
        return current if current is not None and current.live else None

    def living(self, side: int | None = None) -> tuple[Combatant, ...]:
        return tuple(
            one for one in self.combatants if one.alive and (side is None or one.side == side)
        )

    def foes_of(self, combatant_id: int) -> tuple[Combatant, ...]:
        one = self.by_id(combatant_id)
        if one is None:
            return ()
        return tuple(other for other in self.living() if other.side != one.side)

    def visible_foes_of(self, combatant_id: int) -> tuple[Combatant, ...]:
        """Враги, которых этот боец может выбрать целью: без ушедших из виду.

        Отдельно от ``foes_of`` нарочно: на нём держится определение «сторона
        повержена», а спрятавшийся живой враг сторону не освобождает. Выбрать
        незаметного нельзя, но удар по всем его находит, и находит его же дот
        (``rules/combat``, ADR 0043).
        """
        return tuple(
            other
            for other in self.foes_of(combatant_id)
            if not other.effects.has(StatusKind.UNSEEN)
        )

    def allies_of(self, combatant_id: int, *, include_self: bool = True) -> tuple[Combatant, ...]:
        one = self.by_id(combatant_id)
        if one is None:
            return ()
        return tuple(
            other
            for other in self.living()
            if other.side == one.side and (include_self or other.id != one.id)
        )

    def heroes(self, side: int | None = None) -> tuple[Combatant, ...]:
        return tuple(
            one for one in self.combatants if one.is_hero and (side is None or one.side == side)
        )

    def target_for(self, combatant_id: int) -> Combatant | None:
        """На кого смотрит боец: выбранная цель, а если её нет - первый живой."""
        one = self.by_id(combatant_id)
        if one is None:
            return None
        chosen = self.by_id(one.focus)
        if (
            chosen is not None
            and chosen.alive
            and chosen.side != one.side
            and not chosen.effects.has(StatusKind.UNSEEN)
        ):
            return chosen
        foes = self.visible_foes_of(combatant_id)
        return foes[0] if foes else None

    def verdict_for(self, combatant_id: int) -> Verdict:
        """Чем бой кончился для этого бойца."""
        one = self.by_id(combatant_id)
        if one is None or not self.is_over:
            return Verdict.ONGOING
        match self.outcome:
            case BattleOutcome.AVOIDED:
                return Verdict.AVOIDED
            case BattleOutcome.FLED if one.left:
                return Verdict.FLED
            case _:
                if self.winner < 0:
                    return Verdict.FLED
                return Verdict.VICTORY if one.side == self.winner else Verdict.DEFEAT

    def with_events(self, *events: BattleEvent) -> BattleState:
        return replace(self, events=(*self.events, *events))

    def replace_combatant(self, updated: Combatant) -> BattleState:
        return replace(
            self,
            combatants=tuple(updated if one.id == updated.id else one for one in self.combatants),
        )
