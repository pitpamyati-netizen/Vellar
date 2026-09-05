"""Экраны умений: что изучено, что покупает очко и что лежит в панели.

Три экрана, и не больше, потому что форма самой панели не меняется никогда:

- **Умения** — все умения класса, ранг каждого и то, что сделало бы одно очко;
- **Слоты умений** — шесть боевых мест и расовое;
- **Грань** — единственный выбор третьего ранга.

Слот всегда держит свой номер и своё место, пустой он или нет, поэтому панель
можно один раз выучить по положению и не переучивать (правило доступности 7).

Пассивные умения слотов не занимают: изученное работает, и укладывать его
некуда. Три слота из шести означали только то, что половина потраченных очков
не считалась ни в одном бою.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import head
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

EMPTY_SLOT = "пусто"
CLEAR_SLOT = label("Освободить слот")

#: Разделы списка умений. Их ровно два, потому что умение либо жмут в бою, либо
#: оно работает само; третьего вида в игре нет.
ACTIVE_SECTION = "Боевые"
PASSIVE_SECTION = "Пассивные"
SKILL_SECTIONS: tuple[str, ...] = (ACTIVE_SECTION, PASSIVE_SECTION)


def matching_skills(
    content: GameContent, character: Character, pool: tuple[Skill, ...], state: PageState
) -> tuple[Skill, ...]:
    """Умения, прошедшие раздел и поиск.

    Ищут по названию и по описанию: игрок помнит «оглушает», а не «Удар щитом».
    """
    section = state.filters.category
    needle = state.filters.query.casefold().strip()
    return tuple(
        skill
        for skill in pool
        if (not section or section == (ACTIVE_SECTION if skill.is_active else PASSIVE_SECTION))
        and (not needle or needle in skill.name.casefold() or needle in skill.text.casefold())
    )


def points_word(count: int) -> str:
    """«одно очко», «два очка», «пять очков» - слово согласовано с числом."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} очков"
    last = count % 10
    if last == 1:
        return "одно очко" if count == 1 else f"{count} очко"
    if 2 <= last <= 4:
        return f"{count} очка"
    return f"{count} очков"


def branch_name(skill: Skill) -> str:
    """Ветвь умения словом. Пусто - умение вне классового дерева, как расовое."""
    branch = skill_rules.branch_of(skill)
    return skill_rules.BRANCH_NAMES[branch] if branch is not None else ""


def skill_state(content: GameContent, character: Character, skill: Skill) -> str:
    """Одна фраза, говорящая всё, что игроку нужно знать о положении умения.

    В том числе и то, что сделает нажатие: умение, ждущее свою грань, ведёт на
    вопрос о грани, а не поднимает ранг. Цена ранга растёт (ADR 0024), поэтому она
    называется вслух, а не подразумевается.
    """
    rules = content.rules
    if not skill_rules.is_known(character, skill.code):
        taken = skill_rules.fork_taken(content, character, skill)
        if taken is not None:
            return f"закрыто развилкой: взято {taken.name}"
        if not skill_rules.gate_met(content, character, skill):
            gate = skill_rules.gate_of(content, skill)
            return f"закрыто: нужно {points_word(gate)} в ветви «{branch_name(skill)}»"
        return f"не изучено, {points_word(skill_rules.cost_to_learn(content, character, skill))}"
    rank = character.loadout.rank_of(skill.code)
    if skill_rules.needs_edge(content, character, skill):
        return f"ранг {rank} из {rules.max_rank}, сначала выберите грань"
    if rank >= rules.max_rank:
        return f"ранг {rank} из {rules.max_rank}, выше некуда"
    cost = skill_rules.cost_to_learn(content, character, skill)
    return f"ранг {rank} из {rules.max_rank}, следующий за {points_word(cost)}"


