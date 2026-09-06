"""Дерево специализации: кем этот класс стал, оставаясь собой (ADR 0074).

Класс - корень, и дерево ветвится трижды: на тридцатом уровне, на семьдесят
пятом и на сто пятидесятом, каждый раз надвое. Восемь классов, шестнадцать
первых веток, тридцать две вторых, шестьдесят четыре третьих.

ВЕТКА РАСТЁТ ИЗ ВЕТКИ. На каждой ступени предлагаются только дети уже взятого:
Псионик приходит из Оккультиста и ниоткуда больше. Поэтому «кем я стал»
читается дорогой - Маг, Оккультист, Псионик, Иллюзионист, - а не списком из
трёх строк.

ВХОД - ЦЕПОЧКА ЗАДАНИЙ. Порогов характеристик нет вовсе: на «сколько ты вложил»
отвечают вехи (ADR 0068), а ветка отвечает на «кем ты захотел стать». Оплачен
вход уровнем и испытанием - тремя заданиями подряд, которые берут и сдают у
наставника. Испытание можно пройти у обеих веток и решить после: пока подкласс
не выбран, пройденное испытание - это открытая дверь, а не сделанный шаг.

ЧТО ВЕТКА ДАЁТ. Три вещи, и первая - новая:

* **боевое умение** (``skill_code``). Взяв ветку, игрок получает его в список
  изучаемых; ранг стоит очка, место в панели - наравне со всеми (ADR 0067).
  Панель не выросла: шесть боевых и один расовый, как было;
* **свёрток прибавок** - как техника дома и расовая способность;
* **правку классовой сетки** (ADR 0068): названная характеристика получает новый
  выход вместо классового.

ВЕТКИ СКЛАДЫВАЮТСЯ. Правки сеток накладываются по возрастанию ступени: поздняя
договаривает раннюю, а не спорит с ней.

ВЫБОР НЕОБРАТИМ. Ветку берут один раз, и это решение о том, кем персонаж будет:
обратимый выбор не выбор, а настройка.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import CharacterClass, GameContent, Skill, Subclass
from mmorpg.domain.entities.quest import Quest


def taken(content: GameContent, character: Character) -> tuple[Subclass, ...]:
    """Взятые ветки, по возрастанию ступени.

    Содержимое переживает персонажа (``Claude.md``, правило 8): ветка, которую
    вычеркнули из ``subclasses.toml``, здесь просто не появляется, а не роняет
    экран. По той же причине сюда не попадает ветка чужого класса.
    """
    found = [
        content.subclass(one)
        for one in character.subclass_ids
        if content.has_subclass(one) and content.subclass(one).class_id == character.class_id
    ]
    found.sort(key=lambda one: (one.tier, one.id))
    return tuple(found)


def taken_at(content: GameContent, character: Character, tier: int) -> Subclass | None:
    """Ветка, взятая на этой ступени. ``None`` - не взята."""
    for one in taken(content, character):
        if one.tier == tier:
            return one
    return None


def open_tier(content: GameContent, character: Character) -> int | None:
    """Ближайшая ступень, на которой игроку есть что решать. ``None`` - нет такой.

    Ступени берут по порядку: пока не выбрана первая, вторая не предлагается,
    даже если уровень давно за ней. Иначе поздний игрок пропускал бы первое
    решение и не узнавал, что оно было.
    """
    for tier in content.subclass_tiers(character.class_id):
        if taken_at(content, character, tier) is None:
            return tier
    return None


def offered(content: GameContent, character: Character, tier: int) -> tuple[Subclass, ...]:
    """Развилка на этой ступени: дети уже взятого, а на первой - корни класса.

    Показываются все, включая те, до которых игрок не дорос: закрытая ветка с
    названной ценой - это цель, а вычеркнутая из списка - это то, о чём игрок
    никогда не узнает.
    """
    tiers = content.subclass_tiers(character.class_id)
    if not tiers or tier == tiers[0]:
        return content.subclass_roots(character.class_id)
    earlier = [one for one in tiers if one < tier]
    parent = taken_at(content, character, earlier[-1]) if earlier else None
    if parent is None:
        return ()
    return content.subclass_children(parent.id)


def trial_quests(content: GameContent, one: Subclass) -> tuple[Quest, ...]:
    """Задания испытания этой ветки, по порядку."""
    return tuple(
        content.quest(quest_id) for quest_id in one.trial_ids if content.has_quest(quest_id)
    )


def trial_done(content: GameContent, character: Character, one: Subclass) -> bool:
    """Пройдено ли испытание целиком. Испытания нет - считается пройденным."""
    return all(character.quests.is_done(quest.id) for quest in trial_quests(content, one))


def trial_step(content: GameContent, character: Character, one: Subclass) -> Quest | None:
    """Задание испытания, за которым дело стало. ``None`` - испытание пройдено."""
    for quest in trial_quests(content, one):
        if not character.quests.is_done(quest.id):
            return quest
    return None


def trial_progress(content: GameContent, character: Character, one: Subclass) -> tuple[int, int]:
    """Сколько заданий испытания сдано и сколько их всего."""
    quests = trial_quests(content, one)
    done = sum(1 for quest in quests if character.quests.is_done(quest.id))
    return done, len(quests)


def refusal(content: GameContent, character: Character, one: Subclass) -> str:
    """Пусто, когда ветку можно взять, иначе - почему нельзя, словами игрока.

    Отказ называется целиком и до нажатия: игрок обязан услышать, чего именно
    ему не хватает, а не увидеть кнопку, которая молчит.
    """
    if one.class_id != character.class_id:
        return "Эта ветка не вашего класса."
    if taken_at(content, character, one.tier) is not None:
        return "На этой ступени вы уже определились. Выбранное не меняют."
    if one not in offered(content, character, one.tier):
        return "Эта ветка растёт не из вашей: до неё дорога через другую."
    if character.level < one.level:
        return f"Ветку берут с {one.level} уровня. Ваш уровень: {character.level}."
    if not trial_done(content, character, one):
        done, total = trial_progress(content, character, one)
        return f"Испытание не пройдено — сдано {done} из {total}."
    return ""


def choose(content: GameContent, character: Character, subclass_id: str) -> Character | None:
    """Взять ветку. ``None`` - взять нельзя, и почему, скажет ``refusal``."""
    if not content.has_subclass(subclass_id):
        return None
    one = content.subclass(subclass_id)
    if refusal(content, character, one):
        return None
    return character.with_subclass(one.id)


def modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Свёрток прибавок от всех взятых веток."""
    total: dict[str, float] = {}
    for one in taken(content, character):
        for key, value in one.modifiers.items():
            total[key] = total.get(key, 0.0) + value
    return total


