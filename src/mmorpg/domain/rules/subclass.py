"""Ступени специализации: кем воин становится, оставаясь воином (ADR 0069).

Класс отвечает на вопрос «как ты дерёшься». Подкласс отвечает на вопрос «каким
именно ты стал», и отвечает трижды за дорогу: на тридцатом уровне, на семьдесят
пятом с одним уходом за плечами и на сто пятидесятом с тремя.

ЧЕГО ЗДЕСЬ НЕТ. Ни одной новой кнопки. Панель остаётся шесть боевых плюс один
расовый, экранов не прибавляется, и это не скромность, а условие: игру слушают
целиком, и всякая ступень, добавляющая слот, добавляет и то, что игрок обязан
удержать в голове (``docs/accessibility.md``).

ЧТО ПОДКЛАСС ДЕЛАЕТ. Ровно две вещи, и обе движок уже умеет считать:

* **свёрток прибавок** - как техника дома и расовая способность. Ключи из общего
  словаря, проверяются по ``EFFECTIVE_KEYS``;
* **правку классовой сетки** (ADR 0068) - названная характеристика получает новый
  выход вместо классового. Берсерком берсерка делает именно это: сила у него
  ведёт удар сильнее, а броню держит хуже, - а не строка в описании.

СТУПЕНИ СКЛАДЫВАЮТСЯ. На каждой берут одну, взятое не отбирают, и дредноут на сто
пятидесятом - это берсерк, ставший гибридом, ставший дредноутом. Поэтому правки
сеток накладываются по возрастанию ступени: поздняя ступень договаривает раннюю,
а не спорит с ней.

ВЫБОР НЕОБРАТИМ. Ступень берут один раз, и это первое настоящее решение о том,
кем персонаж будет: обратимый выбор не выбор, а настройка.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import CharacterClass, GameContent, Subclass
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.domain.rules import milestones as milestone_rules


def taken(content: GameContent, character: Character) -> tuple[Subclass, ...]:
    """Взятые ступени, по возрастанию ступени.

    Содержимое переживает персонажа (``Claude.md``, правило 8): подкласс, который
    вычеркнули из ``subclasses.toml``, здесь просто не появляется, а не роняет
    экран. По той же причине сюда не попадает подкласс чужого класса - его мог
    оставить уход, случившийся до правки содержимого.
    """
    found = [
        content.subclass(one)
        for one in character.subclass_ids
        if content.has_subclass(one) and content.subclass(one).class_id == character.class_id
    ]
    found.sort(key=lambda one: (one.tier, one.id))
    return tuple(found)


def taken_at(content: GameContent, character: Character, tier: int) -> Subclass | None:
    """Ступень, взятая на этом уровне специализации. ``None`` - не взята."""
    for one in taken(content, character):
        if one.tier == tier:
            return one
    return None


def refusal(content: GameContent, character: Character, one: Subclass) -> str:
    """Пусто, когда ступень можно взять, иначе - почему нельзя, словами игрока.

    Отказ называется целиком и до нажатия: игрок обязан услышать, чего именно ему
    не хватает, а не увидеть кнопку, которая молчит.
    """
    if one.class_id != character.class_id:
        return "Эта ступень не вашего класса."
    if taken_at(content, character, one.tier) is not None:
        return "На этой ступени вы уже определились. Выбранное не меняют."
    gate = one.gate
    if character.level < gate.level:
        return f"Ступень берут с {gate.level} уровня. Ваш уровень: {character.level}."
    if character.remorts < gate.remorts:
        short = gate.remorts - character.remorts
        word = "уход" if short == 1 else "ухода"
        return f"Нужно уходов под новое имя: {gate.remorts}. Не хватает {short} {word}."
    invested = milestone_rules.invested_stats(content, character)
    for code, threshold in gate.stats.items():
        if invested[code] < threshold:
            short = threshold - invested[code]
            return f"Нужно {threshold} в характеристике {code.value}. Не хватает {short}."
    return ""


def offered(content: GameContent, character: Character, tier: int) -> tuple[Subclass, ...]:
    """Все ступени этого уровня у класса игрока - и доступные, и пока нет.

    Показываются все: закрытая ступень с названной ценой - это цель, а вычеркнутая
    из списка ступень - это то, о чём игрок никогда не узнает.
    """
    return content.subclasses_of(character.class_id, tier)


def open_tier(content: GameContent, character: Character) -> int | None:
    """Ближайшая ступень, на которой игроку есть что решать. ``None`` - нет такой.

    Ступени берут по порядку: пока не выбрана тридцатая, семьдесят пятая не
    предлагается, даже если уровень давно за ней. Иначе поздний игрок пропускал
    бы первое решение и не узнавал, что оно было.
    """
    for tier in content.subclass_tiers(character.class_id):
        if taken_at(content, character, tier) is None:
            return tier
    return None


def choose(content: GameContent, character: Character, subclass_id: str) -> Character | None:
    """Взять ступень. ``None`` - взять нельзя, и почему, скажет ``refusal``."""
    if not content.has_subclass(subclass_id):
        return None
    one = content.subclass(subclass_id)
    if refusal(content, character, one):
        return None
    return character.with_subclass(one.id)


def modifiers(content: GameContent, character: Character) -> dict[str, float]:
    """Свёрток прибавок от всех взятых ступеней."""
    total: dict[str, float] = {}
    for one in taken(content, character):
        for key, value in one.modifiers.items():
            total[key] = total.get(key, 0.0) + value
    return total


def class_of(content: GameContent, character: Character) -> CharacterClass:
    """Класс персонажа с сеткой, поправленной взятыми ступенями (ADR 0068, 0069).

    Одно место, где сетка становится окончательной. Всё, что считает удар,
    здоровье, запас и броню, спрашивает класс отсюда, а не из реестра напрямую:
    иначе правка сетки работала бы в одних формулах и молчала в других.

    Правки накладываются по возрастанию ступени: поздняя договаривает раннюю.
    Названная характеристика заменяется целиком - подкласс говорит, чем очко
    стало, а не на сколько сдвинулось: сдвиг пришлось бы читать вместе с тем, от
    чего он считается, и экран не смог бы назвать одно честное число.
    """
    klass = content.character_class(character.class_id)
    steps = taken(content, character)
    if not steps:
        return klass
    grid = dict(klass.scaling)
    for one in steps:
        grid.update(one.scaling)
    return replace(klass, scaling=grid)


def stat_gate_progress(
    content: GameContent, character: Character, one: Subclass
) -> tuple[tuple[StatCode, int, int], ...]:
    """Пороги характеристик этой ступени: что нужно и что уже есть.

    Экран называет обе цифры: «Сила: 120 из 140» - это ответ на «сколько ещё», а
    не намёк на него.
    """
    invested: StatBlock = milestone_rules.invested_stats(content, character)
    return tuple(
        (code, invested[code], threshold) for code, threshold in sorted(one.gate.stats.items())
    )
