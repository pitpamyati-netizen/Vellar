"""Двор дома: чей это город, что даёт его техника и как вступить (ADR 0049)."""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent
from mmorpg.domain.rules import houses as house_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import gold, head


def house_screen(
    content: GameContent,
    character: Character,
    city: City,
    notice: str = "",
) -> Screen:
    """Двор дома в этом городе: техника, членство и кнопка вступить или уйти."""
    house = house_rules.house_of_city(content, city.id)
    if house is None:
        return Screen(
            id=ScreenId.HOUSE,
            lines=(*head("Двор дома.", notice), "Здесь нет двора великого дома."),
        )

    mine = house_rules.current_house(content, character)
    lines = [
        *head(f"Двор дома: {house.name}.", notice),
        f"Дом держит {house.name.replace('Дом ', '').lower()} и землю между двумя своими городами.",
        f"Техника «{house.technique.name}»: {house.technique.text}",
    ]

    rows: list[tuple[Label, ...]] = []
    if mine is not None and mine.id == house.id:
        lines.append("Вы в этом доме. Техника при вас, пока вы его не покинете.")
        rows.append((labels.HOUSE_LEAVE,))
    elif mine is not None:
        lines.append(
            f"Вы состоите в другом доме: {mine.name}. Уйти из него можно там же, "
            "где вступили, — бесплатно."
        )
    else:
        refused = house_rules.join_refusal(content, character, city.id)
        if refused:
            lines.append(refused)
        else:
            lines.append(
                f"Вступить: взнос {gold(house_rules.JOIN_FEE)}, с {house_rules.JOIN_LEVEL} уровня. "
                "Уйти потом можно бесплатно."
            )
            rows.append((labels.HOUSE_JOIN,))

    return Screen(id=ScreenId.HOUSE, lines=tuple(lines), rows=tuple(rows))