def skills(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Умения взятых веток - те, что этому персонажу открыла специализация.

    Одно место, где дерево превращается в умения: их спрашивает и наставник, и
    панель, поэтому ветка, ничему не научившая, здесь просто ничего не даёт.
    """
    return tuple(
        skill for one in taken(content, character) for skill in content.subclass_skills(one.id)
    )


def class_of(content: GameContent, character: Character) -> CharacterClass:
    """Класс персонажа с сеткой, поправленной взятыми ветками (ADR 0068, 0074).

    Одно место, где сетка становится окончательной. Всё, что считает удар,
    здоровье, запас и броню, спрашивает класс отсюда, а не из реестра напрямую:
    иначе правка сетки работала бы в одних формулах и молчала в других.

    Правки накладываются по возрастанию ступени: поздняя договаривает раннюю.
    Названная характеристика заменяется целиком - ветка говорит, чем очко стало,
    а не на сколько сдвинулось: сдвиг пришлось бы читать вместе с тем, от чего
    он считается, и экран не смог бы назвать одно честное число.
    """
    klass = content.character_class(character.class_id)
    steps = taken(content, character)
    if not steps:
        return klass
    grid = dict(klass.scaling)
    for one in steps:
        grid.update(one.scaling)
    return replace(klass, scaling=grid)
