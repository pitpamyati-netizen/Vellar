"""Читаемый выбор талантов с просмотром до замены."""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Talent
from mmorpg.domain.entities.statuses import status_name
from mmorpg.domain.rules import talents as rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head
from mmorpg.presentation.telegram.screens.items import modifier_line
from mmorpg.presentation.telegram.screens.paginated import ListEntry, PageState, paginated_screen

APPLY = label("Принять талант")
RESET = label("Сбросить таланты")
CONFIRM_RESET = label("Подтвердить сброс талантов")


def words(content: GameContent, one: Talent) -> str:
    category = {
        "all": "все боевые умения класса и специализации",
        "damage": "атакующие умения",
        "heal": "лечащие умения",
        "barrier": "умения барьера",
        "buff": "усиления",
        "debuff": "помехи",
        "cleanse": "очищение",
        "special": "особые приёмы",
    }[one.category]
    match one.effect:
        case "power":
            said = f"{category}: основное действие сильнее на {one.amount:g} процентов"
        case "cost":
            said = f"{category}: расход запаса ниже на {one.amount:g} процентов"
        case "cooldown":
            said = f"{category}: откат короче на {one.amount:g} ход, минимум один ход"
        case "duration":
            said = (
                f"{category}: срок эффекта длиннее на {one.amount:g} ход; "
                "лишения хода это не касается"
            )
        case _:
            said = modifier_line(content, one.modifier, one.amount)
    statuses = " или ".join(f"«{status_name(status)}»" for status in one.statuses)
    condition = {
        "always": "",
        "actor_status": f", когда на вас есть {statuses}",
        "target_status": f", когда на цели есть {statuses}",
        "actor_health_below": f", когда у вас менее {one.threshold:g} процентов здоровья",
        "target_health_below": f", когда у цели менее {one.threshold:g} процентов здоровья",
        "actor_barrier": ", пока на вас есть барьер",
    }[one.condition]
    return said[:1].upper() + said[1:] + condition + "."


def branches(content: GameContent, character: Character) -> tuple[tuple[str, str], ...]:
    return tuple(
        dict.fromkeys((one.branch, one.branch_name) for one in rules.pool(content, character))
    )


def branch_entry(content: GameContent, character: Character, branch: str, name: str, i: int) -> str:
    return f"{i}. {name} — вложено {rules.spent(content, character, branch)} очков"


def entry(one: Talent, index: int, character: Character) -> str:
    rank = (
        character.loadout.talent_ranks.get(one.code, 0)
        if character.loadout.talent_tree_version
        else 0
    )
    return f"{index}. {one.name} — ранг {rank} из {one.max_rank}"


def talents_screen(
    content: GameContent, character: Character, page: PageState, notice: str = ""
) -> Screen:
    lead = [
        *head("Таланты.", notice),
        f"Очки талантов: вложено {rules.spent(content, character)} из {rules.points(character)}. "
        f"Свободно: {rules.points(character) - rules.spent(content, character)}.",
        "Первое очко — на уровне 10, затем одно каждые пять уровней. На уровне 150 — 29 очков.",
        "Три ветки можно сочетать. В каждой 15 талантов. Проходные таланты имеют два ранга; "
        "ключевой талант требует полный проходной талант.",
        "Очки умений и золото не тратятся. Сброс бесплатен вне боя.",
        "Откройте ветку: /веткаталантов N. Просмотр таланта: /талант N. Сброс: /сброситьталанты.",
    ]
    if rules.legacy_build(character):
        lead.extend(
            (
                "Прежний набор продолжает действовать. Перед первым вложением в дерево "
                "подтвердите переход через «Сбросить таланты». Прежние эффекты будут сняты.",
                *(
                    f"Сохранён талант «{one.name}». {words(content, one)}"
                    for one in rules.selected(content, character)
                ),
            )
        )
    rows = []
    for i, (branch, name) in enumerate(branches(content, character), 1):
        text = branch_entry(content, character, branch, name, i)
        lead.append(text + ".")
        rows.append((label(text),))
    rows.append((RESET,))
    return Screen(ScreenId.TALENTS, tuple(lead), tuple(rows))


