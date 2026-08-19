"""City services: the inn, the mentor, the strongbox and the descent.

Each one exists because something in the game costs money. The inn sells health,
the mentor sells a second opinion about a skill point, the strongbox keeps gold
out of reach of a bad fight, and the descent is where the money comes from.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import City, GameContent, Npc
from mmorpg.domain.rules import adventure
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.economy import inn_price, mentor_price
from mmorpg.domain.rules.quests import ready_to_hand_in
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, gold, plural
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

DEPOSIT_STEPS: tuple[int, ...] = (50, 250, 1000)


def tavern_screen(
    content: GameContent, character: Character, city: City, notice: str = ""
) -> Screen:
    """A bed, a board with contracts, and a clerk who pays for closed ones."""
    stats = derived_stats(content, character)
    health = character.health_or(stats.max_health)
    # Города считает тот, в котором игрок стоит, а не тот, где он завёлся: иначе
    # доска в чужом городе показывала чужие подряды и ноль своих.
    due = ready_to_hand_in(content, character, city.id)
    price = inn_price(character.level)

    lines = [
        notice or f"Таверна города {city.name}. Пахнет варевом и мокрой шерстью.",
        f"Здоровье: {amount(health, stats.max_health)}.",
        f"Комната на ночь: {gold(price)}. У вас {gold(character.gold)}.",
        "Солома во дворе бесплатна и лечит не всё.",
    ]
    if due:
        lines.append(f"Готовы к сдаче подряды: {len(due)}.")

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
    """Unlearning: the only way a spent skill point comes back."""
    price = mentor_price(character.level)
    entries = [
        ListEntry(
            key=code,
            text=forget_label(content.skill(code).name, character.loadout.rank_of(code)).text,
            detail=f"ранг {character.loadout.rank_of(code)}",
        )
        for code in sorted(skill_rules.known_codes(character))
        if content.has_skill(code)
    ]
    return paginated_screen(
        screen_id=ScreenId.MENTOR,
        title=f"Наставник, {city.name}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or "Наставник берёт деньгами и возвращает очками.",
            f"Разбор одного умения: {gold(price)}. У вас {gold(character.gold)}.",
            "Вместе с умением уходит и его грань, и место в панели.",
        ),
        empty_text="Вы пока ничего не изучили, разбирать нечего.",
        show_filters=False,
    )


def deposit_label(sum_: int) -> Label:
    return label(f"Положить {sum_}")


def withdraw_label(sum_: int) -> Label:
    return label(f"Забрать {sum_}")


def bank_screen(content: GameContent, character: Character, city: City, notice: str = "") -> Screen:
    """Gold in the strongbox is not on you, and a lost fight takes only what is."""
    lines = [
        notice or f"Банк Палаты, {city.name}. Стойка, весы, книга.",
        f"На руках: {gold(character.gold)}. В ячейке: {gold(character.bank_gold)}.",
        "За ячейку не берут: Палате важнее знать, у кого сколько.",
        "Проигранный бой забирает десятую часть того, что на руках. Ячейку не трогает.",
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
    lines = [notice or f"Жители города {city.name}.", f"Здесь стоят: {len(people)}."]
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

    Подряды у него те же, что на доске, и берутся тем же разговором: житель — не
    вторая доска, а лицо у той же работы (``Narrative.md``, раздел 4).
    """
    from mmorpg.presentation.telegram.screens.quests import quest_button

    offered = tuple(
        quest for quest in content.quests_of(npc.id) if quest_rules.is_open(quest, character)
    )
    lines = [notice or f"{npc.title}.", npc.text or "Молчит и смотрит мимо."]
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


def dungeon_screen(
    content: GameContent,
    character: Character,
    city: City,
    *,
    level: int,
    depth: int,
    total: int,
    notice: str = "",
) -> Screen:
    """The descent: fights in a row, no exit in the middle without loss.

    How many is not a constant: each Seal of the Chamber opens another layer
    below the old bottom, so the number is said rather than assumed
    (``domain/rules/turning.py``).
    """
    stats = derived_stats(content, character)
    health = character.health_or(stats.max_health)
    lines = [
        notice or f"Подземелья города {city.name}. Ход вниз, сквозняк, вода по щиколотку.",
        f"Спуск рассчитан на уровень {level}. Ваш уровень: {character.level}.",
        f"{total} {plural(total, 'схватка', 'схватки', 'схваток')} подряд, без "
        "передышки. Здоровье не восстанавливается между ними.",
        f"Здоровье: {amount(health, stats.max_health)}.",
        "Последний внизу — сильный противник, и за ним дно: "
        f"{adventure.descent_gold(level)} золота, опыт и находка сверх того, "
        "что возьмёте в схватках.",
        "Дно платит только тем, кто до него дошёл: уйти на середине — уйти со "
        "взятым в схватках и без этого.",
    ]
    if depth:
        lines.insert(1, f"Пройдено схваток: {depth} из {total}.")
    rows: list[tuple[Label, ...]] = [(labels.DUNGEON_ENTER,)]
    return Screen(id=ScreenId.DUNGEON, lines=tuple(lines), rows=tuple(rows))
