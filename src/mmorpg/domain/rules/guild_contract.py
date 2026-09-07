"""Гильдейский подряд: направленное дело на всю гильдию, на переворот (ADR 0077).

Сводка заставы зовёт одного человека и платит ему (ADR 0053, 0054); подряд зовёт
гильдию и платит гильдии - золотом в казну и деяниями. Дело общее: считается
всё, что за переворот сделали её люди, кто бы из них ни сделал, и закрывается
подряд сам, как только счёт дошёл до нужного. Кнопки «сдать подряд» нет нарочно:
кнопка, которую надо не забыть нажать, - это налог на память, а не игра.

Три дела, и они одни и те же каждый переворот, только числа другие: выиграть
бои с миром, пройти спуски до логова, снести золото в казну. Постоянство тут нарочно -
гильдия не читает подряд заново каждый раз, она знает, к чему идёт; меняется
размер, и он растёт со ступенью.

Чистая функция от ``(сид мира, гильдия, переворот, ступень)``: ничего не
хранится и не пишется. Счёт сделанного держит кэш со сроком до конца переворота
(``application/services/guild.py``), как и разовость надбавки сводки.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mmorpg.domain.entities.content import GuildTier
from mmorpg.domain.procgen.seeds import derive, rng
from mmorpg.domain.rules.guild import Standing, band_level, fight_worth

#: Во сколько боёв уровня ступени обходится гильдии закрытый подряд. Плата идёт
#: в казну целиком: подряд растит гильдию, а не карман того, кто его закрыл.
#: Число сдержанное нарочно: подряд - надбавка к тому, что гильдия делает и так,
#: а не второй источник золота рядом с боем (``Claude.md``, правило 3).
REWARD_FIGHTS = 10

#: Сколько деяний записывает закрытый подряд - за ступень.
DEEDS_PER_CONTRACT = 8

#: Размах, в котором гуляет размер подряда от переворота к перевороту: от трёх
#: четвертей до полутора обычного. Число целое и объявлено сотыми долями,
#: чтобы бросок был один и тот же на всякой машине.
SPREAD = (75, 150)


class ContractKind(StrEnum):
    """Что застава просит у гильдии. Порядок в наборе постоянный."""

    CULL = "cull"  # выиграть столько-то боёв с миром
    DELVE = "delve"  # пройти столько-то спусков до логова
    TITHE = "tithe"  # снести столько-то золота в казну


#: Сколько дела просит на первой ступени. Умножается на ступень и на бросок.
_BASE: dict[ContractKind, int] = {
    ContractKind.CULL: 10,
    ContractKind.DELVE: 2,
    ContractKind.TITHE: 20,  # в боях уровня ступени, не в золоте
}

_LINE: dict[ContractKind, str] = {
    ContractKind.CULL: "Выиграть боёв с миром: {target}.",
    ContractKind.DELVE: "Пройти спусков до логова: {target}.",
    ContractKind.TITHE: "Снести в казну золота: {target}.",
}


@dataclass(frozen=True, slots=True)
class Contract:
    """Одно дело подряда: что сделать, сколько и что за это гильдии."""

    kind: ContractKind
    #: Фраза игроку: что сделать и сколько.
    line: str
    #: Сколько нужно. У ``TITHE`` это золото, у остальных - число дел.
    target: int
    #: Уровень, которым мерена плата: он же уровень ступени (``band_level``).
    level: int
    #: Что гильдии за закрытый подряд: золото в казну и деяния. У взноса
    #: золотом плата одними деяниями: см. :func:`contracts`.
    reward_gold: int
    reward_deeds: int

    def done(self, progress: int) -> bool:
        return progress >= self.target

    @property
    def pay(self) -> str:
        """Чем платят за дело, ровно теми словами, что и считает движок."""
        if self.reward_gold:
            return f"{self.reward_gold} золота в казну и {self.reward_deeds} деяний"
        return f"{self.reward_deeds} деяний"


def contracts(
    tiers: Sequence[GuildTier],
    place: Standing,
    *,
    world_seed: str,
    guild_id: int,
    rotation: int,
) -> tuple[Contract, ...]:
    """Подряд этой гильдии на этот переворот. Детерминировано от аргументов.

    Числа растут со ступенью: гильдия, которая вымахала в Вольный двор, бьёт
    вдесятеро больше товарищества, и просят с неё столько же.
    """
    level = band_level(tiers, place)
    worth = fight_worth(level)
    source = rng(derive(world_seed, "guild-contract", str(guild_id), str(rotation)))
    made: list[Contract] = []
    for kind in ContractKind:
        roll = source.randint(*SPREAD)
        target = max(1, _BASE[kind] * max(1, place.level) * roll // 100)
        if kind is ContractKind.TITHE:
            target *= worth
        made.append(
            Contract(
                kind=kind,
                line=_LINE[kind].format(target=target),
                target=target,
                level=level,
                # Взнос золотом платят деяниями и только ими: золото за
                # снесённое золото - это петля, в которой казна растёт сама
                # (``Claude.md``, правило 3). Работа мира платится и тем и другим.
                reward_gold=0 if kind is ContractKind.TITHE else REWARD_FIGHTS * worth,
                reward_deeds=DEEDS_PER_CONTRACT * max(1, place.level),
            )
        )
    return tuple(made)
