"""City services: tavern, mentor, vault and the dungeon gates.

First level of the four sections that used to be stubs (Roadmap 1.5). Each one
does one real thing and says plainly where it stops:

- **таверна** reads back the watch summary for the locations near the player;
- **наставник** puts an earned point into a stat;
- **банк** takes gold in for a duty and hands it back for nothing;
- **данжи** name the runs under the city and let a player look at the first floor.

Nothing here promises what it cannot do: a section that has a limit states the
limit in the same sentence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent
from mmorpg.domain.entities.location import GeneratedLocation, NodeKind
from mmorpg.domain.entities.stats import StatCode
from mmorpg.domain.procgen.dungeons import Dungeon
from mmorpg.domain.rules.bank import (
    DEPOSIT_FEE_PERCENT,
    TransferKind,
    VaultRefusal,
    largest_deposit,
    vault_limit,
)
from mmorpg.domain.rules.stats import primary_stats
from mmorpg.domain.rules.tavern import Rumour
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.creation import STAT_NAMES
from mmorpg.presentation.telegram.screens.format import gold as gold_words

# --- tavern -----------------------------------------------------------

ASK_PREFIX = "Расспросить: "


def ask_label(rumour: Rumour) -> Label:
    return label(f"{ASK_PREFIX}{rumour.location_name}")


def _rumour_line(rumour: Rumour) -> str:
    if rumour.quiet:
        return (
            f"{rumour.location_name}, уровни с {rumour.level_min} по {rumour.level_max}: "
            f"узлов {rumour.nodes}, ничего примечательного."
        )
    return (
        f"{rumour.location_name}, уровни с {rumour.level_min} по {rumour.level_max}: "
        f"узлов {rumour.nodes}, сильных противников {rumour.elites}, "
        f"тайников {rumour.caches}, святилищ {rumour.shrines}."
    )


def describe_rumour(rumour: Rumour) -> str:
    """The answer to pressing one rumour: the same numbers, said as advice."""
    parts = [f"{rumour.location_name}: узлов {rumour.nodes}."]
    if rumour.elites:
        parts.append(f"Сильных противников {rumour.elites} — идти с запасом зелий.")
    if rumour.caches:
        parts.append(f"Тайников {rumour.caches}.")
    if rumour.shrines:
        parts.append(f"Святилищ {rumour.shrines}, там переводят дух.")
    if rumour.quiet:
        parts.append("Ни сильного зверя, ни тайника — тихо.")
    parts.append("Сводка нынешней стражи, к концу стражи она устареет.")
    return " ".join(parts)


def tavern_screen(city: City, rumours: Sequence[Rumour], notice: str = "") -> Screen:
    lines = [
        notice or f"Таверна города {city.name}.",
        "Здесь читают сводку заставы. К середине стражи она уже врёт.",
    ]
    lines.extend(_rumour_line(rumour) for rumour in rumours)
    if not rumours:
        lines.append("Сводки нет: заставы молчат.")
    lines.append("Спросите хозяина о любой из них.")
    return Screen(
        id=ScreenId.TAVERN,
        lines=tuple(lines),
        rows=tuple((ask_label(rumour),) for rumour in rumours),
    )


def rumour_from_button(text: str, rumours: Sequence[Rumour]) -> Rumour | None:
    for rumour in rumours:
        if ask_label(rumour).matches(text):
            return rumour
    return None


# --- mentor -----------------------------------------------------------

RAISE_PREFIX = "Поднять "

# Accusative case, for "Поднять силу". A screen reader speaks the label as it is
# written, and "Поднять сила" is what the player would hear otherwise.
STAT_ACCUSATIVE: dict[StatCode, str] = {
    StatCode.STR: "силу",
    StatCode.AGI: "ловкость",
    StatCode.END: "выносливость",
    StatCode.INT: "интеллект",
    StatCode.WIS: "мудрость",
    StatCode.CHA: "харизму",
    StatCode.LCK: "удачу",
}


def raise_label(code: StatCode) -> Label:
    return label(f"{RAISE_PREFIX}{STAT_ACCUSATIVE[code]}")


def mentor_screen(
    content: GameContent, city: City, character: Character, notice: str = ""
) -> Screen:
    primary = primary_stats(content, character)
    values = ", ".join(f"{name.lower()} {primary[code]}" for code, name in STAT_NAMES.items())
    return Screen(
        id=ScreenId.MENTOR,
        lines=(
            notice or f"Наставник города {city.name}.",
            f"Свободных очков характеристик: {character.unspent_stat_points}.",
            f"Сейчас: {values}.",
            "Очко ставится сразу и насовсем: сброса наставник не делает.",
            "Переучивание умений здесь пока не берут.",
        ),
        rows=(
            (raise_label(StatCode.STR), raise_label(StatCode.AGI)),
            (raise_label(StatCode.END), raise_label(StatCode.INT)),
            (raise_label(StatCode.WIS), raise_label(StatCode.CHA)),
            (raise_label(StatCode.LCK),),
        ),
    )


def trained_line(content: GameContent, character: Character, code: StatCode) -> str:
    """What the mentor says once the point is placed - read off the new sheet."""
    primary = primary_stats(content, character)
    return (
        f"{STAT_NAMES[code]} теперь {primary[code]}. "
        f"Свободных очков: {character.unspent_stat_points}."
    )


def stat_from_button(text: str) -> StatCode | None:
    for code in STAT_ACCUSATIVE:
        if raise_label(code).matches(text):
            return code
    return None


# --- bank -------------------------------------------------------------

DEPOSIT_STEPS: tuple[int, ...] = (100, 1000)
DEPOSIT_ALL = label("Положить всё")
WITHDRAW_ALL = label("Снять всё")

_AMOUNT_COMMAND = re.compile(r"^/?(положить|снять)\s+(\d+)$", re.IGNORECASE)

VAULT_REFUSALS: dict[VaultRefusal, str] = {
    VaultRefusal.NOTHING: "Нечего класть и нечего снимать: назовите сумму больше нуля.",
    VaultRefusal.NO_GOLD: "На руках столько нет — сумма считается вместе с пошлиной.",
    VaultRefusal.NO_STORED: "В хранилище столько не лежит.",
    VaultRefusal.OVER_LIMIT: "Хранилище не примет столько: предел растёт с уровнем.",
}


def deposit_label(amount: int) -> Label:
    return label(f"Положить {amount}")


def withdraw_label(amount: int) -> Label:
    return label(f"Снять {amount}")


def bank_screen(city: City, character: Character, notice: str = "") -> Screen:
    limit = vault_limit(character.level)
    return Screen(
        id=ScreenId.BANK,
        lines=(
            notice or f"Банк города {city.name}.",
            f"На руках: {gold_words(character.gold)}. "
            f"В хранилище: {gold_words(character.bank_gold)}.",
            f"Предел хранилища на вашем уровне: {gold_words(limit)}.",
            f"Приём вклада: {DEPOSIT_FEE_PERCENT} процента с суммы, выдача бесплатна.",
            f"Сейчас можно положить не больше {largest_deposit(character)}: "
            "пошлина считается сверху.",
            "Сумму можно написать словами: «положить 250» или «снять 50».",
        ),
        rows=(
            tuple(deposit_label(step) for step in DEPOSIT_STEPS),
            (DEPOSIT_ALL,),
            tuple(withdraw_label(step) for step in DEPOSIT_STEPS),
            (WITHDRAW_ALL,),
        ),
    )


def parse_transfer(text: str, character: Character) -> tuple[TransferKind, int] | None:
    """Read a pressed button or a typed line as "put in" or "take out".

    Buttons and words go through the same door: the button text *is* the command
    (accessibility rule 10).
    """
    stripped = text.strip()
    if DEPOSIT_ALL.matches(stripped):
        return TransferKind.DEPOSIT, largest_deposit(character)
    if WITHDRAW_ALL.matches(stripped):
        return TransferKind.WITHDRAW, character.bank_gold

    match = _AMOUNT_COMMAND.match(stripped)
    if match is None:
        return None
    kind = (
        TransferKind.DEPOSIT if match.group(1).casefold() == "положить" else TransferKind.WITHDRAW
    )
    return kind, int(match.group(2))


# --- dungeons ---------------------------------------------------------

LOOK_SUFFIX = ", осмотреть вход"


def dungeon_label(dungeon: Dungeon) -> Label:
    return label(f"{dungeon.name}{LOOK_SUFFIX}")


def _dungeon_line(dungeon: Dungeon, character: Character) -> str:
    fit = "" if dungeon.covers(character.level) else ", не по вашему уровню"
    return (
        f"{dungeon.name}: уровни с {dungeon.level_min} по {dungeon.level_max}, "
        f"ярусов {dungeon.floors}, отряд от {dungeon.party}, "
        f"взнос {gold_words(dungeon.fee)}{fit}."
    )


def describe_entrance(dungeon: Dungeon, floor: GeneratedLocation) -> str:
    """What a player sees from the gate: the first floor, counted."""
    kinds = [node.kind for node in floor.nodes]
    return (
        f"{dungeon.name}, первый ярус: узлов {len(kinds)}, "
        f"сильных противников {kinds.count(NodeKind.ELITE_BATTLE)}, "
        f"тайников {kinds.count(NodeKind.CACHE)}. "
        f"Вниз идут отрядом от {dungeon.party}, взнос {gold_words(dungeon.fee)}. "
        "Спуск откроется, когда заработает сбор отряда."
    )


def dungeons_screen(
    city: City, dungeons: Sequence[Dungeon], character: Character, notice: str = ""
) -> Screen:
    lines = [
        notice or f"Данжи города {city.name}.",
        f"Ваш уровень: {character.level}. Вниз идут отрядом, в одиночку не пускают.",
    ]
    lines.extend(_dungeon_line(dungeon, character) for dungeon in dungeons)
    if not dungeons:
        lines.append("Под городом ходов нет.")
    lines.append("Вход можно осмотреть уже сейчас.")
    return Screen(
        id=ScreenId.DUNGEONS,
        lines=tuple(lines),
        rows=tuple((dungeon_label(dungeon),) for dungeon in dungeons),
    )


def dungeon_from_button(text: str, dungeons: Sequence[Dungeon]) -> Dungeon | None:
    for dungeon in dungeons:
        if dungeon_label(dungeon).matches(text):
            return dungeon
    return None
