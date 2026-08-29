"""Городские службы: постоялый двор, наставник, сундук и спуск.

Каждая из них существует потому, что что-то в игре стоит денег. Постоялый двор
продаёт здоровье, наставник - второе мнение об очке умений, сундук держит золото
подальше от проигранного боя, а спуск - это то, откуда деньги берутся.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, Dungeon, GameContent, Npc
from mmorpg.domain.rules import dungeon as dungeon_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.economy import inn_price, mentor_price
from mmorpg.domain.rules.quests import ready_to_hand_in
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens import dungeon as dungeon_screens
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold, head
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

DEPOSIT_STEPS: tuple[int, ...] = (50, 250, 1000)


def tavern_screen(
    content: GameContent, character: Character, city: City, notice: str = ""
) -> Screen:
    """Постель, доска с заданиями и писарь, который платит за закрытые."""
    stats = derived_stats(content, character)
    health = character.health_or(stats.max_health)
    # Города считает тот, в котором игрок стоит, а не тот, где он завёлся: иначе
    # доска в чужом городе показывала чужие задания и ноль своих.
    due = ready_to_hand_in(content, character, city.id)
    price = inn_price(character.level)

    lines = [
        *head(f"Таверна города {city.name}.", notice),
        "Пахнет варевом и мокрой шерстью, у стойки считают чужие деньги вслух.",
        f"Здоровье: {amount(health, stats.max_health)}.",
        f"Комната на ночь: {gold(price)}. У вас {gold(character.gold)}.",
        "Солома во дворе даётся даром и лечит не всё.",
    ]
    if due:
        lines.append(f"Готовы к сдаче задания: {len(due)}.")

    rows: list[tuple[Label, ...]] = [(labels.REST_PAID, labels.REST_FREE), (labels.QUEST_BOARD,)]
    if due:
        rows.append((labels.HAND_IN,))
    return Screen(id=ScreenId.TAVERN, lines=tuple(lines), rows=tuple(rows))


def forget_label(skill_name: str, refund: int) -> Label:
    return label(f"Забыть: {skill_name} — вернут {refund}")


def mentor_screen(
    content: GameContent,
    character: Character,
    city: City,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Роспуск: единственный способ вернуть потраченное очко умений.

    В списке только то, за что платили очком, - умения класса. Расовое стояло
    здесь наравне с ними, наставник брал за него деньги, объявлял «забыто» - и
    умение оставалось: расовый слот заводит ранг заново, а другого расового у
    этой расы нет (``domain/rules/skills.forget``). Кнопка, которая берёт плату
    и ничего не делает, - это баг (``Claude.md``, правило 9).
    """
    price = mentor_price(character.level)
    entries = [
        ListEntry(
            key=skill.code,
            text=forget_label(skill.name, character.loadout.rank_of(skill.code)).text,
            detail=f"ранг {character.loadout.rank_of(skill.code)}",
        )
        for skill in skill_rules.forgettable(content, character)
    ]
    return paginated_screen(
        screen_id=ScreenId.MENTOR,
        title=f"Наставник, {city.name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or "Наставник берёт деньгами и возвращает очками.",
            f"Разбор одного умения: {gold(price)}. У вас {gold(character.gold)}.",
            "Вместе с умением уходит и его грань, и место в панели, если оно там стояло.",
        ),
        empty_text="Разбирать нечего: расовое умение с вами останется в любом случае.",
        show_filters=False,
    )


def deposit_label(sum_: int) -> Label:
    return label(f"Положить {sum_}")


def withdraw_label(sum_: int) -> Label:
    return label(f"Забрать {sum_}")


def bank_screen(content: GameContent, character: Character, city: City, notice: str = "") -> Screen:
    """Золото в сундуке не при тебе, а проигранный бой берёт только то, что при тебе."""
    lines = [
        *head(f"Банк Палаты, {city.name}.", notice),
        "Стойка, весы и книга, в которую записывают всё до монеты.",
        f"На руках: {gold(character.gold)}. В ячейке: {gold(character.bank_gold)}.",
        "За саму ячейку не берут: Палате важнее знать, у кого сколько.",
        "Проигранный бой забирает десятую часть того, что на руках. Ячейку он не трогает.",
    ]
    rows: list[tuple[Label, ...]] = [
        tuple(deposit_label(step) for step in DEPOSIT_STEPS),
        tuple(withdraw_label(step) for step in DEPOSIT_STEPS),
    ]
    return Screen(id=ScreenId.BANK, lines=tuple(lines), rows=tuple(rows))


def npc_label(npc: Npc) -> Label:
    return label(npc.title)