def refusal(content: GameContent, character: Character, skill: Skill) -> str:
    """Почему очко не легло. Отказ обязан называть причину, а не просто случиться."""
    if skill_rules.is_known(character, skill.code):
        if character.loadout.rank_of(skill.code) >= content.rules.max_rank:
            return f"{skill.name} уже на высшем ранге."
    else:
        taken = skill_rules.fork_taken(content, character, skill)
        if taken is not None:
            return (
                f"{skill.name} и {taken.name} стоят на одной развилке, и {taken.name} "
                "уже взято. Разобрать его может наставник."
            )
        if not skill_rules.gate_met(content, character, skill):
            branch = skill_rules.branch_of(skill)
            have = skill_rules.branch_points(content, character)[branch] if branch else 0
            return (
                f"{skill.name} стоит выше по ветви «{branch_name(skill)}»: нужно "
                f"{points_word(skill_rules.gate_of(content, skill))} в ней, вложено {have}."
            )
    cost = skill_rules.cost_to_learn(content, character, skill)
    return (
        f"На {skill.name} нужно {points_word(cost)}, а есть {character.unspent_skill_points}. "
        "Очки дают за уровень, и их возвращает наставник."
    )


def weapon_demand(content: GameContent, skill: Skill) -> str:
    """Каким оружием это умение вообще работает. Пусто — любым и без оружия.

    Список читается со стороны умения, а не со стороны рук: экран умений
    открывают вне боя, и там важно, подо что умение брать, а не что надето сейчас.
    """
    if not skill.weapon_types:
        return ""
    wanted = ", ".join(
        content.weapon_type(type_id).name.lower()
        for type_id in skill.weapon_types
        if content.has_weapon_type(type_id)
    )
    return f"Работает только с таким оружием: {wanted}." if wanted else ""


def fork_note(content: GameContent, skill: Skill) -> str:
    """Чему это умение соперник. Пусто - оно ни с чем не спорит."""
    rivals = skill_rules.fork_rivals(content, skill)
    if not rivals:
        return ""
    names = ", ".join(rival.name for rival in rivals)
    return f"Развилка: или это, или {names}."


def skill_detail(content: GameContent, skill: Skill) -> str:
    """Строка под названием умения: что оно делает и чем его для этого держат."""
    parts = [skill.text, weapon_demand(content, skill), fork_note(content, skill)]
    return " ".join(part for part in parts if part)


def skill_entry_text(content: GameContent, character: Character, skill: Skill) -> str:
    kind = "боевое" if skill.is_active else "пассивное"
    branch = branch_name(skill)
    where = f"{kind}, ветвь «{branch}»" if branch else kind
    return f"{skill.name} — {where}, {skill_state(content, character, skill)}"


def branch_line(content: GameContent, character: Character) -> str:
    """Три числа, по которым читается вся сборка: во что вложены очки."""
    tally = skill_rules.branch_points(content, character)
    said = ", ".join(
        f"{skill_rules.BRANCH_NAMES[branch]} {tally[branch]}" for branch in skill_rules.BRANCHES
    )
    return f"Вложено по ветвям: {said}."


