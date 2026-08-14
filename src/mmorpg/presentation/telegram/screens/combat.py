"""The combat screen.

Shape fixed by the specification (docs/skills.md): a status block first, then the
basic attack, then six numbered skill slots in fixed positions, then the racial
slot, then bag and flee.

Empty slots are rendered. A skill on cooldown keeps its position and says so in
its own label. Nothing here depends on colour or on an icon (accessibility rules
5, 6 and 7).
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import CombatEvent, CombatState, EventKind
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.rules import tempo
from mmorpg.domain.rules.tempo import Tag
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, turns

EMPTY_SLOT = "Пустой слот"
READY = "готово"

# The three tempo tags, as a player hears them (Roadmap 1.1). The domain speaks
# codes; the words live here, like every other piece of interface text.
TAG_WORDS: dict[str, str] = {
    Tag.PRESS.value: "натиск",
    Tag.GUARD.value: "оборона",
    Tag.AIM.value: "точность",
}

TAG_ACCUSATIVE: dict[str, str] = {
    Tag.PRESS.value: "натиск",
    Tag.GUARD.value: "оборону",
    Tag.AIM.value: "точность",
}


def trail_line(state: CombatState) -> str:
    """The status line the specification asks for: «След: натиск, 2 подряд»."""
    if not state.trail:
        return "След пуст: первое действие задаст его."
    last = TAG_WORDS.get(state.trail[-1], state.trail[-1])
    run = tempo.streak(state.trail)
    if tempo.is_break(state.trail):
        return f"След: {last}, три разных подряд — перелом."
    return f"След: {last}, {run} подряд."


def intent_line(state: CombatState) -> str:
    if not state.intent or not state.living_enemies:
        return ""
    who = state.living_enemies[0].name
    what = TAG_ACCUSATIVE.get(state.intent, state.intent)
    counter = TAG_WORDS[
        next(tag.value for tag, beaten in tempo.BEATS.items() if beaten.value == state.intent)
    ]
    return f"{who} готовит {what}. Сломает это {counter}."


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


def describe_event(event: CombatEvent, player: str = "") -> str:
    """Events carry no prose; the sentences are written here.

    ``player`` lets the line address the listener directly - "бьёт вас" reads
    better by ear than a name in the wrong case, and Russian names cannot be
    declined generically.
    """
    hit_you = bool(player) and event.target == player
    you_hit = bool(player) and event.actor == player
    match event.kind:
        case EventKind.DAMAGE if hit_you:
            return f"{event.actor} наносит вам {event.amount} урона."
        case EventKind.DAMAGE if you_hit:
            return f"Вы наносите {event.amount} урона, цель: {event.target}."
        case EventKind.DAMAGE:
            return f"{event.actor} наносит {event.amount} урона, цель: {event.target}."
        case EventKind.CRIT if you_hit:
            return f"Критический удар: {event.amount} урона, цель: {event.target}."
        case EventKind.CRIT:
            return f"{event.actor} бьёт критически: {event.amount} урона."
        case EventKind.MISS:
            return f"Промах по цели {event.target}."
        case EventKind.DODGE if hit_you:
            return f"Вы уклоняетесь, {event.actor} не попадает."
        case EventKind.DODGE:
            return f"{event.target} уклоняется от удара, {event.actor} не попадает."
        case EventKind.HEAL if you_hit:
            return f"Вы восстанавливаете {event.amount} здоровья."
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
            return "Слот пуст. Наберите умения в меню, вне боя."
        case EventKind.TURN_SKIPPED:
            return f"{event.actor} пропускает ход."
        case EventKind.INTENT:
            return f"{event.actor} готовит {TAG_ACCUSATIVE.get(event.tag, event.tag)}."
        case EventKind.MOMENTUM:
            return f"Разгон: {TAG_WORDS.get(event.tag, event.tag)}, {event.amount} подряд."
        case EventKind.BREAK:
            return "Перелом: три разных действия подряд, противник теряет ход."
        case EventKind.BREACH:
            return "Брешь: намерение сломано, броня не в счёт."
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
    lines.extend(
        text
        for event in state.events[-2:]
        if event.kind is not EventKind.INTENT and (text := describe_event(event, state.player.name))
    )
    lines.append(trail_line(state))
    if announcement := intent_line(state):
        lines.append(announcement)
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


def victory_screen(
    state: CombatState,
    level_up: str = "",
    extra: Sequence[str] = (),
    rows: Sequence[tuple[Label, ...]] = (),
    loot: Sequence[str] = (),
) -> Screen:
    """``loot`` is what the player hears: names, never content ids."""
    lines = [
        "Победа.",
        f"Опыт: {state.experience}. Золото: {state.gold}.",
    ]
    spoils = tuple(loot) or state.loot
    if spoils:
        lines.append(f"Добыча: {', '.join(spoils)}.")
    if level_up:
        lines.append(level_up)
    lines.extend(line for line in extra if line)
    lines.append(f"Здоровье после боя: {amount(state.player.health, state.player.max_health)}.")
    # A descent offers its own two buttons; everywhere else the way out is "Назад".
    lines.append(
        "Дальше вниз или наверх — решать сейчас." if rows else "Нажмите «Назад», чтобы вернуться."
    )
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines), rows=tuple(rows))


def defeat_screen(gold_lost: int = 0) -> Screen:
    lines = [
        "Поражение.",
        "Вы приходите в себя в городе, перевязанный и злой.",
    ]
    if gold_lost:
        lines.append(f"Потеряно золота: {gold_lost}. Ячейку в банке это не трогает.")
    lines.append("Нажмите «Главное меню», чтобы продолжить.")
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines))


def escaped_screen(fled: bool) -> Screen:
    return Screen(
        id=ScreenId.COMBAT,
        lines=(
            "Вы сбежали из боя." if fled else "Боя удалось избежать.",
            "Нажмите «Назад», чтобы вернуться в локацию.",
        ),
    )
