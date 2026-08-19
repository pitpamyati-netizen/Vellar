"""Skill screens: what is known, what a point buys, and what sits in the panel.

Three screens, and no more, because the panel itself never changes shape:

- **Умения** - every skill of the class, its rank and what one point would do;
- **Слоты умений** - the six active, three passive and one racial position;
- **Грань** - the single rank-three choice.

A slot always keeps its number and its place, empty or not, so a player can learn
the panel by position once and never relearn it (accessibility rule 7).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, Skill, SkillKind
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
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
PASSIVE_SECTION = "Постоянные"
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


def skill_state(content: GameContent, character: Character, skill: Skill) -> str:
    """One phrase that says everything a player needs about a skill's standing."""
    if not skill_rules.is_known(character, skill.code):
        return "не изучено, одно очко умений"
    rank = character.loadout.rank_of(skill.code)
    if rank >= content.rules.max_rank:
        return f"ранг {rank} из {content.rules.max_rank}, выше некуда"
    return f"ранг {rank} из {content.rules.max_rank}, повысить за одно очко"


def skill_entry_text(content: GameContent, character: Character, skill: Skill) -> str:
    kind = "боевое" if skill.is_active else "постоянное"
    return f"{skill.name} — {kind}, {skill_state(content, character, skill)}"


def skills_screen(
    content: GameContent,
    character: Character,
    state: PageState,
    notice: str = "",
) -> Screen:
    """The list a skill point is spent from."""
    pool = matching_skills(content, character, skill_rules.teachable(content, character), state)
    entries = [
        ListEntry(
            key=skill.code,
            text=skill_entry_text(content, character, skill),
            detail=skill.text[:60],
        )
        for skill in pool
    ]
    edge_due = [skill.name for skill in pool if skill_rules.needs_edge(content, character, skill)]
    lead = [
        notice or f"Умения. Очков умений: {character.unspent_skill_points}.",
        f"В панели шесть боевых слотов и три постоянных, это не меняется. "
        f"Ваш уровень: {character.level}.",
    ]
    if not character.unspent_skill_points:
        lead.append("Очко умений даёт каждый новый уровень. Первое умение выдано без очков.")
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


def slot_label(content: GameContent, character: Character, kind: SkillKind, slot: int) -> Label:
    """A slot button carries its number, its kind and what is in it."""
    prefix = "Боевой" if kind is SkillKind.ACTIVE else "Постоянный"
    codes = character.loadout.actives if kind is SkillKind.ACTIVE else character.loadout.passives
    code = codes[slot]
    name = content.skill(code).name if code and content.has_skill(code) else EMPTY_SLOT
    return label(f"{prefix} слот {slot + 1}: {name}")


def slots_screen(content: GameContent, character: Character, notice: str = "") -> Screen:
    """The panel, exactly as it will look in a fight."""
    rules = content.rules
    racial_code = character.loadout.racial
    racial = (
        content.skill(racial_code).name
        if racial_code and content.has_skill(racial_code)
        else EMPTY_SLOT
    )
    lines = [
        notice or "Слоты умений. Нажмите слот, чтобы положить в него умение.",
        f"Боевых слотов {rules.active_slots}, постоянных {rules.passive_slots}, "
        "расовый один и меняться не может.",
        f"Расовое умение: {racial}.",
    ]
    rows: list[tuple[Label, ...]] = [
        (slot_label(content, character, SkillKind.ACTIVE, slot),)
        for slot in range(rules.active_slots)
    ]
    rows.extend(
        (slot_label(content, character, SkillKind.PASSIVE, slot),)
        for slot in range(rules.passive_slots)
    )
    return Screen(id=ScreenId.SKILL_SLOTS, lines=tuple(lines), rows=tuple(rows))


def pick_screen(
    content: GameContent,
    character: Character,
    kind: SkillKind,
    slot: int,
    state: PageState,
    notice: str = "",
) -> Screen:
    """What may go into one slot: known skills of the right kind, and nothing else."""
    available = skill_rules.equippable(content, character, kind)
    entries = [
        ListEntry(
            key=skill.code,
            text=f"{skill.name} — ранг {character.loadout.rank_of(skill.code)}",
            detail=skill.text[:60],
        )
        for skill in available
    ]
    prefix = "боевой" if kind is SkillKind.ACTIVE else "постоянный"
    return paginated_screen(
        screen_id=ScreenId.SKILL_PICK,
        title=f"Слот {slot + 1}, {prefix}",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Выберите умение для слота {slot + 1}.",
            "Умение занимает один слот: из другого оно уйдёт само.",
        ),
        empty_text="Изученных умений этого вида нет. Сначала изучите их в разделе «Умения».",
        show_filters=False,
        extra_rows=((CLEAR_SLOT,),),
    )


def edge_label(edge_name: str) -> Label:
    return label(f"Грань: {edge_name}")


def edge_screen(content: GameContent, character: Character, skill: Skill) -> Screen:
    """The rank-three fork. Two ways to use the same skill, not two skills."""
    first, second = skill.edges
    return Screen(
        id=ScreenId.SKILL_EDGE,
        lines=(
            f"Грань умения {skill.name}. Выбирается один раз, на ранге "
            f"{skill_rules.edge_rank_for(content, character)}.",
            f"{first.name}: {first.text} Бьёт сильнее на "
            f"{round(skill_rules.EDGE_POWER_BONUS * 100)} процентов.",
            f"{second.name}: {second.text} Стоит дешевле на "
            f"{round((1 - skill_rules.EDGE_COST_FACTOR) * 100)} процентов.",
            "Сменить грань потом сможет только наставник, и он берёт за это деньги.",
        ),
        rows=((edge_label(first.name),), (edge_label(second.name),)),
    )
