"""Перерождение: что делают на трёхсотом уровне и чем за это платят.

Уровень кончается, игра — нет. Перерождение — единственное место, где растут не за
счёт мира, а за счёт себя: приключенец отдаёт Палате вещь со своего плеча или
грань собственного умения и получает **Печать Палаты**. Уровень остаётся тем же,
опыт не трогают, характеристики от Печати не растут вовсе — Печать открывает
доступы (``Narrative.md``, раздел 6).

Что она открывает:

- **спуск идёт глубже**. Каждая Печать добавляет городским подземельям ещё один
  слой узлов ниже прежнего дна: было три схватки подряд, стало четыре;
- **грань открывается раньше**. Обычно грань выбирают на третьем ранге; с
  Печатью — на ранг раньше за каждую (``domain/rules/skills.edge_rank_for``);
- **голос в голосовании**. Печать — это голос, и весит он ровно столько
  перерождений, сколько человек их совершил. Палата спрашивает — про пошлину, про
  ворота, про цену воды, — а ответ считают по тем, кто за него заплатил делом.

Заклад не возвращается и не закладывается дважды: что ушло в перерождение, записано на
персонаже (``Character.pledges``). Без этого грань можно было бы заложить и
выбрать заново — грань выбирают бесплатно, — и Печати печатались бы из воздуха
ровно так же, как арена когда-то печатала золото.

Всё здесь чистое: функция возвращает нового персонажа или ``None``, а словами
отказ объясняет экран.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Item, Skill, Turning
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.progression import MAX_LEVEL

#: С какого уровня совершают перерождение. Он же последний: дальше расти некуда.
MIN_LEVEL = MAX_LEVEL

#: Уровень вещи, которую Палата примет в первое перерождение, и насколько он растёт с
#: каждым следующим. Дешёвая вещь — дешёвая Печать, поэтому запрос растёт.
PLEDGE_LEVEL_BASE = 20
PLEDGE_LEVEL_STEP = 20

#: Ранг, ниже которого грань в заклад не берут: грань умения, брошенного на
#: втором ранге, отдать не жалко, а перерождение — это про жалко.
PLEDGE_EDGE_RANK = 5

#: Сколько схваток в спуске у того, кто ни одного перерождения не совершил. Коротко
#: настолько, чтобы держать в голове, и длинно настолько, чтобы входить туда
#: раненым было решением (Roadmap 1.5).
BASE_DESCENT_DEPTH = 3

ITEM_PLEDGE = "item"
EDGE_PLEDGE = "edge"


def pledge_key(kind: str, entity_id: str) -> str:
    """Как заклад записан на персонаже: разновидность и то, что отдали."""
    return f"{kind}:{entity_id}"


def asking(seals: int) -> int:
    """Уровень вещи, которую Палата примет в следующее перерождение."""
    return PLEDGE_LEVEL_BASE + PLEDGE_LEVEL_STEP * max(0, seals)


def descent_depth(character: Character) -> int:
    """Сколько схваток в спуске у этого персонажа: три и по одной за Печать."""
    return BASE_DESCENT_DEPTH + max(0, character.seals)


def refusal(character: Character) -> str:
    """Пусто, когда перерождение можно совершить, иначе — почему нельзя."""
    if character.level < MIN_LEVEL:
        return (
            f"Перерождение совершают с {MIN_LEVEL} уровня. Ваш уровень: {character.level}. "
            "До этого Палата смотрит подорожную и не более того."
        )
    return ""


def pledgeable_items(content: GameContent, character: Character) -> tuple[Item, ...]:
    """Вещи, которые Палата примет: надетые, не ниже запроса, ещё не заложенные.

    Только надетые: отдать то, что лежит в сумке про запас, — не заклад.
    """
    wanted = asking(character.seals)
    found = [
        content.item(item_id)
        for item_id in character.equipment.item_ids()
        if content.has_item(item_id) and not character.has_pledged(pledge_key(ITEM_PLEDGE, item_id))
    ]
    return tuple(sorted((item for item in found if item.level >= wanted), key=lambda i: i.name))


def pledgeable_edges(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Грани, которые Палата примет: выбранные, на полном ранге, не заложенные."""
    return tuple(
        content.skill(code)
        for code in sorted(skill_rules.known_codes(character))
        if content.has_skill(code)
        and character.loadout.edge_of(code) is not None
        and character.loadout.rank_of(code) >= PLEDGE_EDGE_RANK
        and not character.has_pledged(pledge_key(EDGE_PLEDGE, code))
    )


@dataclass(frozen=True, slots=True)
class Sealed:
    """Совершённое перерождение: кем персонаж стал и что он за это отдал."""

    character: Character
    #: Как называется отданное, словами игрока.
    given: str
    #: Вещь, которая ушла из слота. Пусто, если закладывали грань.
    item_id: str = ""


def pledge_item(content: GameContent, character: Character, item_id: str) -> Sealed | None:
    """Отдать надетую вещь. Она уходит из слота и не попадает в сумку."""
    if refusal(character):
        return None
    if all(item.id != item_id for item in pledgeable_items(content, character)):
        return None
    slot = next(
        (name for name, worn in character.equipment.items.items() if worn == item_id),
        None,
    )
    if slot is None:
        return None
    stripped = replace(character, equipment=character.equipment.unequip(slot))
    return Sealed(
        character=stripped.with_seal(pledge_key(ITEM_PLEDGE, item_id)),
        given=content.item(item_id).name,
        item_id=item_id,
    )


def pledge_edge(content: GameContent, character: Character, skill_code: str) -> Sealed | None:
    """Отдать грань умения. Само умение и его ранг остаются при вас."""
    if refusal(character):
        return None
    if all(skill.code != skill_code for skill in pledgeable_edges(content, character)):
        return None
    skill = content.skill(skill_code)
    edge_code = character.loadout.edge_of(skill_code)
    given = f"{skill.name}, грань «{skill.edge(edge_code).name}»" if edge_code else skill.name
    cleared = skill_rules.clear_edge(character, skill)
    return Sealed(character=cleared.with_seal(pledge_key(EDGE_PLEDGE, skill_code)), given=given)


# --- голосование ---------------------------------------------------


def may_answer(character: Character) -> bool:
    """Голос есть у того, кто заплатил за него перерождением."""
    return character.seals > 0


def voice(character: Character) -> int:
    """Сколько весит его голос: по Печати за перерождение."""
    return max(0, character.seals)


def answered(character: Character, turning: Turning) -> str:
    """Что этот персонаж ответил на открытый вопрос. Пусто — ещё не отвечал.

    Голос, поданный за прошлый цикл, в этом не считается, и ответ, которого в
    содержимом больше нет, не считается тоже (``Claude.md``, правило 8).
    """
    if character.turning_cycle != turning.id:
        return ""
    if not turning.has_option(character.turning_answer):
        return ""
    return character.turning_answer


def answer(character: Character, turning: Turning, option_id: str) -> Character | None:
    """Подать голос. ``None``, когда голоса нет, ответа такого нет или он уже подан."""
    if not may_answer(character) or not turning.has_option(option_id):
        return None
    if answered(character, turning) == option_id:
        return None
    return character.with_turning_answer(turning.id, option_id)


def leading(tally: Mapping[str, int]) -> str:
    """Ответ, за которым сейчас больше голосов. Пусто при равенстве и пустоте."""
    counted = {option: votes for option, votes in tally.items() if votes > 0}
    if not counted:
        return ""
    ranked = sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ""
    return ranked[0][0]
