"""Что смотритель игры вправе сделать с персонажем - своим или чужим.

Смотритель - не игрок посильнее: всё здесь - обход работы, которую игра иначе
попросила бы сделать: золото, которое заплатило бы задание, уровень, который
принёс бы бой, раны, которые закрыла бы ночь на постоялом дворе. Ничто здесь не
выдумывает собственных правил: золото, уровни и очки приходят теми же функциями,
какими пользуется игра, поэтому персонаж смотрителя остаётся законным
персонажем.

Те же обходы смотритель применяет к игроку, написавшему, что что-то пошло не
так, - поэтому каждая функция здесь принимает персонажа, которого меняет, а не
считает его собственным персонажем смотрителя.

Кто смотритель, решается вне домена, через ``ADMIN_IDS``; этот модуль отвечает
только на вопрос «и что тогда происходит».
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, OwnerKind
from mmorpg.domain.entities.quest import QuestLog
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.guild import Guild, GuildRank
from mmorpg.domain.rules.party import Party
from mmorpg.domain.rules.progression import (
    MAX_LEVEL,
    LevelUp,
    experience_to_reach,
    grant_experience,
)
from mmorpg.domain.rules.stats import derived_stats

# Один шаг каждой выдачи. Круглые числа, потому что смотритель нажмёт кнопку ещё раз,
# если захочет больше.
GOLD_STEP = 1000
POINTS_STEP = 5


def grant_gold(character: Character, amount: int = GOLD_STEP) -> Character:
    """Изменить золото на руках. Число со знаком: минус списывает, ноль не бывает."""
    return character.with_gold(amount)


def set_bank_gold(character: Character, amount: int) -> Character:
    """Выставить золото в ячейке. Ниже нуля не опускается."""
    return replace(character, bank_gold=max(0, amount))


def set_health(content: GameContent, character: Character, value: int) -> Character:
    """Выставить здоровье в границах нынешнего максимума.

    Число зажимается так же, как зажимается сохранённое (``Character.with_health``):
    ниже единицы играть нельзя, выше максимума нечем.
    """
    maximum = derived_stats(content, character).max_health
    return character.with_health(value, maximum)


def rename(character: Character, name: str) -> Character:
    """Сменить имя. Годность имени проверяет тот, кто принял набранное."""
    return replace(character, name=name.strip())


def set_stat(character: Character, code: StatCode, value: int) -> Character | None:
    """Выставить вложенное в характеристику. Разница идёт через нераспределённые очки.

    Больше, чем есть очков (уже вложенных сюда плюс нераспределённых), поставить
    нельзя: тогда персонаж носил бы то, чего на его уровне иметь не может. Ниже
    нуля — тоже нельзя. Не хватает очков — их сначала выдают («Задать точно»).
    """
    if value < 0:
        return None
    delta = value - character.allocated[code]
    if delta > character.unspent_stat_points:
        return None
    return replace(
        character,
        allocated=character.allocated.with_change(code, delta),
        unspent_stat_points=character.unspent_stat_points - delta,
    )


def raise_level(content: GameContent, character: Character) -> tuple[Character, LevelUp]:
    """Ровно один уровень, оплаченный тем опытом, которого он и правда стоит.

    Выдаётся опыт, а не ставится уровень, поэтому очки, идущие с уровнем, приходят из
    единственного места, где их раздают.
    """
    if character.level >= MAX_LEVEL:
        return character, LevelUp(
            previous_level=character.level, new_level=character.level, stat_points=0, skill_points=0
        )
    needed = experience_to_reach(character.level + 1) - character.experience
    return grant_experience(content, character, max(0, needed))


def heal(content: GameContent, character: Character) -> Character:
    """Закрыть все раны, которые несёт персонаж."""
    maximum = derived_stats(content, character).max_health
    return character.with_health(maximum, maximum)


def grant_points(
    character: Character, stat_points: int = POINTS_STEP, skill_points: int = POINTS_STEP
) -> Character:
    return character.with_level(character.level, stat_points=stat_points, skill_points=skill_points)


def move_to(character: Character, city_id: str) -> Character:
    """Перевести персонажа в город.

    Единственная правка чужого персонажа, которая не выдаёт ничего: игрок, чей
    экран остался в снесённом городе, стоит там, пока его оттуда не выведут.
    """
    return replace(character, city_id=city_id)


def equip_item(
    content: GameContent, character: Character, item_id: str
) -> tuple[Character, tuple[tuple[str, int], ...]] | None:
    """Надеть вещь из сумки. Возвращает персонажа и правку сумки: −1 надетому,
    +1 тому, что стояло в слоте раньше. ``None`` — это не снаряжение."""
    if not content.has_item(item_id):
        return None
    item = content.item(item_id)
    if not item.is_equipment:
        return None
    displaced = character.equipment.item_in(item.slot)
    bag: list[tuple[str, int]] = [(item_id, -1)]
    if displaced is not None and displaced != item_id:
        bag.append((displaced, 1))
    return replace(character, equipment=character.equipment.equip(item.slot, item_id)), tuple(bag)


def unequip_slot(character: Character, slot: str) -> tuple[Character, str] | None:
    """Снять вещь со слота — она уходит в сумку. ``None`` — слот и так пуст."""
    item_id = character.equipment.item_in(slot)
    if item_id is None:
        return None
    return replace(character, equipment=character.equipment.unequip(slot)), item_id


def mark_quest_done(character: Character, quest_id: str) -> Character:
    """Затолкать задание в закрытые — когда оно застряло и сдать его нечем."""
    log = character.quests
    taken = {key: value for key, value in log.taken.items() if key != quest_id}
    done = log.done if quest_id in log.done else (*log.done, quest_id)
    return replace(character, quests=QuestLog(taken=MappingProxyType(taken), done=done))


def clear_quest(character: Character, quest_id: str) -> Character:
    """Убрать задание из журнала совсем: ни взято, ни закрыто — можно брать заново."""
    log = character.quests
    return replace(
        character,
        quests=QuestLog(
            taken=MappingProxyType(
                {key: value for key, value in log.taken.items() if key != quest_id}
            ),
            done=tuple(one for one in log.done if one != quest_id),
        ),
    )


def set_quest_progress(character: Character, quest_id: str, amount: int) -> Character:
    """Выставить счётчик задания. Не взято — берётся с этим счётом; закрыто — снова взято."""
    log = character.quests
    return replace(
        character,
        quests=QuestLog(
            taken=MappingProxyType({**log.taken, quest_id: max(0, amount)}),
            done=tuple(one for one in log.done if one != quest_id),
        ),
    )


def teach_skill(content: GameContent, character: Character, code: str) -> Character | None:
    """Дать классовое умение первым рангом — без очков и без гейтов ветви.

    Смотритель не покупает умение, а ставит его: это обход работы, как и всё
    здесь. Расовое умение так не выдают — оно приходит с расой.
    """
    if not content.has_skill(code):
        return None
    skill = content.skill(code)
    if skill.owner_kind is not OwnerKind.CLASS or skill_rules.is_known(character, code):
        return None
    return replace(character, loadout=character.loadout.with_rank(code, 1))


def set_skill_rank(
    content: GameContent, character: Character, code: str, rank: int
) -> Character | None:
    """Выставить ранг изученного умения. Ноль — забыть умение целиком.

    Очки не двигаются: смотритель ставит ранг, а не платит за него. Грань,
    оказавшаяся выше нового ранга, снимается вместе с ним.
    """
    loadout = character.loadout
    if not content.has_skill(code) or not skill_rules.is_known(character, code):
        return None
    wanted = max(0, min(rank, content.rules.max_rank))
    if wanted == 0:
        if code == loadout.racial:
            return None
        return replace(
            character,
            loadout=replace(
                loadout,
                ranks={key: value for key, value in loadout.ranks.items() if key != code},
                edges={key: value for key, value in loadout.edges.items() if key != code},
                actives=tuple(None if item == code else item for item in loadout.actives),
            ),
        )
    updated = loadout.with_rank(code, wanted)
    if wanted < skill_rules.edge_rank_for(content) and loadout.edge_of(code) is not None:
        updated = replace(
            updated, edges={key: value for key, value in updated.edges.items() if key != code}
        )
    return replace(character, loadout=updated)


def set_skill_edge(
    content: GameContent, character: Character, code: str, edge_code: str
) -> Character | None:
    """Выбрать грань изученного умения или снять её пустым значением."""
    if not content.has_skill(code) or not skill_rules.is_known(character, code):
        return None
    skill = content.skill(code)
    if edge_code and all(edge.code != edge_code for edge in skill.edges):
        return None
    edges = {key: value for key, value in character.loadout.edges.items() if key != code}
    if edge_code:
        edges[code] = edge_code
    return replace(character, loadout=replace(character.loadout, edges=edges))


def put_skill_in_slot(
    content: GameContent, character: Character, slot: int, code: str | None
) -> Character | None:
    """Разложить изученное боевое умение по слотам — тем же правилом, что и игрок."""
    return skill_rules.put_in_slot(content, character, slot, code)


def unslot_skill(content: GameContent, character: Character, code: str) -> Character | None:
    """Убрать умение из того слота, где оно лежит. ``None`` — его нигде нет."""
    slot = next((index for index, held in enumerate(character.loadout.actives) if held == code), -1)
    if slot < 0:
        return None
    return skill_rules.put_in_slot(content, character, slot, None)


def respec_skills(content: GameContent, character: Character) -> Character:
    """Сбросить классовое дерево: очки — назад в нераспределённые, панель — чистой.

    Это законная замена «понизить уровень»: очки возвращаются все, а расовое
    умение и его ранг не трогаются — за него не платили.
    """
    loadout = character.loadout
    refund = sum(
        skill_rules.spent_on(content, character, code)
        for code in loadout.ranks
        if content.has_skill(code)
        and content.skill(code).owner_kind is OwnerKind.CLASS
        and code != loadout.racial
    )
    kept: dict[str, int] = {}
    if loadout.racial and loadout.racial in loadout.ranks:
        kept[loadout.racial] = loadout.ranks[loadout.racial]
    return replace(
        character,
        loadout=replace(
            loadout,
            ranks=kept,
            edges={key: value for key, value in loadout.edges.items() if key == loadout.racial},
            actives=(None,) * len(loadout.actives),
        ),
        unspent_skill_points=character.unspent_skill_points + refund,
    )


# --- отряд и гильдия игрока -------------------------------------------
#
# Смотритель правит объединение тем же обходом, что и персонажа: без зова и без
# согласия - «вывести», «сменить звание», «выставить казну». Собравшего отряд и
# основателя гильдии так не трогают: объединение без того, кто за него отвечает,
# не правят, а распускают целиком (это делает хендлер).


def remove_from_party(party: Party, character_id: int) -> Party | None:
    """Вывести человека из отряда. ``None`` - его там нет или это собравший."""
    if not party.has(character_id) or character_id == party.leader_id:
        return None
    return party.without(character_id)


def remove_from_guild(guild: Guild, character_id: int) -> Guild | None:
    """Вывести человека из гильдии. ``None`` - его там нет или это основатель."""
    if guild.rank_of(character_id) is None or character_id == guild.founder_id:
        return None
    return guild.without(character_id)


def set_guild_rank(guild: Guild, character_id: int, rank: GuildRank) -> Guild | None:
    """Сменить звание участнику. ``None`` - его нет в гильдии, это основатель, или
    звание не между «участник» и «офицер»: второго основателя не бывает."""
    current = guild.rank_of(character_id)
    if current is None or character_id == guild.founder_id:
        return None
    if rank not in (GuildRank.MEMBER, GuildRank.OFFICER) or current is rank:
        return None
    return guild.with_rank(character_id, rank)


def set_vault_gold(guild: Guild, amount: int) -> Guild:
    """Выставить казну гильдии. Ниже нуля не опускается.

    Двигает казну хендлер условным ``UPDATE`` (``Claude.md``, правило 8), как и
    кошелёк персонажа; здесь - только новое число.
    """
    return replace(guild, vault_gold=max(0, amount))


def set_level(content: GameContent, character: Character, level: int) -> tuple[Character, LevelUp]:
    """Поднять до названного уровня, опытом и по одному.

    Понизить нельзя: очки уже вложены, умения уже изучены, и отобрать уровень
    значило бы оставить персонажа с тем, чего он на этом уровне иметь не может.
    """
    wanted = max(character.level, min(level, MAX_LEVEL))
    grown = character
    gained_stat = 0
    gained_skill = 0
    while grown.level < wanted:
        grown, step = raise_level(content, grown)
        gained_stat += step.stat_points
        gained_skill += step.skill_points
    return grown, LevelUp(
        previous_level=character.level,
        new_level=grown.level,
        stat_points=gained_stat,
        skill_points=gained_skill,
    )
