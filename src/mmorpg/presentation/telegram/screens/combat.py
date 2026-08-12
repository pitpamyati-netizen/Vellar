"""The combat screen.

Shape fixed by the specification (docs/skills.md): a status block first, then the
basic attack, then six numbered skill slots in fixed positions, then the racial
slot, then bag and flee.

Empty slots are rendered. A skill on cooldown keeps its position and says so in
its own label. Nothing here depends on colour or on an icon (accessibility rules
5, 6 and 7).
"""

from __future__ import annotations

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import CombatEvent, CombatState, EventKind
from mmorpg.domain.entities.content import GameContent
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, turns

EMPTY_SLOT = "Пустой слот"
READY = "готово"


def skill_label(content: GameContent, character: Character, state: CombatState, slot: int) -> Label:
    """One panel button. The number prefix keeps every label unique and stable."""
    code = character.loadout.actives[slot]
    if code is None:
        return label(f"{slot + 1}. {EMPTY_SLOT}")

    skill = content.skill(code)
    cooldown = state.player.cooldown_of(code)
    if cooldown > 0:
        return label(f"{slot + 1}. {skill.name} — откат {turns(cooldown)}")
    if skill.cost > state.player.resource:
        return label(f"{slot + 1}. {skill.name} — не хватает ресурса, нужно {skill.cost}")
    return label(f"{slot + 1}. {skill.name} — {READY}, {skill.cost} ресурса")


def racial_label(content: GameContent, character: Character, state: CombatState) -> Label:
    code = character.loadout.racial
    if code is None:
        return label(f"Расовое умение — {EMPTY_SLOT.lower()}")
    skill = content.skill(code)
    cooldown = state.player.cooldown_of(code)
    suffix = f"откат {turns(cooldown)}" if cooldown else READY
    return label(f"{skill.name} — расовое, {suffix}")


def describe_event(event: CombatEvent) -> str:
    """Events carry no prose; the sentences are written here."""
    match event.kind:
        case EventKind.DAMAGE:
            return f"{event.actor} наносит {event.target}: {event.amount} урона."
        case EventKind.CRIT:
            return f"{event.actor} критически бьёт {event.target}: {event.amount} урона."
        case EventKind.MISS:
            return f"Промах по цели {event.target}."
        case EventKind.DODGE:
            return f"{event.target} уклоняется от удара, {event.actor} не попадает."
        case EventKind.HEAL:
            return f"{event.actor} восстанавливает {event.amount} здоровья."
        case EventKind.SHIELD:
            return f"{event.actor} получает щит на {event.amount}."
        case EventKind.EFFECT_APPLIED:
            target = event.target or event.actor
            return f"{target}: наложен эффект {event.effect_name} на {turns(event.turns)}."
        case EventKind.CLEANSED:
            return f"Снято отрицательных эффектов: {event.amount}."
        case EventKind.STUNNED:
            return f"{event.target} пропускает {turns(event.turns)}."
        case EventKind.RESOURCE:
            return f"{event.actor} восстанавливает {event.amount} ресурса."
        case EventKind.ENEMY_DEFEATED:
            return f"{event.target} повержен."
        case EventKind.PLAYER_DEFEATED:
            return "Вы проиграли бой."
        case EventKind.FLED:
            return "Вы сбежали из боя."
        case EventKind.FLEE_FAILED:
            return "Сбежать не удалось."
        case EventKind.AVOIDED:
            return "Боя удалось избежать."
        case EventKind.NOT_ENOUGH_RESOURCE:
            return f"Не хватает ресурса на умение {event.skill_name}: нужно {event.amount}."
        case EventKind.ON_COOLDOWN:
            return f"Умение {event.skill_name} на откате ещё {turns(event.turns)}."
        case EventKind.EMPTY_SLOT:
            return "Этот слот пуст. Наберите умения в меню, вне боя."
        case EventKind.TURN_SKIPPED:
            return f"{event.actor} пропускает ход."
        case _:
            return ""


def combat_screen(
    content: GameContent, character: Character, state: CombatState, notice: str = ""
) -> Screen:
    enemies = state.living_enemies
    lead = notice or f"Бой. Ход {state.turn}."
    lines = [lead]
    for enemy in enemies:
        lines.append(f"{enemy.name}: здоровье {amount(enemy.health, enemy.enemy.max_health)}.")
    lines.append(
        f"Вы: здоровье {amount(state.player.health, state.player.max_health)}, "
        f"{state.player.resource_name.lower()} "
        f"{amount(state.player.resource, state.player.max_resource, with_percent=False)}."
    )
    # Only the last two events are read out: the message must stay short enough to
    # listen to before acting.
    lines.extend(text for event in state.events[-2:] if (text := describe_event(event)))
    lines.append("Ваш ход.")

    rows: list[tuple[Label, ...]] = [(labels.ATTACK,)]
    rows.extend(
        (skill_label(content, character, state, slot),)
        for slot in range(content.rules.active_slots)
    )
    rows.append((racial_label(content, character, state),))
    rows.append((labels.BAG, labels.FLEE))

    return Screen(id=ScreenId.COMBAT, lines=tuple(lines), rows=tuple(rows))


def bag_screen(
    content: GameContent, entries: tuple[tuple[str, str, int], ...], notice: str = ""
) -> Screen:
    """Consumables during a fight. They live here, never in a skill slot."""
    lines = [
        notice or "Сумка. Расходники доступны только отсюда.",
        f"Позиций в сумке: {len(entries)}.",
    ]
    rows: list[tuple[Label, ...]] = []
    for _item_id, name, quantity in entries:
        lines.append(f"{name}, штук {quantity}.")
        rows.append((label(f"{name} — использовать"),))
    if not entries:
        lines.append("Расходников нет.")
    return Screen(id=ScreenId.COMBAT_BAG, lines=tuple(lines), rows=tuple(rows))


def victory_screen(state: CombatState, level_up: str = "") -> Screen:
    lines = [
        "Победа.",
        f"Опыт: {state.experience}. Золото: {state.gold}.",
    ]
    if state.loot:
        lines.append(f"Добыча: {', '.join(state.loot)}.")
    if level_up:
        lines.append(level_up)
    lines.append("Нажмите «Назад», чтобы вернуться в локацию.")
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines))


def defeat_screen() -> Screen:
    return Screen(
        id=ScreenId.COMBAT,
        lines=(
            "Поражение.",
            "Вы приходите в себя в городе. Часть золота потеряна.",
            "Нажмите «Главное меню», чтобы продолжить.",
        ),
    )


def escaped_screen(fled: bool) -> Screen:
    return Screen(
        id=ScreenId.COMBAT,
        lines=(
            "Вы сбежали из боя." if fled else "Боя удалось избежать.",
            "Нажмите «Назад», чтобы вернуться в локацию.",
        ),
    )
