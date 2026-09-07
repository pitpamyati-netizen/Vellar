"""Гильдия: объединение надолго, которое растёт от того, что делают её люди.

Отряд собирают ради одного боя (``domain/rules/party.py``); гильдия - другое.
Она держится месяцами, в ней десятки человек, у каждого звание, и у неё есть
общий кошелёк, из которого берут по званию, а не по тому, кто первым дошёл.

Гильдия **растёт деяниями** (ADR 0076). Выигранный её человеком бой с миром -
деяние; внесённое в казну золото - столько деяний, сколько это боёв его уровня
(``procgen/enemies.gold_at``, ``Claude.md``, правило 3). Набранные деяния
поднимают гильдию на следующую ступень (``content/guilds.toml``), а ступень -
это всё, что гильдия даёт: сколько человек в неё помещается и что она добавляет
своим за выигранный бой. Ничего из этого не хранится дважды: ступень считается
из деяний, вместимость - из ступени.

Пять званий, и у каждого свои права:

- **новик** - только что позванный. Кладёт в казну, но не берёт ничего.
- **участник** - берёт из казны понемногу.
- **ветеран** - зовёт новых и берёт заметно больше.
- **старейшина** - зовёт, выгоняет тех, кто ниже, и берёт много.
- **основатель** - один. Раздаёт звания, распускает гильдию, передаёт её
  другому и берёт из казны без предела.

Предел выемки - **за переворот прилавка** и в боях своего уровня, а не числом:
казна, из которой один человек за раз выносит всё, - это не общая казна. Само
взятое считает ``application/services/guild.py``; здесь только правила: кто что
вправе сделать и почему нельзя, если нельзя.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import IntEnum

from mmorpg.domain.entities.content import GameContent, GuildTier
from mmorpg.domain.procgen.enemies import gold_at
from mmorpg.domain.rules.progression import MAX_LEVEL

#: Сколько человек помещается в гильдию на самой высокой её ступени. Ступень
#: ниже вмещает меньше (``content/guilds.toml``), выше этого - никакая.
MAX_MEMBERS = 30

#: С какого уровня можно завести гильдию и сколько это стоит золотом.
FOUND_LEVEL = 10
FOUND_COST = 500

#: Границы имени гильдии: не короче трёх букв, не длиннее двадцати четырёх.
NAME_MIN = 3
NAME_MAX = 24

#: Сколько деяний записывает гильдии один выигранный её человеком бой с миром.
#: Единица нарочно: деяние не меряется уровнем противника, иначе гильдия
#: полутораста уровня росла бы в сотни раз быстрее той, что ходит с новичками.
DEEDS_PER_FIGHT = 1


class GuildRank(IntEnum):
    """Звание в гильдии. Больше значение - больше прав."""

    RECRUIT = 0
    MEMBER = 1
    VETERAN = 2
    ELDER = 3
    FOUNDER = 4

    @property
    def title(self) -> str:
        return {
            GuildRank.RECRUIT: "новик",
            GuildRank.MEMBER: "участник",
            GuildRank.VETERAN: "ветеран",
            GuildRank.ELDER: "старейшина",
            GuildRank.FOUNDER: "основатель",
        }[self]


#: Звания, которые раздаёт основатель. Своего в этом списке нет: второго
#: основателя не бывает, а гильдию передают целиком (:func:`succeed_refusal`).
GRANTABLE: tuple[GuildRank, ...] = (
    GuildRank.RECRUIT,
    GuildRank.MEMBER,
    GuildRank.VETERAN,
    GuildRank.ELDER,
)

#: С какого звания зовут новых и с какого выгоняют.
INVITE_RANK = GuildRank.VETERAN
KICK_RANK = GuildRank.ELDER

#: Сколько боёв своего уровня звание выносит из казны за переворот прилавка.
#: Основателя здесь нет: он берёт без предела - он же за казну и отвечает.
#: Числа заметные нарочно: предел, который меньше самой мелкой кнопки казны, -
#: это не предел, а кнопка, которая всегда отказывает (``Claude.md``, правило 9).
WITHDRAW_FIGHTS: dict[GuildRank, int] = {
    GuildRank.RECRUIT: 0,
    GuildRank.MEMBER: 25,
    GuildRank.VETERAN: 75,
    GuildRank.ELDER: 200,
}


#: Сколько вещей за переворот прилавка выносит из хранилища каждое звание
#: (ADR 0077). Считается штуками, а не видами: стопка в двести стрел - это
#: двести вещей, и вынести её целиком не то же самое, что вынести один меч.
#: Основателя здесь нет: он берёт без предела, как и из казны.
STORE_TAKE: dict[GuildRank, int] = {
    GuildRank.RECRUIT: 0,
    GuildRank.MEMBER: 20,
    GuildRank.VETERAN: 60,
    GuildRank.ELDER: 200,
}


@dataclass(frozen=True, slots=True)
class GuildMember:
    """Один человек в гильдии: звание и вклад.

    ``contributed`` - его доля в деяниях гильдии: сколько он принёс ей боями и
    золотом. Число только растёт и ни на что не тратится: это память о том, кто
    держал гильдию, а не валюта (ADR 0076).
    """

    character_id: int
    rank: GuildRank = GuildRank.RECRUIT
    contributed: int = 0


@dataclass(frozen=True, slots=True)
class Guild:
    """Гильдия целиком: имя, состав со званиями, казна и деяния.

    Ничего производного тут не хранится: имена участников и их уровни экран
    приносит отдельно, как и у отряда, а ступень считается из ``deeds``.
    """

    id: int
    name: str
    founder_id: int
    members: tuple[GuildMember, ...] = ()
    vault_gold: int = 0
    deeds: int = 0

    @property
    def size(self) -> int:
        return len(self.members)

    def has(self, character_id: int) -> bool:
        return any(one.character_id == character_id for one in self.members)

    def rank_of(self, character_id: int) -> GuildRank | None:
        for one in self.members:
            if one.character_id == character_id:
                return one.rank
        return None

    def contributed_by(self, character_id: int) -> int:
        for one in self.members:
            if one.character_id == character_id:
                return one.contributed
        return 0

    def can_invite(self, character_id: int) -> bool:
        rank = self.rank_of(character_id)
        return rank is not None and rank >= INVITE_RANK

    def can_take_from_vault(self, character_id: int) -> bool:
        rank = self.rank_of(character_id)
        return rank is not None and withdraw_fights(rank) != 0

    def with_member(
        self, character_id: int, rank: GuildRank = GuildRank.RECRUIT, seats: int = MAX_MEMBERS
    ) -> Guild:
        """Позванный встал в состав. Полная гильдия не меняется вовсе."""
        if self.has(character_id) or self.size >= max(0, seats):
            return self
        return replace(self, members=(*self.members, GuildMember(character_id, rank)))

    def without(self, character_id: int) -> Guild:
        return replace(
            self, members=tuple(one for one in self.members if one.character_id != character_id)
        )

    def with_rank(self, character_id: int, rank: GuildRank) -> Guild:
        return replace(
            self,
            members=tuple(
                replace(one, rank=rank) if one.character_id == character_id else one
                for one in self.members
            ),
        )

    def succeeded_by(self, character_id: int) -> Guild:
        """Гильдия у нового основателя; прежний остаётся в ней старейшиной.

        Уйти основатель не может и теперь: гильдия без того, кто за неё отвечает,
        - это брошенная казна. Но он может её передать (ADR 0076).
        """
        if self.rank_of(character_id) is None:
            return self
        handed = self.with_rank(self.founder_id, GuildRank.ELDER).with_rank(
            character_id, GuildRank.FOUNDER
        )
        return replace(handed, founder_id=character_id)


@dataclass(frozen=True, slots=True)
class Standing:
    """Где гильдия стоит сейчас: ступень, деяния и что до следующей.

    Считается целиком из ``deeds``, поэтому разойтись с базой ей негде.
    """

    tier: GuildTier | None = None
    next_tier: GuildTier | None = None
    deeds: int = 0
    seats: int = MAX_MEMBERS
    members: int = 0
    #: Сколько разных вещей держит хранилище на этой ступени (ADR 0077).
    store_slots: int = 0

    @property
    def level(self) -> int:
        return self.tier.level if self.tier is not None else 1

    @property
    def name(self) -> str:
        return self.tier.name if self.tier is not None else ""

    @property
    def full(self) -> bool:
        return self.members >= self.seats

    @property
    def deeds_left(self) -> int:
        """Сколько деяний до следующей ступени. Ноль - выше ступеней нет."""
        if self.next_tier is None:
            return 0
        return max(0, self.next_tier.deeds - self.deeds)

    @property
    def exp_percent(self) -> float:
        return self.tier.exp_percent if self.tier is not None else 0.0

    @property
    def gold_percent(self) -> float:
        return self.tier.gold_percent if self.tier is not None else 0.0

    @property
    def pays(self) -> bool:
        """Даёт ли ступень своим хоть что-нибудь сверх мест."""
        return bool(self.exp_percent or self.gold_percent)


def standing(content: GameContent, guild: Guild) -> Standing:
    """Ступень гильдии по её деяниям.

    Ступеней в содержимом может не оказаться вовсе - тогда гильдия стоит там же,
    где стояла до ступеней: полный состав и никаких надбавок. Содержимое
    переживает код и наоборот (``Claude.md``, правило 8).
    """
    tier = content.guild_tier_at(guild.deeds)
    return Standing(
        tier=tier,
        next_tier=content.guild_tier_after(guild.deeds),
        deeds=guild.deeds,
        seats=tier.seats if tier is not None else MAX_MEMBERS,
        members=guild.size,
        store_slots=tier.store_slots if tier is not None else 0,
    )


def fight_bonus(place: Standing, *, experience: int, gold: int) -> tuple[int, int]:
    """Что ступень добавляет своему за выигранный бой: опыт и золото сверх.

    Надбавка ложится **поверх посчитанной платы**, там, где за бой платят, и
    больше её не читает никто: гильдия - не свёрток прибавок, а объединение
    (``handlers/combat._settle_world``).
    """
    extra_exp = int(max(0, experience) * place.exp_percent / 100.0)
    extra_gold = int(max(0, gold) * place.gold_percent / 100.0)
    return extra_exp, extra_gold


def deeds_for_deposit(gold: int, level: int) -> int:
    """Сколько деяний записывает гильдии внесённое золото.

    Меряется боем своего уровня, а не числом: иначе один и тот же вклад значил
    бы на первом уровне месяц игры, а на сто пятидесятом - одну вылазку
    (``Claude.md``, правило 3).
    """
    if gold <= 0:
        return 0
    return gold // fight_worth(level)


def fight_worth(level: int) -> int:
    """Чего стоит один бой этого уровня, целым числом. Мера вклада и выемки."""
    return max(1, round(gold_at(level)))


def withdraw_fights(rank: GuildRank) -> int:
    """Сколько боёв своего уровня звание выносит за переворот. ``-1`` - без предела."""
    if rank is GuildRank.FOUNDER:
        return -1
    return WITHDRAW_FIGHTS.get(rank, 0)


def withdraw_limit(rank: GuildRank, level: int) -> int | None:
    """Предел выемки за переворот, золотом. ``None`` - предела нет (основатель)."""
    fights = withdraw_fights(rank)
    if fights < 0:
        return None
    return fights * fight_worth(level)


def raised(rank: GuildRank) -> GuildRank:
    """Звание на ступень выше. Выше старейшины не поднимают: гильдию передают."""
    return GuildRank(min(int(rank) + 1, int(GuildRank.ELDER)))


def lowered(rank: GuildRank) -> GuildRank:
    """Звание на ступень ниже. Ниже новика не опускают: из гильдии выгоняют."""
    return GuildRank(max(int(rank) - 1, int(GuildRank.RECRUIT)))


def name_refusal(name: str) -> str:
    """Пусто, когда имя годится; иначе - чем не годится."""
    trimmed = name.strip()
    if not (NAME_MIN <= len(trimmed) <= NAME_MAX):
        return f"Имя гильдии - от {NAME_MIN} до {NAME_MAX} знаков."
    if not any(ch.isalpha() for ch in trimmed):
        return "В имени гильдии должны быть буквы."
    return ""


def found_refusal(*, level: int, gold: int, in_guild: bool, name_taken: bool, name: str) -> str:
    """Пусто, когда завести гильдию можно; иначе - почему нельзя."""
    if in_guild:
        return "Вы уже в гильдии. Выйдите из неё, прежде чем заводить свою."
    if level < FOUND_LEVEL:
        return f"Гильдию заводят с {FOUND_LEVEL} уровня. Ваш: {level}."
    if gold < FOUND_COST:
        return f"На грамоту нужно {FOUND_COST} золота. У вас {gold}."
    if refusal := name_refusal(name):
        return refusal
    if name_taken:
        return "Гильдия с таким именем уже есть."
    return ""


def invite_refusal(
    *,
    guild: Guild | None,
    place: Standing,
    inviter_id: int,
    invitee_name: str,
    invitee_in_guild: bool,
) -> str:
    """Пусто, когда звать можно; иначе - почему нельзя, целой фразой."""
    if guild is None:
        return "У вас нет гильдии."
    if not guild.can_invite(inviter_id):
        return f"Звать в гильдию может {INVITE_RANK.title} и выше."
    if place.full:
        return (
            f"В гильдию помещается {place.seats} человек, и все места заняты. "
            "Следующая ступень даст ещё."
        )
    if invitee_in_guild:
        return f"{invitee_name} уже в гильдии."
    return ""


def rank_change_refusal(*, guild: Guild, actor_id: int, target_id: int, to: GuildRank) -> str:
    """Пусто, когда звание можно сменить; иначе - почему нельзя.

    Звание раздаёт и снимает только основатель, и только среди четырёх
    раздаваемых: второго основателя не бывает, гильдию передают целиком.
    """
    if guild.rank_of(actor_id) is not GuildRank.FOUNDER:
        return "Звание в гильдии раздаёт только основатель."
    if target_id == actor_id:
        return "Своё звание основатель не меняет: гильдию передают целиком."
    if guild.rank_of(target_id) is None:
        return "Этого человека нет в гильдии."
    if to not in GRANTABLE:
        return "Второго основателя не бывает. Гильдию передают целиком."
    if guild.rank_of(target_id) is to:
        return "У него уже это звание."
    return ""


def succeed_refusal(*, guild: Guild | None, actor_id: int, target_id: int) -> str:
    """Пусто, когда гильдию можно передать этому человеку; иначе - почему нельзя."""
    if guild is None:
        return "У вас нет гильдии."
    if guild.rank_of(actor_id) is not GuildRank.FOUNDER:
        return "Передать гильдию может только основатель."
    if target_id == actor_id:
        return "Гильдию передают другому."
    if guild.rank_of(target_id) is None:
        return "Этого человека нет в гильдии."
    return ""


def kick_refusal(*, guild: Guild, actor_id: int, target_id: int) -> str:
    """Пусто, когда этого можно выгнать; иначе - почему нельзя."""
    if target_id == actor_id:
        return "Себя из гильдии не выгоняют: из неё выходят."
    actor = guild.rank_of(actor_id)
    target = guild.rank_of(target_id)
    if target is None:
        return "Этого человека нет в гильдии."
    if actor is None or actor < KICK_RANK:
        return f"Выгонять из гильдии может {KICK_RANK.title} и выше."
    if actor <= target:
        return "Выгнать можно только того, кто ниже вас званием."
    return ""


def withdraw_refusal(
    *, guild: Guild, actor_id: int, amount: int, level: int, taken: int = 0
) -> str:
    """Пусто, когда столько взять можно; иначе - почему нельзя.

    ``taken`` - сколько этот человек уже вынес за нынешний переворот прилавка.
    Предел считается от его же уровня: выемка стоит одинаково и в Дубно, и на
    последней заставе (ADR 0076).
    """
    if amount <= 0:
        return "Назовите сумму."
    rank = guild.rank_of(actor_id)
    if rank is None:
        return "Вы не в этой гильдии."
    if not guild.can_take_from_vault(actor_id):
        return f"{rank.title.capitalize()} из казны не берёт: берут званием выше."
    if amount > guild.vault_gold:
        return f"В казне только {guild.vault_gold}."
    limit = withdraw_limit(rank, level)
    if limit is not None and taken + amount > limit:
        return (
            f"За переворот {rank.title} берёт из казны не больше {limit} золота. "
            f"Осталось: {max(0, limit - taken)}."
        )
    return ""


def band_level(tiers: Sequence[GuildTier], place: Standing) -> int:
    """Уровень, которым меряется всё, что гильдия платит и ставит (ADR 0077).

    У гильдии нет уровня: у неё есть ступень. Но всё, что игра платит золотом,
    меряется одним боем своего уровня (``Claude.md``, правило 3), и подряду со
    ставкой войны тоже нужна такая мера. Она берётся из ступени: лестница
    растянута на всю полосу, поэтому первая ступень платит как начало пути, а
    последняя - как его конец. Уровень того, кто закрыл подряд, тут не при чём
    нарочно: иначе гильдия со сто пятидесятым в составе получала бы за то же
    дело в семь раз больше.
    """
    if not tiers:
        return max(1, MAX_LEVEL // 2)
    return max(1, min(MAX_LEVEL, round(MAX_LEVEL * place.level / len(tiers))))


def store_take_limit(rank: GuildRank) -> int | None:
    """Сколько вещей звание выносит из хранилища за переворот. ``None`` - без предела."""
    if rank is GuildRank.FOUNDER:
        return None
    return STORE_TAKE.get(rank, 0)


def can_take_from_store(guild: Guild, character_id: int) -> bool:
    """Есть ли смысл рисовать этому человеку кнопку «Взять» (``Claude.md``, правило 9)."""
    rank = guild.rank_of(character_id)
    if rank is None:
        return False
    limit = store_take_limit(rank)
    return limit is None or limit > 0


def stow_refusal(
    *, guild: Guild, actor_id: int, amount: int, held: int, place: Standing, kinds: int, known: bool
) -> str:
    """Пусто, когда вещь можно положить в хранилище; иначе - почему нельзя.

    ``kinds`` - сколько разных вещей в хранилище уже лежит, ``known`` - лежит ли
    там уже эта. Место считается видами: новая вещь занимает место, а прибавка
    к лежащей стопке - нет.
    """
    if guild.rank_of(actor_id) is None:
        return "Вы не в этой гильдии."
    if amount <= 0:
        return "Назовите количество."
    if held < amount:
        return f"У вас {held}: столько не положить."
    if not known and kinds >= max(0, place.store_slots):
        return (
            f"В хранилище гильдии {place.store_slots} мест, и все заняты. "
            "Следующая ступень даст ещё."
        )
    return ""


def take_refusal(*, guild: Guild, actor_id: int, amount: int, stored: int, taken: int = 0) -> str:
    """Пусто, когда столько вещей можно вынести; иначе - почему нельзя.

    ``taken`` - сколько этот человек уже вынес за нынешний переворот прилавка.
    Предел у хранилища тот же по смыслу, что у казны (ADR 0076): общее добро,
    которое один человек выносит целиком, - это не общее добро.
    """
    rank = guild.rank_of(actor_id)
    if rank is None:
        return "Вы не в этой гильдии."
    if amount <= 0:
        return "Назовите количество."
    if stored < amount:
        return f"В хранилище только {stored}."
    limit = store_take_limit(rank)
    if limit is not None and limit <= 0:
        return f"{rank.title.capitalize()} из хранилища не берёт: берут званием выше."
    if limit is not None and taken + amount > limit:
        return (
            f"За переворот {rank.title} выносит из хранилища не больше {limit} вещей. "
            f"Осталось: {max(0, limit - taken)}."
        )
    return ""
