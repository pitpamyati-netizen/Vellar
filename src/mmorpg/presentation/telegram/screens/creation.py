"""Экраны создания персонажа.

Каждый шаг говорит, и в таком порядке: где игрок, что уже выбрано и что можно
сделать сейчас (правило доступности 4). Уже сделанные выборы повторяются на
каждом шаге, поэтому возврат назад никогда не оставляет игрока гадать.
"""

from __future__ import annotations

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities.content import GameContent, Trait
from mmorpg.domain.entities.stats import StatBlock, StatCode
from mmorpg.presentation.telegram.keyboards.labels import (
    CLASS_DETAILS,
    CONFIRM,
    CONTINUE,
    RACE_DETAILS,
    Label,
    label,
)
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import percent
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    describe_selection,
    filters_screen,
    paginated_screen,
)

STAT_NAMES: dict[StatCode, str] = {
    StatCode.STR: "Сила",
    StatCode.AGI: "Ловкость",
    StatCode.END: "Выносливость",
    StatCode.INT: "Интеллект",
    StatCode.WIS: "Мудрость",
    StatCode.CHA: "Харизма",
    StatCode.LCK: "Удача",
}

# Дательный падеж, для «плюс 2 к ловкости». Экранный диктор это произносит, поэтому
# грамматика обязана быть верной: именительный в нижнем регистре даёт «плюс 2 к
# ловкость».
STAT_DATIVE: dict[StatCode, str] = {
    StatCode.STR: "силе",
    StatCode.AGI: "ловкости",
    StatCode.END: "выносливости",
    StatCode.INT: "интеллекту",
    StatCode.WIS: "мудрости",
    StatCode.CHA: "харизме",
    StatCode.LCK: "удаче",
}

# Genitive case, for "удар растёт от силы".
STAT_GENITIVE: dict[StatCode, str] = {
    StatCode.STR: "силы",
    StatCode.AGI: "ловкости",
    StatCode.END: "выносливости",
    StatCode.INT: "интеллекта",
    StatCode.WIS: "мудрости",
    StatCode.CHA: "харизмы",
    StatCode.LCK: "удачи",
}

# Что характеристика делает - и только то, что движок за ней действительно
# считает. Переносимого веса, сопротивлений, внушения и редкости добычи в игре
# нет, а обещаны они были все пять: «Интеллект: магический урон и мана» стоял
# перед магом, у которого запас зовётся Чарами, а «Мудрость: лечение» - перед
# жрецом, у которого лечение считается от здоровья и мудрости не видит.
STAT_EFFECTS: dict[StatCode, str] = {
    StatCode.STR: "тяжесть удара в ближнем бою",
    StatCode.AGI: "точность, уклонение и то, кто бьёт первым",
    StatCode.END: "здоровье и броню",
    StatCode.INT: "силу чар",
    StatCode.WIS: "восстановление ресурса за ход",
    StatCode.CHA: "цену в лавке: чем выше, тем дешевле отдают",
    StatCode.LCK: "как часто и как больно ложится критический удар",
}

RESET_POINTS = label("Сбросить очки")


def describe_bonuses(bonuses: StatBlock) -> str:
    """ "плюс 2 к ловкости, минус 1 к харизме" - spoken, never a table."""
    parts = [
        f"{'плюс' if value > 0 else 'минус'} {abs(value)} к {STAT_DATIVE[code]}"
        for code, value in bonuses
        if value
    ]
    return ", ".join(parts) if parts else "без изменений характеристик"


def chosen_so_far(content: GameContent, draft: CharacterDraft) -> str:
    """Одна строка, повторяющая каждое решение, принятое к этой минуте."""
    parts: list[str] = []
    if draft.name:
        parts.append(f"имя {draft.name}")
    if draft.race_id:
        parts.append(f"раса {content.race(draft.race_id).name}")
    if draft.class_id:
        parts.append(f"класс {content.character_class(draft.class_id).name}")
    if draft.trait_ids:
        names = ", ".join(content.trait(trait_id).name for trait_id in draft.trait_ids)
        parts.append(f"особенности {names}")
    return f"Выбрано: {'; '.join(parts)}." if parts else "Пока ничего не выбрано."


def name_screen(draft: CharacterDraft, notice: str = "") -> Screen:
    # Каждый экран создания держит те же ряды на тех же местах, закончен шаг или нет:
    # недоступное действие называет причину в теле и остаётся там, где игрок его оставил
    # (правило доступности 7).
    return Screen(
        id=ScreenId.CREATE_NAME,
        lines=(
            "Создание персонажа, шаг 1 из 5: имя.",
            notice or "Напишите имя сообщением. От 2 до 20 символов.",
            f"Текущее имя: {draft.name}." if draft.name else "Имя ещё не выбрано.",
            "Кнопка «Продолжить» станет доступна, когда имя будет принято."
            if not draft.name
            else "Нажмите «Продолжить», чтобы перейти к выбору расы.",
            "Кнопка «Назад» выйдет из создания персонажа.",
        ),
        rows=((CONTINUE,),),
    )


