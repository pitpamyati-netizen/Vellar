"""The skills section: the panel as it stands, and every skill of the craft.

First level of the section (Roadmap 1.2): the panel is read back slot by slot -
including the empty slots, which keep their place and say so - and the whole class
list is here to be read, locked entries included. Picking six of eight and three
of six into the panel comes next; nothing on this screen pretends to do it yet.

Skills are listed through the shared paginated component, so the section behaves
like every other long list in the game.
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character, SkillLoadout
from mmorpg.domain.entities.content import GameContent, Skill, SkillKind
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.paginated import (
    ListEntry,
    PageState,
    paginated_screen,
)

KIND_WORDS: dict[SkillKind, str] = {
    SkillKind.ACTIVE: "активное",
    SkillKind.PASSIVE: "пассивное",
}

EMPTY_SLOT = "пусто"


def known_skills(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Everything the craft has, actives first, then passives, then the racial one.

    Skills above the character's level stay in the list: a player deciding where
    to spend the next ten levels needs to hear what waits there.
    """
    owner = f"class:{character.class_id}"
    actives = content.skills_of(owner, SkillKind.ACTIVE)
    passives = content.skills_of(owner, SkillKind.PASSIVE)
    racial = content.racial_active(character.race_id)
    ordered = sorted(actives, key=lambda skill: (skill.level, skill.name))
    ordered += sorted(passives, key=lambda skill: (skill.level, skill.name))
    return (*ordered, racial)


def _panel_line(content: GameContent, codes: tuple[str | None, ...]) -> str:
    parts = []
    for index, code in enumerate(codes, start=1):
        name = content.skill(code).name if code and content.has_skill(code) else EMPTY_SLOT
        parts.append(f"{index} {name}")
    return ", ".join(parts)


def _in_panel(loadout: SkillLoadout, skill: Skill) -> bool:
    return skill.code in {
        *loadout.equipped_actives(),
        *loadout.equipped_passives(),
        loadout.racial or "",
    }


def describe_skill(content: GameContent, character: Character, skill: Skill) -> str:
    """One skill read out in full - what the player hears after pressing it."""
    rank = character.loadout.rank_of(skill.code)
    lines = [f"{skill.name}, {KIND_WORDS[skill.kind]}, ранг {rank}.", skill.text]
    if skill.is_active:
        lines.append(f"Стоит {skill.cost}, откат {skill.cooldown}.")
    if skill.level > character.level:
        lines.append(f"Откроется на уровне {skill.level}.")
    return " ".join(lines)


def skills_screen(
    content: GameContent, character: Character, state: PageState, notice: str = ""
) -> Screen:
    loadout = character.loadout
    racial = content.skill(loadout.racial).name if loadout.racial else EMPTY_SLOT

    entries: list[ListEntry] = []
    for skill in known_skills(content, character):
        if skill.level > character.level:
            detail = f"откроется на уровне {skill.level}"
        elif _in_panel(loadout, skill):
            detail = "в панели"
        else:
            detail = "доступно"
        entries.append(
            ListEntry(key=skill.code, text=f"{skill.name}, {KIND_WORDS[skill.kind]}", detail=detail)
        )

    return paginated_screen(
        screen_id=ScreenId.SKILLS,
        title="Умения",
        entries=entries,
        state=state,
        lead_lines=(
            notice or f"Умения. {character.name}, уровень {character.level}.",
            f"Активные слоты: {_panel_line(content, loadout.actives)}.",
            f"Пассивные слоты: {_panel_line(content, loadout.passives)}.",
            f"Расовое умение: {racial}. Очков умений: {character.unspent_skill_points}.",
            "Нажмите умение, чтобы услышать, что оно делает. Набор панели появится позже.",
        ),
        empty_text="У вашего ремесла пока нет умений.",
        show_filters=False,
    )


def skill_from_button(content: GameContent, character: Character, text: str) -> Skill | None:
    """Match a pressed skill button back to its skill."""
    for skill in known_skills(content, character):
        if text.startswith(f"{skill.name}, "):
            return skill
    return None
