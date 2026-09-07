"""Уклады: то, что пассивное умение меняет в правилах, а не в числах.

ЗАЧЕМ. Сто шестьдесят пассивных умений говорили одно и то же: «поднимает
броню», «поднимает точность», «поднимает урон». Очко, вложенное в такое, ничего
не меняло в бою - оно двигало число, которого игрок не видит, и выбор между
двумя пассивками был выбором между «плюс восемь» и «плюс девять».

Поэтому у пассивки теперь бывает **уклад** - правило, которое движок исполняет:
крит возвращает запас, добитый противник снимает откаты, первая беда спадает
сама, закрывшийся отвечает на удар. Прибавка осталась там, где она честна
(здоровье, броня, сопротивления), а всё, что должно быть слышно, стало укладом.

КАК ЭТО ОБЪЯВЛЯЕТСЯ. У пассивного умения в ``skills.toml`` вместо ключа прибавки
стоит код уклада, а ``power`` - его величина; что она значит, сказано у самого
уклада, и текст умения пишется по ней. Загрузчик принимает либо ключ из
``modifiers.EFFECTIVE_KEYS``, либо код отсюда: третьего не бывает, и умение,
обещающее то, чего никто не считает, в игру не попадает (ADR 0018).

ЧТО ДАЁТ РАНГ ПАССИВКИ. Размер уклада, и только его: у пассивки нет ни хода, ни
отката, ни цены (ADR 0073 в этой части стоит). Но размер теперь - это размер
чего-то, что происходит: «крит возвращает 8 процентов запаса» на первом ранге и
24 на пятом - это разница, которую слышно.

РАЗ ЗА БОЙ. Укладу, который срабатывает однажды, память нужна, и она берётся
там же, где её берут умения, - в откатах бойца, ключом ``once:<код>``
(``rules/combat._once_done``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import skills as skill_rules


@dataclass(frozen=True, slots=True)
class PassivePower:
    """Один уклад: что он делает и что значит его величина.

    ``text`` - образец фразы игрока с одним местом под число: экран подставляет
    величину нынешнего ранга, поэтому пассивка на пятом ранге читается не так,
    как на первом, и очко в неё слышно.
    """

    code: str
    name: str
    #: Фраза с одним ``{}`` под величину.
    text: str
    #: Срабатывает однажды за бой.
    once: bool = False

    def words(self, amount: float) -> str:
        return self.text.format(round(amount))


POWERS: tuple[PassivePower, ...] = (
    PassivePower(
        code="opener",
        name="Почин",
        text="В первом круге ваши удары не промахиваются и бьют на {} процентов сильнее.",
    ),
    PassivePower(
        code="crit_refund",
        name="Отдача",
        text="Критический удар возвращает вам {} процентов запаса.",
    ),
    PassivePower(
        code="kill_refresh",
        name="Разгон",
        text="Добив противника, вы снимаете ход со всех откатов и получаете {} процентов запаса.",
    ),
    PassivePower(
        code="guard_answer",
        name="Ответный щит",
        text="Закрывшись, вы отвечаете {} процентами удара тому, кто по вам попал.",
    ),
    PassivePower(
        code="overheal",
        name="Избыток",
        text="Лечение сверх полного здоровья становится барьером: {} процентов лишнего.",
    ),
    PassivePower(
        code="last_stand",
        name="Не пасть",
        text="Раз за бой смертельный удар оставляет вам {} процентов здоровья.",
        once=True,
    ),
    PassivePower(
        code="bleed_feed",
        name="Кровопийца",
        text=(
            "Удар по цели, которую точит горение, яд или кровотечение, "
            "возвращает вам {} процентов нанесённого здоровьем."
        ),
    ),
    PassivePower(
        code="status_ward",
        name="Хладнокровие",
        text="Беды, которые вешают на вас, держатся на {} процентов меньше ходов.",
    ),
    PassivePower(
        code="combo_master",
        name="Чутьё связки",
        text="Удар, ответивший на состояние цели, бьёт ещё на {} процентов сильнее.",
    ),
    PassivePower(
        code="cleave",
        name="Замах",
        text="Ваш удар по одной цели задевает соседа на {} процентов.",
    ),
    PassivePower(
        code="resolve",
        name="Упорство",
        text="Чем меньше у вас здоровья, тем выше броня: до {} процентов у почти павшего.",
    ),
    PassivePower(
        code="punish",
        name="Расплата",
        text="По цели, которой нечем ходить, ваш удар выше на {} процентов.",
    ),
    PassivePower(
        code="swift_start",
        name="Первый шаг",
        text="В первом круге вы ходите раньше: ваша инициатива выше на {} процентов.",
    ),
    PassivePower(
        code="panic_heal",
        name="Крайность",
        text="Впервые упав ниже трети здоровья, вы лечитесь на {} процентов.",
        once=True,
    ),
)

_BY_CODE: dict[str, PassivePower] = {one.code: one for one in POWERS}

#: Коды укладов - то, что загрузчик принимает у пассивного умения наравне с
#: ключом прибавки.
POWER_KEYS: frozenset[str] = frozenset(_BY_CODE)


def has_power(code: str) -> bool:
    return code in _BY_CODE


def power(code: str) -> PassivePower:
    return _BY_CODE[code]


def collect(content: GameContent, character: Character) -> dict[str, float]:
    """Уклады этого персонажа с их нынешними величинами.

    Одно место, где изученные пассивки превращаются в правила: бой спрашивает
    отсюда, и уклад, которого в реестре нет, сюда не попадает - содержимое
    переживает код (``Claude.md``, правило 8).
    """
    found: dict[str, float] = {}
    for skill in skill_rules.known_passives(content, character):
        if skill.effect not in _BY_CODE:
            continue
        rank = character.loadout.rank_of(skill.code)
        found[skill.effect] = found.get(skill.effect, 0.0) + skill.power_at_rank(rank)
    return found


def amount(powers: Mapping[str, float], code: str) -> float:
    """Величина уклада у этого бойца. Ноль - уклада у него нет."""
    return powers.get(code, 0.0)