def race_screen(
    content: GameContent, draft: CharacterDraft, state: PageState, notice: str = ""
) -> Screen:
    entries = [
        ListEntry(
            key=race.id,
            text=race.name,
            detail=describe_bonuses(race.bonuses),
        )
        for race in content.races
    ]
    current = content.race(draft.race_id).name if draft.race_id else "не выбрана"
    return paginated_screen(
        screen_id=ScreenId.CREATE_RACE,
        title="Создание персонажа, шаг 2 из 5: раса",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Текущая раса: {current}.",
            "Нажмите расу, чтобы выбрать её."
            if not draft.race_id
            else "Нажмите «Продолжить» или выберите другую расу.",
        ),
        extra_rows=((RACE_DETAILS, CONTINUE),),
        show_filters=False,
    )


def race_details_screen(content: GameContent, race_id: str) -> Screen:
    race = content.race(race_id)
    active = content.skill(race.active_code)
    return Screen(
        id=ScreenId.CREATE_RACE_DETAILS,
        lines=(
            f"Раса: {race.name}.",
            race.description,
            f"Характеристики: {describe_bonuses(race.bonuses)}.",
            f"Пассивная способность: {race.passive.name}. {race.passive.text}",
            f"Расовое умение: {active.name}. {active.text}",
            "Расовое умение занимает отдельный слот и не мешает классовым.",
        ),
    )


def class_screen(content: GameContent, draft: CharacterDraft, notice: str = "") -> Screen:
    race = content.race(draft.race_id) if draft.race_id else None
    race_line = f"Раса: {race.name}, {describe_bonuses(race.bonuses)}." if race else ""
    current = content.character_class(draft.class_id).name if draft.class_id else "не выбран"
    rows: list[tuple[Label, ...]] = [
        (label(f"{klass.name} — {klass.role.lower()}"),) for klass in content.classes
    ]
    rows.append((CLASS_DETAILS, CONTINUE))
    return Screen(
        id=ScreenId.CREATE_CLASS,
        lines=(
            "Создание персонажа, шаг 3 из 5: класс.",
            notice or f"Текущий класс: {current}.",
            race_line,
            "Нажмите класс, чтобы выбрать его."
            if not draft.class_id
            else "Нажмите «Продолжить» или выберите другой класс.",
        ),
        rows=tuple(rows),
    )


def own_gear_line(content: GameContent, class_id: str) -> str:
    """Чем этот класс дерётся и в чём ходит - своим (ADR 0064).

    Доспех у класса один, и это надо услышать до выбора, а не на карточке первой
    находки: чужое не запрещено, но каждая чужая часть стоит точности и
    инициативы (``equipment.proficiency_penalty``).
    """
    klass = content.character_class(class_id)
    armour = ", ".join(
        content.armor_type(kind).name.lower()
        for kind in klass.armor_types
        if content.has_armor_type(kind)
    )
    weapons = ", ".join(
        content.weapon_type(kind).name.lower()
        for kind in klass.weapon_types
        if content.has_weapon_type(kind)
    )
    parts = []
    if armour:
        parts.append(f"Носит {armour}")
    if weapons:
        parts.append(f"оружие — {weapons}")
    if not parts:
        return ""
    return f"{', '.join(parts)}. Чужое надеть можно, но в нём точность и инициатива ниже."


def class_details_screen(content: GameContent, class_id: str) -> Screen:
    klass = content.character_class(class_id)
    key_stats = ", ".join(STAT_NAMES[code].lower() for code in klass.key_stats)
    return Screen(
        id=ScreenId.CREATE_CLASS_DETAILS,
        lines=(
            f"Класс: {klass.name}.",
            klass.description,
            # Роль тут не повторяется: она стоит прямо на кнопке, которой этот
            # класс выбирают. Вместо неё - то, чего кнопка сказать не может.
            klass.power,
            f"Ключевые характеристики: {key_stats}. Очки идут прежде всего в них.",
            own_gear_line(content, class_id),
            f"Ресурс: {klass.resource.name}. Умения тратят его, и он копится сам.",
            "Умений у класса шестьдесят: двадцать боевых и сорок пассивных. "
            "В панель встанут шесть боевых, и этот выбор — ваш почерк в бою; "
            "пассивные слота не занимают, изученное работает.",
        ),
    )


