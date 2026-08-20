"""The combat screen.

Shape fixed by the specification (docs/skills.md): a status block first, then the
basic attack, then six numbered skill slots in fixed positions, then the racial
slot, then bag and flee.

Empty slots are rendered. A skill on cooldown keeps its position and says so in
its own label. Nothing here depends on colour or on an icon (accessibility rules
5, 6 and 7).

The tag rules (intent, trace, breach) add no buttons: every tag is a word inside
a label the player already has, and the state of the trace is one spoken line.
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionTag,
    CombatEvent,
    CombatState,
    EnemyState,
    EventKind,
    Trace,
    counter_to,
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.location import EnemyRank
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules.combat import (
    MOMENTUM_DAMAGE_PERCENT,
    blow_range,
    enemy_intent,
)
from mmorpg.domain.rules.skill_effects import EffectCategory, EffectSpec, spec_for, tag_of_skill
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, head, percent, plural, turns

EMPTY_SLOT = "Пустой слот"
READY = "готово"

#: The domain keeps tags machine-readable; the Russian for them lives here.
TAG_NAMES: dict[ActionTag, str] = {
    ActionTag.PRESS: "натиск",
    ActionTag.GUARD: "оборона",
    ActionTag.PRECISION: "точность",
}

#: Enough of a tier to tell the player how long this will take.
RANK_NAMES: dict[EnemyRank, str] = {
    EnemyRank.NORMAL: "",
    EnemyRank.ELITE: "эпический",
    # Не «босс»: слово из мастерской, а не из Веллара, и карта локации всюду
    # зовёт его хозяином логова (``screens/play.py``).
    EnemyRank.BOSS: "хозяин логова",
}

#: What a modifier is called when a skill label has to name it. Only the keys
#: active skills actually apply are here; anything else falls back to a word for
#: the whole category.
MODIFIER_NAMES: dict[str, str] = {
    "damage_percent": "урон",
    "damage_taken_percent": "получаемый урон",
    "armor_percent": "броня",
    "dodge_percent": "уклонение",
    "accuracy_percent": "точность",
    "health_percent": "здоровье",
    "initiative_percent": "инициатива",
    "resource_regen_percent": "восстановление ресурса",
    "crit_chance_percent": "шанс критического удара",
    "lifesteal_percent": "вампиризм",
}

#: Skills whose whole point is a rule, not a number.
SPECIAL_NAMES: dict[str, str] = {
    "evade_next": "уклонение от следующего удара",
    "free_cast": "следующее умение бесплатно",
    "cooldown_reset": "снимает все откаты",
    "full_heal": "полное лечение",
    "avoid_combat": "попытка уйти из боя",
    "steal_gold": "кража золота",
    "taunt": "враг переключается на вас",
    "counter": "ответный удар",
    "unstunnable": "вас нельзя оглушить",
    "companion": "зверь бьёт за вас",
    "heal_over_time": "лечение по ходам",
    "cleanse_and_shield": "снимает эффекты и даёт щит",
    "haste_self": "ускоряет вас",
}


def attack_label() -> Label:
    """The basic attack carries a tag too, so its label says which."""
    return label(
        f"{labels.ATTACK.text} — {TAG_NAMES[ActionTag.PRESS]}",
        labels.ATTACK.emoji,
    )


def skill_effect(
    content: GameContent, character: Character, state: CombatState, skill: Skill
) -> str:
    """What this skill will do, in numbers the player can compare.

    The figure is the blow before armour, dodge and criticals - it is a promise
    about the skill, not a prediction about the turn. Without it a panel of six
    buttons says only "натиск, готово" six times over, which is exactly the
    complaint this screen exists to answer.

    Урон называется границами, а не одним числом: он бросается по костям оружия,
    и «урон 65» обещало бы точность, которой нет. «Урон от 34 до 96» - это ровно
    то, что случится.
    """
    spec = spec_for(skill.effect)
    power = skill.power_at_rank(character.loadout.rank_of(skill.code))

    if spec.category is EffectCategory.DAMAGE:
        low, high = blow_range(content, character, state.player.effects, skill.scaling)
        share = power / 100.0 * spec.damage_scale
        rank_scale = 1.0 + skill.rank_step * (character.loadout.rank_of(skill.code) - 1)
        extra = skill.dice
        least = max(1, round(low * share + (extra.low * rank_scale if extra else 0)))
        most = max(least, round(high * share + (extra.high * rank_scale if extra else 0)))
        line = f"урон от {least} до {most}"
        if spec.hits > 1:
            line = f"{line}, {spec.hits} раза"
        if spec.aoe:
            line = f"{line} по всем"
        return _with_extras(line, spec)

    if spec.category is EffectCategory.HEAL:
        return f"лечит {round(state.player.max_health * power / 100.0)}"
    if spec.category is EffectCategory.SHIELD:
        return _with_extras(f"щит {round(state.player.max_health * power / 100.0)}", spec)
    parts = (_modifier_phrase(spec, power), SPECIAL_NAMES.get(spec.special, ""))
    return ", ".join(part for part in parts if part) or "особое действие"


def _with_extras(line: str, spec: EffectSpec) -> str:
    """The riders that change how a blow is chosen, not how big it is."""
    extras = []
    if spec.pierce:
        extras.append("пробивает броню")
    if spec.stun_turns:
        extras.append(f"цель пропускает {turns(spec.stun_turns)}")
    if spec.dot_turns:
        extras.append(f"и ещё {turns(spec.dot_turns)}")
    if spec.special in SPECIAL_NAMES and spec.category is not EffectCategory.SPECIAL:
        extras.append(SPECIAL_NAMES[spec.special])
    if spec.cleanse_count and spec.category is not EffectCategory.CLEANSE:
        extras.append("снимает эффекты")
    return ", ".join([line, *extras])


def _modifier_phrase(spec: EffectSpec, power: float) -> str:
    """Buffs and debuffs, named by what they move and by how much.

    Empty when the skill moves nothing measurable - the caller then falls back to
    the word for the rule the skill actually is.
    """
    if spec.cleanse_count:
        return f"снимает до {spec.cleanse_count} отрицательных эффектов"

    bundle = spec.self_modifiers or spec.target_modifiers
    whose = "вам" if spec.self_modifiers else "цели"
    if not bundle:
        return ""

    if len(bundle) == 1:
        item = bundle[0]
        name = MODIFIER_NAMES.get(item.key, item.key)
        value = item.amount(power)
        phrase = f"{whose} {name} {'плюс' if value >= 0 else 'минус'} {percent(abs(value))}"
    else:
        many = "несколько усилений" if spec.self_modifiers else "несколько помех"
        phrase = f"{whose} {many}"
    return f"{phrase} на {turns(spec.duration)}" if spec.duration else phrase


def _weapon_status(content: GameContent, character: Character, skill: Skill) -> str:
    """Чего умению не хватает в руках. Пусто, когда хватает.

    Кнопка обязана обещать ровно то, что сделает (``Claude.md``, правило 9):
    «Прицельный выстрел» без лука не выстрелит, и сказать об этом надо до нажатия.
    """
    if not skill.weapon_types:
        return ""
    held = gear.weapon_type_of(content, character)
    if held in skill.weapon_types:
        return ""
    wanted = ", ".join(
        content.weapon_type(type_id).name.lower()
        for type_id in skill.weapon_types
        if content.has_weapon_type(type_id)
    )
    return f"нужно оружие: {wanted}"


def _slot_status(skill: Skill, state: CombatState) -> str:
    """Readiness, the price and the price of using it - always in words.

    A cooldown is stated twice on purpose: as what it will cost ("откат 3 хода")
    while the skill is ready, and as what is left ("ещё 2 хода") while it is not.
    Those are different questions and the player asks both.
    """
    cooldown = state.player.cooldown_of(skill.code)
    if cooldown > 0:
        return f"ещё {turns(cooldown)}"

    parts = []
    if skill.cost:
        parts.append(f"стоит {skill.cost}")
    if skill.cooldown:
        parts.append(f"откат {turns(skill.cooldown)}")
    if skill.cost > state.player.resource:
        parts.append(f"не хватает {skill.cost - state.player.resource}")
    else:
        parts.append(READY)
    return ", ".join(parts)


def skill_label(content: GameContent, character: Character, state: CombatState, slot: int) -> Label:
    """One panel button. The number prefix keeps every label unique and stable.

    A slot naming a skill the game no longer has reads as empty: a panel drawn
    from a loadout older than the content must not raise (rule 12).
    """
    code = character.loadout.actives[slot]
    if code is None or not content.has_skill(code):
        return label(f"{slot + 1}. {EMPTY_SLOT}")

    skill = content.skill(code)
    status = _weapon_status(content, character, skill) or _slot_status(skill, state)
    return label(
        f"{slot + 1}. {skill.name} — {TAG_NAMES[tag_of_skill(skill)]}, "
        f"{skill_effect(content, character, state, skill)}, {status}"
    )


def racial_label(content: GameContent, character: Character, state: CombatState) -> Label:
    code = character.loadout.racial
    if code is None or not content.has_skill(code):
        return label(f"Расовое умение — {EMPTY_SLOT.lower()}")
    skill = content.skill(code)
    return label(
        f"{skill.name} — расовое, {TAG_NAMES[tag_of_skill(skill)]}, "
        f"{skill_effect(content, character, state, skill)}, {_slot_status(skill, state)}"
    )


def enemy_line(enemy: EnemyState, turn: int) -> str:
    """Health, the announced intent and the tag that would open a breach."""
    intent = enemy_intent(enemy, turn)
    rank = RANK_NAMES[enemy.enemy.rank]
    title = f"{enemy.name} ({rank})" if rank else enemy.name
    return (
        f"{title}: здоровье {amount(enemy.health, enemy.enemy.max_health)}. "
        f"Намерение: {TAG_NAMES[intent]}, брешь — {TAG_NAMES[counter_to(intent)]}."
    )


def trace_line(trace: Trace) -> str:
    """Where the exchange stands, and what the next move would earn."""
    if trace.last is None:
        return (
            "След пуст. Повтор тега даёт разгон и усиливает удар, "
            "три разных тега подряд — перелом, и враги пропускают ход."
        )

    head = f"След: {TAG_NAMES[trace.last]}"
    if trace.streak > 1:
        marks = plural(trace.streak, "след", "следа", "следов")
        gain = percent(MOMENTUM_DAMAGE_PERCENT * (trace.streak - 1))
        head = f"{head}, {trace.streak} {marks} подряд, разгон {gain}"
    repeat = percent(MOMENTUM_DAMAGE_PERCENT * trace.streak)
    hints = [f"повтор даст разгон {repeat}"]
    hints.extend(f"{TAG_NAMES[tag]} даст перелом" for tag in ActionTag if trace.breaks_with(tag))
    return f"{head}. Дальше: {'; '.join(hints)}."


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
            return f"{event.actor} наносит {event.target}: {event.amount} урона."
        case EventKind.CRIT if you_hit:
            return f"Критический удар: {event.amount} урона, цель: {event.target}."
        case EventKind.CRIT:
            return f"{event.actor} критически бьёт {event.target}: {event.amount} урона."
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
        case EventKind.WRONG_WEAPON:
            # Отказ уже собран словами в домене: он знает и что умение просит, и
            # что сейчас в руке.
            return event.effect_name
        case EventKind.EMPTY_SLOT:
            return "Слот пуст. Наберите умения в меню, вне боя."
        case EventKind.TURN_SKIPPED:
            return f"{event.actor} пропускает ход."
        case EventKind.MOMENTUM:
            marks = plural(event.amount, "след", "следа", "следов")
            gain = percent(MOMENTUM_DAMAGE_PERCENT * (event.amount - 1))
            return f"Разгон: {event.amount} {marks} подряд, удар сильнее на {gain}."
        case EventKind.BREACH:
            return f"Брешь: {event.target} открылся, броня не в счёт, его удар вдвое слабее."
        case EventKind.BREAKTHROUGH:
            return "Перелом: враг сбит с ритма и в этот ход не отвечает."
        case _:
            return ""


def combat_screen(
    content: GameContent, character: Character, state: CombatState, notice: str = ""
) -> Screen:
    enemies = state.living_enemies
    lines = list(head(f"Бой. Ход {state.turn}.", notice))
    lines.extend(enemy_line(enemy, state.turn) for enemy in enemies)
    lines.append(
        f"Вы: здоровье {amount(state.player.health, state.player.max_health)}, "
        f"{state.player.resource_name.lower()} "
        f"{amount(state.player.resource, state.player.max_resource, with_percent=False)}."
    )
    lines.append(trace_line(state.trace))
    # Весь ход целиком, а не последние две строки. Обрезка съедала как раз то,
    # ради чего экран читают: удар игрока стоял первым, а разгон, брешь и ответ
    # врага выталкивали его наружу - и выходило, что игрок бьёт в тишину.
    # Ход - это несколько строк, и он и должен звучать целиком.
    lines.extend(
        text for event in state.events if (text := describe_event(event, state.player.name))
    )
    lines.append("Ваш ход.")

    rows: list[tuple[Label, ...]] = [(attack_label(),)]
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
        *head("Сумка. Расходники берут только отсюда.", notice),
        f"Позиций в сумке: {len(entries)}. Всё, что выпито в бою, оставляет след обороны.",
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
        # Без причастия: у персонажа может быть любой род, а русское прошедшее
        # время заставляет игру угадывать (``screens/group.py``).
        "Вы приходите в себя в городе. Раны перевязаны, дальше идти можно.",
    ]
    if gold_lost:
        lines.append(f"Потеряно золота: {gold_lost}. Ячейку в банке не трогает.")
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