def skills_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Список, из которого тратят очко умений."""
    pool = matching_skills(content, character, skill_rules.teachable(content, character), state)
    entries = [
        ListEntry(
            key=skill.code,
            text=skill_entry_text(content, character, skill),
            detail=skill_detail(content, skill),
        )
        for skill in pool
    ]
    edge_due = [skill.name for skill in pool if skill_rules.needs_edge(content, character, skill)]
    # Вступление короткое нарочно: оно повторяется на каждой странице списка, а
    # места в сообщении столько же, сколько у самих умений. Устройство панели
    # рассказывает экран слотов - тот, на котором это и делают.
    lead = [
        notice or f"Умения. Очков умений: {character.unspent_skill_points}.",
        f"Ваш уровень: {character.level}. Пассивные умения слотов не занимают.",
        branch_line(content, character),
    ]
    if not character.unspent_skill_points:
        lead.append("Очко умений даёт каждый новый уровень.")
    if edge_due:
        lead.append(f"Ждут выбора грани: {', '.join(edge_due)}.")
    return paginated_screen(
        screen_id=ScreenId.SKILLS,
        title="Умения",
        entries=entries,
        state=state,
        lead_lines=lead,
        empty_text="Пока учить нечего, следующее умение откроется с уровнем.",
        extra_rows=((labels.SKILL_SLOTS,),),
        categories=SKILL_SECTIONS,
    )


def slot_label(content: GameContent, character: Character, slot: int) -> Label:
    """Кнопка слота несёт свой номер и то, что в нём лежит."""
    code = character.loadout.actives[slot]
    name = content.skill(code).name if code and content.has_skill(code) else EMPTY_SLOT
    return label(f"Боевой слот {slot + 1}: {name}")


def slots_screen(content: GameContent, character: Character, notice: str = "") -> Screen:
    """Панель ровно в том виде, в каком она будет выглядеть в бою."""
    rules = content.rules
    racial_code = character.loadout.racial
    racial = (
        content.skill(racial_code).name
        if racial_code and content.has_skill(racial_code)
        else EMPTY_SLOT
    )
    working = skill_rules.known_passives(content, character)
    lines = [
        *head("Слоты умений.", notice),
        "Нажмите слот, чтобы положить в него умение.",
        f"Боевых слотов {rules.active_slots}, расовый один, и он не меняется.",
        f"Расовое умение: {racial}.",
        "Пассивные умения слотов не занимают: изученное работает всегда.",
    ]
    if working:
        lines.append(
            "Работают сейчас: "
            + ", ".join(
                f"{skill.name}, ранг {character.loadout.rank_of(skill.code)}" for skill in working
            )
            + "."
        )
    rows: list[tuple[Label, ...]] = [
        (slot_label(content, character, slot),) for slot in range(rules.active_slots)
    ]
    return Screen(id=ScreenId.SKILL_SLOTS, lines=tuple(lines), rows=tuple(rows))


def pick_screen(
    content: GameContent,
    character: Character,
    slot: int,
    state: PageState,
    notice: str = "",
) -> Screen:
    """Что можно положить в слот: изученные боевые умения, и больше ничего."""
    available = skill_rules.equippable(content, character)
    entries = [
        ListEntry(
            key=skill.code,
            text=f"{skill.name} — ранг {character.loadout.rank_of(skill.code)}",
            detail=skill_detail(content, skill),
        )
        for skill in available
    ]
    return paginated_screen(
        screen_id=ScreenId.SKILL_PICK,
        title=f"Слот {slot + 1}, боевой",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Выберите умение для слота {slot + 1}.",
            "Умение занимает один слот: из другого оно уйдёт само.",
        ),
        empty_text="Изученных боевых умений нет. Сначала изучите их в разделе «Умения».",
        show_filters=False,
        extra_rows=((CLEAR_SLOT,),),
    )


def edge_label(edge_name: str) -> Label:
    return label(f"Грань: {edge_name}")


def edge_screen(content: GameContent, character: Character, skill: Skill) -> Screen:
    """Развилка третьего ранга. Два способа применять одно умение, а не два умения.

    Каждая грань говорит только то, что написано у неё в содержимом: усиление и
    скидка - такая же объявленная механика, как всё остальное, и попадают в
    описание грани, а не после него (``domain/rules/edges.py``).
    """
    first, second = skill.edges
    return Screen(
        id=ScreenId.SKILL_EDGE,
        lines=(
            f"Грань умения {skill.name}. Выбирается один раз, на ранге "
            f"{skill_rules.edge_rank_for(content)}.",
            f"{first.name}: {first.text}",
            f"{second.name}: {second.text}",
            "Выбор грани ранга не повышает: ранг растёт за очко умений, как и раньше.",
            "Сменить грань потом сможет только наставник, и он берёт за это деньги.",
        ),
        rows=((edge_label(first.name),), (edge_label(second.name),)),
    )