def trait_categories(content: GameContent) -> tuple[str, ...]:
    """Разделы, по которым режется список особенностей, в порядке содержимого."""
    seen: list[str] = []
    for trait in content.traits:
        name = content.trait_categories.get(trait.category, "")
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def matching_traits(content: GameContent, state: PageState) -> tuple[Trait, ...]:
    """Особенности, прошедшие раздел и поиск. Экран и автомат берут её одну.

    Поиск идёт и по названию, и по описанию: игрок помнит «уклонение», а не
    «Кошачья поступь», и должен найти её именно так.
    """
    category = state.filters.category
    needle = state.filters.query.casefold().strip()
    return tuple(
        trait
        for trait in content.traits
        if (not category or content.trait_categories.get(trait.category) == category)
        and (not needle or needle in trait.name.casefold() or needle in trait.text.casefold())
    )


def traits_screen(
    content: GameContent,
    draft: CharacterDraft,
    state: PageState,
    notice: str = "",
) -> Screen:
    required = content.rules.traits_at_creation
    pool = matching_traits(content, state)

    entries = [
        ListEntry(
            key=trait.id,
            text=("Выбрано: " if trait.id in draft.trait_ids else "") + trait.name,
            detail=trait.text,
        )
        for trait in pool
    ]
    ready = len(draft.trait_ids) == required
    return paginated_screen(
        screen_id=ScreenId.CREATE_TRAITS,
        title="Создание персонажа, шаг 4 из 5: особенности",
        entries=entries,
        state=state,
        lead_lines=(
            notice or describe_selection(len(draft.trait_ids), required),
            "Нажмите особенность, чтобы выбрать или снять выбор."
            if not ready
            else "Нажмите «Продолжить», чтобы перейти к распределению очков.",
        ),
        extra_rows=((CONTINUE,),),
        categories=trait_categories(content),
        empty_text="По этому поиску ничего нет. Сбросьте фильтры и попробуйте иначе.",
    )


def trait_filters_screen(content: GameContent, state: PageState) -> Screen:
    return filters_screen(
        screen_id=ScreenId.CREATE_TRAIT_FILTERS,
        title="Разделы особенностей",
        categories=trait_categories(content),
        current=state.filters,
    )


def points_screen(content: GameContent, draft: CharacterDraft, notice: str = "") -> Screen:
    budget = content.rules.free_points_at_creation
    left = draft.points_left(budget)
    base = content.rules.base_stat_value
    race = content.race(draft.race_id).bonuses
    klass = content.character_class(draft.class_id).bonuses

    rows: list[tuple[Label, ...]] = []
    lines = [
        "Создание персонажа, шаг 5 из 5: свободные очки.",
        notice or f"Осталось распределить: {left} из {budget}.",
        # Пять очков раздаются вслепую, если не сказать, чем этот класс бьёт.
        content.character_class(draft.class_id).power,
    ]
    for code, name in STAT_NAMES.items():
        total = base + race[code] + klass[code] + draft.allocated[code]
        lines.append(f"{name}: {total}. Влияет на {STAT_EFFECTS[code]}.")
        rows.append((label(f"{name} плюс один"),))
    lines.append(
        "Нажмите «Продолжить», чтобы перейти к подтверждению."
        if left == 0
        else f"Кнопка «Продолжить» станет доступна, когда останется 0 очков, сейчас {left}."
    )
    rows.append((RESET_POINTS, CONTINUE))

    return Screen(id=ScreenId.CREATE_POINTS, lines=tuple(lines), rows=tuple(rows))


def confirm_screen(content: GameContent, draft: CharacterDraft) -> Screen:
    race = content.race(draft.race_id)
    klass = content.character_class(draft.class_id)
    traits = ", ".join(content.trait(trait_id).name for trait_id in draft.trait_ids)
    allocated = ", ".join(
        f"плюс {value} к {STAT_DATIVE[code]}" for code, value in draft.allocated if value
    )
    return Screen(
        id=ScreenId.CREATE_CONFIRM,
        lines=(
            "Подтверждение персонажа.",
            f"Имя: {draft.name}.",
            f"Раса: {race.name}, {describe_bonuses(race.bonuses)}.",
            f"Класс: {klass.name}, ресурс {klass.resource.name}.",
            f"Особенности: {traits}.",
            f"Распределено: {allocated or 'ничего'}.",
            f"Расовое умение: {content.skill(race.active_code).name}.",
            "Нажмите «Подтвердить», чтобы начать игру.",
        ),
        rows=((CONFIRM,),),
    )


def trait_summary(content: GameContent, trait_id: str) -> str:
    """Берётся строкой-вестью после выбора."""
    trait = content.trait(trait_id)
    effects = ", ".join(
        f"{key.replace('_percent', '')} {percent(value)}" for key, value in trait.modifiers.items()
    )
    return f"{trait.name}: {effects}."
