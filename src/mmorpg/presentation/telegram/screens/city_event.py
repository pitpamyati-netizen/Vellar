"""Короткая общая сводка, действия и точная цена помощи."""

from mmorpg.domain.entities.city_event import CityEvent, CityEventState
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import city_event as rules
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head

OPEN = label("Городское дело")
BATTLE = label("Помочь боем")
SCOUT = label("Помочь разведкой")
CRAFT = label("Сделать партию городу")
CLAIM = label("Забрать награду города")
REFRESH = label("Обновить городское дело")


def overview(
    content: GameContent,
    event: CityEvent,
    state: CityEventState,
    character_id: int,
    notice: str = "",
) -> Screen:
    city = content.city(event.city_id)
    lines = [*head(f"Городское дело, {city.name}: {event.name}.", notice)]
    rows: list[tuple[Label, ...]] = []
    if state.stage < len(event.stages):
        stage = event.stages[state.stage]
        location = next(one for one in city.locations if one.slot == stage.location_slot)
        recipe = content.recipe(stage.recipe_id)
        lines.extend(
            (
                f"Этап {state.stage + 1} из {len(event.stages)}: {stage.name}.",
                stage.text,
                f"Общий вклад: {rules.progress(state, state.stage)} из {stage.target}.",
                "Бой: победите в стае с вашим участием. "
                "Разведка: исследуйте тайник, святилище или особое место. "
                f"Место помощи: {location.name}.",
                "Ремесло: изготовить партию и передать все изделия городу. Материалы: "
                + ", ".join(
                    f"{content.item(one.item_id).name}, {one.count}" for one in recipe.inputs
                )
                + ".",
                "Ваш вклад: "
                + "; ".join(
                    f"{title} {rules.used(state, state.stage, character_id, kind)} из {stage.cap}"
                    for kind, title in zip(rules.KINDS, ("бой", "разведка", "ремесло"), strict=True)
                )
                + ".",
                "За помощь этому этапу после его завершения полагается "
                f"{stage.reward_gold} золота один раз. Одного вклада достаточно.",
            )
        )
        rows.extend(((BATTLE, SCOUT), (CRAFT,)))
    else:
        lines.extend(
            (
                "Все этапы завершены. Переправа работает.",
                f"Проезд из города дешевле на {rules.discount(event, state)} процентов для всех.",
            )
        )
    for index, outcome in enumerate(state.outcomes):
        result = next(one.text for one in event.outcomes if one.kind == outcome)
        lines.append(f"Итог этапа {index + 1}: {result}")
    gold, _ = rules.reward_due(event, state, character_id)
    lines.extend(
        (
            f"К получению: {gold} золота.",
            "Срока участия нет. Обычные занятия доступны при любом итоге. "
            "Команды: /событие бой, /событие разведка, /событие ремесло, "
            "/событие награда, /событие обновить.",
        )
    )
    rows.extend(((CLAIM,), (REFRESH,)))
    return Screen(id=ScreenId.CITY_EVENT, lines=tuple(lines), rows=tuple(rows))
