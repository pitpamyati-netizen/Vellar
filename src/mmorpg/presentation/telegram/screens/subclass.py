"""Дерево специализации: два экрана — развилка и испытание (ADR 0074).

«Ступень» показывает дорогу, которой игрок уже прошёл, и развилку, на которой
стоит: две ветки, у каждой сказано, что она даёт, чему учит и чего просит.
«Испытание» — три задания одной ветки, со счётчиками и одной кнопкой на шаг.

Испытание вынесено отдельным экраном не для порядка: три задания с условиями,
счётчиками и кнопками у двух веток сразу в одно сообщение не помещаются
(``docs/accessibility.md``, правило 11).

Обе ветки показываются всегда — и открытая, и закрытая. Закрытая ветка с
названной ценой это цель; ветка, вычеркнутая из списка до того, как её можно
взять, — это то, о чём игрок никогда не узнает и к чему, значит, не станет
готовиться.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Subclass
from mmorpg.domain.entities.quest import Quest
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.domain.rules import subclass_powers as way_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head
from mmorpg.presentation.telegram.screens.quests import instructions

#: Как называется каждая ступень словами игрока. Читается не из содержимого:
#: имена ступеней это часть разговора, а не часть баланса.
TIER_NAMES: dict[int, str] = {1: "Первая ветвь", 2: "Вторая ветвь", 3: "Третья ветвь"}


def choose_label(name: str) -> Label:
    return label(f"Стать: {name}")


def trial_label(name: str) -> Label:
    return label(f"Испытание: {name}")


def tier_name(tier: int) -> str:
    return TIER_NAMES.get(tier, f"Ступень {tier}")


def path_line(content: GameContent, character: Character) -> str:
    """Дорога, которой игрок пришёл, — одной строкой и по порядку."""
    steps = subclass_rules.taken(content, character)
    klass = content.character_class(character.class_id)
    if not steps:
        return f"Вы {klass.name}. Ветвей вы пока не брали."
    road = " — ".join(f"{one.name} ({one.level})" for one in steps)
    return f"Ваша дорога: {klass.name} — {road}."


def working_lines(content: GameContent, character: Character) -> tuple[str, ...]:
    """Уклады, которые работают у этого персонажа прямо сейчас.

    Ветку берут раз в жизни, а её правило действует каждый бой, - и потому
    названо оно должно быть там, где о ветках и говорят.
    """
    taken = [one for one in subclass_rules.taken(content, character) if way_line(one)]
    if not taken:
        return ()
    return ("Работает сейчас:", *(f"{one.name}. {way_line(one)}" for one in taken))


def skill_line(content: GameContent, one: Subclass) -> str:
    """Чему ветка учит. Пусто — ничему, и это ошибка содержимого, а не экрана."""
    if not one.skill_code or not content.has_skill(one.skill_code):
        return ""
    skill = content.skill(one.skill_code)
    return f"Умение ветки: «{skill.name}». {skill.text}"


def way_line(one: Subclass) -> str:
    """Уклад ветки: чем она меняет сам бой (``rules/subclass_powers``).

    Главная строка карточки: прибавки говорят «насколько», а уклад - «как за неё
    играют». Пусто - ветка правил не меняет, и это ошибка содержимого.
    """
    if not one.power or not way_rules.has_power(one.power):
        return ""
    way = way_rules.power(one.power)
    return f"Уклад ветки — {way.name}: {way.text}"


def _offer_lines(content: GameContent, character: Character, one: Subclass) -> tuple[str, ...]:
    """Одна ветка на развилке: что даёт, чему учит, чего стоит."""
    lines = [f"{one.name} — {one.role}. {one.text}"]
    if one.lore:
        lines.append(one.lore)
    if way := way_line(one):
        lines.append(way)
    taught = skill_line(content, one)
    if taught:
        lines.append(taught)
    refused = subclass_rules.refusal(content, character, one)
    if refused:
        # Отказ уже называет счёт испытания, когда дело стало за ним: две строки
        # об одном и том же числе — это одна лишняя.
        lines.append(f"Пока нельзя: {refused}")
    else:
        done, total = subclass_rules.trial_progress(content, character, one)
        lines.append(f"Испытание пройдено: {done} из {total}.")
    return tuple(lines)


def subclass_screen(
    content: GameContent,
    character: Character,
    notice: str = "",
) -> Screen:
    """Развилка: взятое, открытая ступень и две ветки, между которыми выбирают."""
    tier = subclass_rules.open_tier(content, character)

    lines = [
        *head("Ступень.", notice),
        path_line(content, character),
        *working_lines(content, character),
    ]
    if tier is None:
        lines.append("Дерево пройдено до конца. Дальше растёт только то, что вы вложили.")
        return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines))

    offered = subclass_rules.offered(content, character, tier)
    if not offered:
        lines.append("На этой ступени выбирать пока не из чего.")
        return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines))

    lines.append(
        f"Открыта {tier_name(tier).lower()}: её берут с {offered[0].level} уровня, "
        "и выбранное на ней не меняют."
    )
    rows: list[tuple[Label, ...]] = []
    for one in offered:
        lines.extend(_offer_lines(content, character, one))
        if subclass_rules.refusal(content, character, one):
            rows.append((trial_label(one.name),))
        else:
            rows.append((choose_label(one.name), trial_label(one.name)))
    return Screen(id=ScreenId.SUBCLASS, lines=tuple(lines), rows=tuple(rows))


def _step_mark(character: Character, quest: Quest) -> str:
    """В каком состоянии шаг испытания: сдан, взят и досчитан, взят, не взят."""
    log = character.quests
    if log.is_done(quest.id):
        return "сдано"
    if not log.is_taken(quest.id):
        return "не взято"
    progress = log.progress(quest.id)
    counted = f"{progress} из {quest.target_count}"
    return f"готово, можно сдавать ({counted})" if progress >= quest.target_count else counted


def trial_screen(
    content: GameContent,
    character: Character,
    one: Subclass,
    notice: str = "",
) -> Screen:
    """Испытание одной ветки: три задания подряд, и берут их по порядку."""
    lines = [
        *head(f"Испытание: {one.name}.", notice),
        f"{one.role}. {one.text}",
        "Три задания подряд. Следующее открывается сданным предыдущим, "
        "а сдают их здесь же, у наставника.",
    ]
    quests = subclass_rules.trial_quests(content, one)
    for number, quest in enumerate(quests, start=1):
        lines.append(f"{number}. {quest.name} — {_step_mark(character, quest)}.")
        lines.append(quest.terms)

    rows: list[tuple[Label, ...]] = []
    step = subclass_rules.trial_step(content, character, one)
    if step is None:
        lines.append("Испытание пройдено целиком. Ветку берут на экране «Ступень».")
        return Screen(id=ScreenId.SUBCLASS_TRIAL, lines=tuple(lines), rows=())

    log = character.quests
    lines.append(f"Сейчас: {step.name}.")
    lines.extend(instructions(content, step))
    if not log.is_taken(step.id):
        rows.append((labels.TRIAL_TAKE,))
    elif log.progress(step.id) >= step.target_count:
        rows.append((labels.HAND_IN,))
    else:
        lines.append("Счёт двигается сам, пока вы делаете то, о чём здесь сказано.")
    return Screen(id=ScreenId.SUBCLASS_TRIAL, lines=tuple(lines), rows=tuple(rows))


def chosen_line(content: GameContent, one: Subclass) -> str:
    """Что сказать про взятую ветку, в одну строку."""
    taught = ""
    if one.skill_code and content.has_skill(one.skill_code):
        taught = f" Открыто умение «{content.skill(one.skill_code).name}» — учат у наставника."
    way = f" {way_line(one)}." if way_line(one) else ""
    return f"Вы стали: {one.name}. {one.text}{way}{taught}"
