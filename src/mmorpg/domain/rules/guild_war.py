"""Гильдейская война: два объединения меряются людьми, а не часами (ADR 0077).

Война - не событие с часами и не осада, которую надо отстоять в назначенный
вечер: в игре без таймеров такого не бывает вовсе (``Claude.md``, правило 3).
Здесь она устроена как счёт: одна гильдия шлёт другой вызов, та соглашается,
обе кладут ставку из казны - и несколько переворотов прилавка всякий выигранный
поединок с человеком враждебной гильдии идёт своей стороне очком. Дошло до
конца - у кого очков больше, тот забирает обе ставки в казну и записывает
деяния; поровну - каждой возвращается своя.

Три вещи держат это в рамках игры:

- **очко даёт только поединок с живым человеком враждебной гильдии**. Арена и
  бои с миром не в счёт: война - это спор двух гильдий, а не гонка за добычей.
- **за одного и того же побеждённого платят раз за переворот**. Иначе двое
  сговорившихся набивают счёт друг об друга, не выходя из города.
- **деяния за войну платятся один раз, в конце**. Очко войны деянием не
  считается: деяния растит добыча мира (ADR 0076), и обойти это через поединок
  значило бы растить гильдию об игроков.

Ничего здесь не хранится и не пишет: война - запись в базе
(``GuildRepository``), а этот модуль только считает, кто что вправе сделать и
чем всё кончилось.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mmorpg.domain.entities.content import GuildTier
from mmorpg.domain.rules.guild import Guild, GuildRank, Standing, band_level, fight_worth

#: Сколько переворотов прилавка держится война. Три - это несколько дней живой
#: игры: короче не успеет тот, кто заходит через день, длиннее - забывается.
WAR_ROTATIONS = 3

#: Ставка войны в боях уровня ступени (:func:`mmorpg.domain.rules.guild.band_level`).
#: Ставка заметная нарочно: война без цены - это кнопка, которую жмут от скуки.
STAKE_FIGHTS = 40

#: Сколько деяний записывает гильдии выигранная война - за ступень.
DEEDS_PER_WAR = 20

#: Сколько ждёт ответа вызов на войну, секунд. Дольше зова в гильдию: решение
#: принимает основатель, а он не всегда в игре.
CALL_TTL = 3600


@dataclass(frozen=True, slots=True)
class War:
    """Война двух гильдий: кто с кем, до какого переворота и с каким счётом.

    ``ends`` - номер переворота прилавка, на котором война уже кончилась:
    сравнение идёт с нынешним переворотом, а не со временем. ``over`` ставится
    при расчёте, и расчёт делается лениво - тем, кто первым заглянул на экран
    войны или взял очко (``application/services/guild.py``).
    """

    id: int
    challenger_id: int
    defender_id: int
    stake: int = 0
    started: int = 0
    ends: int = 0
    challenger_score: int = 0
    defender_score: int = 0
    over: bool = False

    def has(self, guild_id: int) -> bool:
        return guild_id in {self.challenger_id, self.defender_id}

    def foe_of(self, guild_id: int) -> int:
        """Кто по ту сторону. Ноль - эта гильдия не воюет вовсе."""
        if guild_id == self.challenger_id:
            return self.defender_id
        if guild_id == self.defender_id:
            return self.challenger_id
        return 0

    def score_of(self, guild_id: int) -> int:
        if guild_id == self.challenger_id:
            return self.challenger_score
        if guild_id == self.defender_id:
            return self.defender_score
        return 0

    def rotations_left(self, rotation: int) -> int:
        """Сколько переворотов войне осталось. Ноль - она уже кончилась."""
        return max(0, self.ends - rotation)

    def due(self, rotation: int) -> bool:
        """Пора ли подводить итог: срок вышел, а расчёт ещё не сделан."""
        return not self.over and rotation >= self.ends


def war_stake(tiers: Sequence[GuildTier], place: Standing) -> int:
    """Ставка войны золотом: :data:`STAKE_FIGHTS` боёв уровня ступени."""
    return STAKE_FIGHTS * fight_worth(band_level(tiers, place))


def war_deeds(place: Standing) -> int:
    """Сколько деяний записывает выигранная война. Растёт со ступенью."""
    return DEEDS_PER_WAR * max(1, place.level)


def winner_of(war: War) -> int:
    """Чей верх. Ноль - поровну, и тогда каждой возвращается своя ставка."""
    if war.challenger_score > war.defender_score:
        return war.challenger_id
    if war.defender_score > war.challenger_score:
        return war.defender_id
    return 0


def spoils(war: War) -> dict[int, int]:
    """Сколько золота вернуть в казну каждой гильдии по итогу войны.

    Победившей - обе ставки, проигравшей - ничего; поровну - каждой своя.
    Ставка снята с казны при согласии, поэтому вернуть её надо всегда, и
    молчание тут было бы кражей.
    """
    champion = winner_of(war)
    if champion == 0:
        return {war.challenger_id: war.stake, war.defender_id: war.stake}
    return {champion: war.stake * 2, war.foe_of(champion): 0}


def declares(guild: Guild, character_id: int) -> bool:
    """Кто объявляет войну и соглашается на неё: только основатель.

    Ставку кладут из казны, а за казну отвечает он (ADR 0076).
    """
    return guild.rank_of(character_id) is GuildRank.FOUNDER


def declare_refusal(
    *,
    guild: Guild | None,
    actor_id: int,
    foe: Guild | None,
    foe_name: str,
    stake: int,
    at_war: bool,
    foe_at_war: bool,
    called: bool = False,
) -> str:
    """Пусто, когда войну можно объявить; иначе - почему нельзя, целой фразой."""
    if guild is None:
        return "У вас нет гильдии."
    if not declares(guild, actor_id):
        return "Войну объявляет основатель: ставку кладут из казны."
    if foe is None:
        return f"Гильдии «{foe_name}» в Велларе нет."
    if foe.id == guild.id:
        return "С собой не воюют."
    if at_war:
        return "Вы уже воюете. Дождитесь конца этой войны."
    if foe_at_war:
        return f"Гильдия «{foe.name}» уже воюет с другими."
    if called:
        return f"Вызов гильдии «{foe.name}» уже послан. Слово за ней."
    if guild.vault_gold < stake:
        return f"Ставка войны - {stake} золота, а в казне {guild.vault_gold}."
    return ""


def accept_refusal(
    *,
    guild: Guild | None,
    actor_id: int,
    challenger: Guild | None,
    stake: int,
    at_war: bool,
    challenger_at_war: bool,
) -> str:
    """Пусто, когда вызов можно принять; иначе - почему нельзя."""
    if guild is None:
        return "У вас нет гильдии."
    if not declares(guild, actor_id):
        return "Вызов принимает основатель: ставку кладут из казны."
    if challenger is None:
        return "Вас никто не вызывал."
    if at_war:
        return "Вы уже воюете."
    if challenger_at_war:
        return f"Гильдия «{challenger.name}» успела уйти на другую войну."
    if guild.vault_gold < stake:
        return f"Ставка войны - {stake} золота, а в казне {guild.vault_gold}."
    if challenger.vault_gold < stake:
        return f"В казне гильдии «{challenger.name}» ставки уже нет."
    return ""


def scores(*, winner_guild: int, loser_guild: int, war: War | None) -> bool:
    """Идёт ли этот выигранный поединок войне в очко.

    Очко берут только тогда, когда обе стороны в этой войне и по разные её
    стороны: поединок с соклановцем войны не касается, а поединок с чужим
    человеком - тем более.
    """
    if war is None or war.over or not winner_guild or not loser_guild:
        return False
    return war.has(winner_guild) and war.foe_of(winner_guild) == loser_guild
