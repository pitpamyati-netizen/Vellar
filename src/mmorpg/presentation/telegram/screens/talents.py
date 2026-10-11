"""Читаемый выбор талантов с просмотром до замены."""

from __future__ import annotations

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


def entry(one: Talent, index: int, character: Character) -> str:
    status = "выбран" if one.code in character.loadout.talents else f"с уровня {one.level}"
    return f"{index}. {one.name} — {status}"


def talents_screen(
    content: GameContent, character: Character, page: PageState, notice: str = ""
) -> Screen:
    taken = rules.selected(content, character)
    unlocked = sum(level <= character.level for level in rules.LEVELS)
    lead = [
        *head("Таланты.", notice),
        f"Выбрано: {len(taken)} из {unlocked} доступных ступеней.",
        "Ступени открываются на уровнях 10, 30, 60, 100 и 150. На каждой выбирается "
        "один из трёх талантов.",
        "Очки умений не тратятся. Выбор можно бесплатно заменить вне боя.",
        "Прибавки талантов к основному действию складываются; общий предел — 60 процентов.",
        "Нажмите талант для просмотра. Команды: /таланты, /талант N, /выбратьталант.",
    ]
    return paginated_screen(
        screen_id=ScreenId.TALENTS,
        title="Таланты",
        entries=[
            ListEntry(one.code, entry(one, i, character), words(content, one))
            for i, one in enumerate(rules.pool(content, character), 1)
        ],
        state=page,
        lead_lines=lead,
        empty_text="У вашего класса пока нет талантов.",
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
                words(content, one),
                f"Ступень уровня {one.level}. Ваш уровень: {character.level}.",
            )
        )
        previous = next(
            (old for old in rules.selected(content, character) if old.level == one.level), None
        )
        if previous and previous.code == one.code:
            lines.append("Этот талант уже выбран.")
        elif character.level < one.level:
            lines.append(f"Выбор откроется на уровне {one.level}.")
        else:
            lines.append(
                f"Будет заменён талант «{previous.name}»."
                if previous
                else "Это первый выбор на этой ступени."
            )
            lines.append(
                "Замена бесплатна. Для подтверждения нажмите «Принять талант» или наберите "
                "/выбратьталант."
            )
            rows = ((APPLY,),)
    return Screen(ScreenId.TALENT, tuple(lines), rows)