def branch_screen(
    content: GameContent, character: Character, branch: str, page: PageState, notice: str = ""
) -> Screen:
    nodes = [
        (i, one) for i, one in enumerate(rules.pool(content, character), 1) if one.branch == branch
    ]
    name = nodes[0][1].branch_name if nodes else "Ветка больше не существует"
    lead = [
        *head(f"Ветка талантов: {name}.", notice),
        f"Вложено в ветку: {rules.spent(content, character, branch)}. "
        f"Свободных очков: {rules.points(character) - rules.spent(content, character)}.",
        "Открытие рядов: уровни 10, 30, 60, 100 и 150; вложения в предыдущие ряды "
        "этой ветки: 0, 4, 8, 12 и 16 очков.",
        "Нажмите талант для просмотра требований и действия. /талант N, /выбратьталант.",
    ]
    return paginated_screen(
        screen_id=ScreenId.TALENT_BRANCH,
        title=name,
        entries=[
            ListEntry(
                one.code,
                entry(one, i, character),
                f"Ряд {one.row}, уровень {one.level}. "
                + ("Проходной талант. " if one.max_rank > 1 else "Ключевой талант. ")
                + words(content, one)
                + (
                    " Доступен следующий ранг."
                    if not rules.blocked(content, character, one)
                    else " Требования — в карточке таланта."
                ),
            )
            for i, one in nodes
        ],
        state=page,
        lead_lines=lead,
        empty_text="Вернитесь к /таланты и выберите другую ветку.",
        show_filters=False,
    )


def talent_screen(
    content: GameContent, character: Character, code: str, notice: str = ""
) -> Screen:
    one = next((one for one in rules.pool(content, character) if one.code == code), None)
    lines = [*head("Выбор таланта.", notice)]
    rows: tuple[tuple[Label, ...], ...] = ()
    if one is None:
        lines.append("Этого таланта больше нет. Вернитесь к списку.")
    else:
        lines.extend(
            (
                one.name + ".",
                f"Ветка: {one.branch_name}. Ряд {one.row}. Уровень {one.level}. "
                f"Ваш уровень: {character.level}.",
            )
        )
        rank = rules.rank_of(content, character, code)
        lines.append(f"Текущий ранг: {rank} из {one.max_rank}.")
        if rank:
            lines.append(
                "Действует сейчас. " + words(content, replace(one, amount=one.amount * rank))
            )
        if rank < one.max_rank:
            lines.append(
                "Следующий ранг. " + words(content, replace(one, amount=one.amount * (rank + 1)))
            )
        lines.append(
            "Цена следующего ранга: одно очко талантов. Свободно: "
            f"{rules.points(character) - rules.spent(content, character)}."
        )
        by_code = {t.code: t for t in rules.pool(content, character)}
        if one.requires_all:
            lines.append(
                "Путь: изучите полностью "
                + ", ".join(by_code[c].name for c in one.requires_all)
                + "."
            )
        if one.requires_any:
            lines.append(
                "Путь: изучите полностью "
                + " или ".join(by_code[c].name for c in one.requires_any)
                + "."
            )
        if one.points_required:
            lines.append(f"В предыдущих рядах ветки нужно вложить {one.points_required} очков.")
        if one.choice_group:
            lines.append(
                "Развилка: выбирается только один из двух проходных талантов последнего ряда."
            )
        children = [t.name for t in by_code.values() if code in (*t.requires_all, *t.requires_any)]
        if children:
            lines.append("Полный ранг открывает путь к: " + ", ".join(children) + ".")
        if reasons := rules.blocked(content, character, one):
            lines.extend(reasons)
        else:
            lines.append("Подтвердите вложение: «Принять талант» или /выбратьталант.")
            rows = ((APPLY,),)
    return Screen(ScreenId.TALENT, tuple(lines), rows)


def reset_signature(character: Character) -> str:
    return repr(
        (
            character.loadout.talent_tree_version,
            character.loadout.talents,
            sorted(character.loadout.talent_ranks.items()),
        )
    )


def reset_screen(content: GameContent, character: Character, notice: str = "") -> Screen:
    lines = [*head("Сброс талантов.", notice)]
    if rules.legacy_build(character):
        lines.append(
            "Вы перейдёте к дереву талантов. Прежние выбранные таланты перестанут действовать."
        )
    else:
        lines.append("Все ранги талантов будут сняты. Эффекты талантов перестанут действовать.")
    lines.extend(
        (
            f"Для нового распределения будет доступно {rules.points(character)} очков талантов.",
            "Умения, ранги умений, грани, специализации, вещи и золото сохранятся. "
            "Сброс бесплатен.",
            "Подтвердите: /подтвердитьсбросталантов. «Назад» отменяет сброс.",
        )
    )
    return Screen(ScreenId.TALENT_RESET, tuple(lines), ((CONFIRM_RESET,),))