def npcs_screen(content: GameContent, city: City, notice: str = "") -> Screen:
    """Кто стоит в этом городе.

    Экран появляется только там, где кто-то живёт: кнопка «Жители», за которой
    пусто, — это обещание, которого город не давал.
    """
    people = content.npcs_in(city.id)
    lines = [*head(f"Жители города {city.name}.", notice), f"Здесь стоят: {len(people)}."]
    lines.extend(f"{npc.title}." for npc in people)
    if not people:
        lines.append("Сейчас никого. Загляните позже.")
    return Screen(
        id=ScreenId.NPCS,
        lines=tuple(lines),
        rows=tuple((npc_label(npc),) for npc in people),
    )


def npc_screen(content: GameContent, character: Character, npc: Npc, notice: str = "") -> Screen:
    """Один человек: что говорит и что предлагает.

    Задания у него те же, что на доске, и берутся тем же разговором: житель — не
    вторая доска, а лицо у той же работы (``Narrative.md``, раздел 4).
    """
    from mmorpg.presentation.telegram.screens.quests import quest_button

    offered = tuple(
        quest for quest in content.quests_of(npc.id) if quest_rules.is_open(quest, character)
    )
    lines = [*head(f"{npc.title}.", notice), npc.text or "Молчит и смотрит мимо."]
    if offered:
        lines.append(f"Работа есть: {len(offered)}.")
    else:
        lines.append("Работы для вас у него сейчас нет.")
    lines.extend(f"{quest.name}: плата {quest.reward_gold}." for quest in offered)
    return Screen(
        id=ScreenId.NPC,
        lines=tuple(lines),
        rows=tuple((quest_button(quest),) for quest in offered),
    )


def dungeon_list_screen(
    content: GameContent,
    character: Character,
    city: City,
    *,
    page: PageState,
    base_depth: int,
    notice: str = "",
) -> Screen:
    """Список подземелий города: сперва выбирают куда, потом сложность (ADR 0041).

    У города несколько названных подземелий вразброс по его полосе и одно
    глубокое на самом верху, открытое дошедшему до последней локации. У каждого
    свой уровень, и он не растёт вместе с игроком: подземелье - это место
    (ADR 0019). Закрытые в список не попадают - только строкой.
    """
    depth = dungeon_rules.final_layer(base_depth, dungeon_rules.Difficulty.RECON)
    threshold = city.locations[-1].level_min

    lead = [
        "Комната за комнатой, после каждой — развилка: куда дальше, назад пути нет.",
        "Здоровье между боями не растёт — только зелья да передышки.",
        f"До логова около {depth} схваток. Дно за боссом платит лишь тому, кто дошёл.",
    ]
    entries: list[ListEntry] = []
    for one in city.dungeons:
        if not dungeon_rules.dungeon_unlocked(
            deep=one.deep,
            unlock_level=one.unlock_level,
            char_level=character.level,
            deep_threshold=threshold,
        ):
            need = threshold if one.deep else one.unlock_level
            lead.append(f"{one.name} — закрыт до уровня {need}.")
            continue
        note = " — вы его переросли" if not one.deep and character.level > one.level else ""
        entries.append(
            ListEntry(key=one.id, text=one.name, detail=f"уровень {one.level}{note}"),
        )

    return paginated_screen(
        screen_id=ScreenId.DUNGEON,
        title=f"Подземелья города {city.name}",
        entries=entries,
        state=page,
        lead_lines=tuple(lead) if not notice else (notice, *lead),
        empty_text="Ни одного подземелья вам ещё не открыто.",
        show_filters=False,
    )


def dungeon_pick_screen(
    content: GameContent,
    character: Character,
    city: City,
    dungeon: Dungeon,
    *,
    base_depth: int,
    notice: str = "",
) -> Screen:
    """Одно подземелье и три сложности (ADR 0041, ADR 0036)."""
    stats = derived_stats(content, character)
    health = character.health_or(stats.max_health)
    depth = dungeon_rules.final_layer(base_depth, dungeon_rules.Difficulty.RECON)
    outgrew = (
        " — вы переросли этот спуск, платит он по своему уровню"
        if not dungeon.deep and character.level > dungeon.level
        else ""
    )
    lines = [
        *head(f"{dungeon.name}.", notice),
        dungeon.flavour,
        f"Уровень спуска {dungeon.level}, ваш {character.level}{outgrew}.",
        f"До логова около {depth} схваток. У вас {amount(health, stats.max_health)} здоровья.",
        "Сложность домножает силу врагов и плату:",
    ]
    for difficulty in dungeon_screens.DIFFICULTY_ORDER:
        lines.append(
            f"— {dungeon_screens.DIFFICULTY_NAMES[difficulty]}: "
            f"{dungeon_screens.DIFFICULTY_FLAVOUR[difficulty]}"
        )
    rows = [
        (dungeon_screens.difficulty_label(difficulty),)
        for difficulty in dungeon_screens.DIFFICULTY_ORDER
    ]
    return Screen(id=ScreenId.DUNGEON_PICK, lines=tuple(lines), rows=tuple(rows))
