"""Экран боя.

Форма задана спецификацией (``docs/skills.md``): сперва положение дел, потом
обычный удар, потом занятые слоты своими номерами, потом расовое умение, потом
сумка и бегство.

Экран рисуется **для того, кто смотрит**: в бою четверых «вы» и «он» решаются
номером бойца, а не порядком в списке. Один и тот же бой звучит по-разному для
двух его участников, и это не украшение - иначе игрок не разберёт, кого ударили
(ADR 0021).

Пустой слот кнопки не получает. Номер он сохраняет - третье умение остаётся
третьим, - но кнопки, отвечающей «слот пуст», в панели нет: кнопка, которая
ничего не делает, - это баг (``Claude.md``, правило 9). Умение на откате
остаётся на месте и говорит об этом само.

Повадка противника не добавляет кнопок: она названа одним словом в строке о
нём, а что она делает, рассказывает «Разбор боя» (ADR 0066).
"""

from __future__ import annotations

from collections.abc import Sequence

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    BattleEvent,
    BattleState,
    Combatant,
    EventKind,
)
from mmorpg.domain.entities.content import GameContent, Skill
from mmorpg.domain.entities.location import EnemyRank, EnemyRole
from mmorpg.domain.entities.statuses import StatusKind, status_name
from mmorpg.domain.rules import equipment as gear
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.combat import (
    BASIC_ATTACK_PERCENT,
    CASTER_SWEEP_SCALE,
    HEALER_SHARE,
    ROLE_MOVE_EVERY,
    blow_range,
    defend_armor,
    defend_dodge,
    role_of,
    skill_cost,
)
from mmorpg.domain.rules.skill_effects import (
    EffectCategory,
    EffectSpec,
    cleansed_count,
    spec_for,
)
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label, label
from mmorpg.presentation.telegram.screens.base import Screen, ScreenId
from mmorpg.presentation.telegram.screens.format import amount, head, percent, turns

EMPTY_SLOT = "Пустой слот"
READY = "готово"
NEEDS_STEALTH = "нужна незаметность"

#: Домен держит повадки машинными; русские слова для них живут здесь.
ROLE_NAMES: dict[EnemyRole, str] = {
    EnemyRole.BRUTE: "громила",
    EnemyRole.WARRIOR: "воин",
    EnemyRole.ROGUE: "разбойник",
    EnemyRole.CASTER: "заклинатель",
    EnemyRole.HEALER: "знахарь",
}

#: Что повадка обещает - словами, описывающими ровно то, что делает движок
#: (``domain/rules/combat``: ``role_action``, ``_role_move``; ADR 0066).
ROLE_HINTS: dict[EnemyRole, str] = {
    EnemyRole.BRUTE: "бьёт всегда, а на исходе сил бьёт в полтора раза сильнее",
    EnemyRole.WARRIOR: "раненым закрывается: его придётся добивать",
    EnemyRole.ROGUE: "по тому, кому осталась треть, бьёт наверняка",
    EnemyRole.CASTER: (
        f"раз в {ROLE_MOVE_EVERY} круга бьёт всех сразу, вполсилы и своим родом урона"
    ),
    EnemyRole.HEALER: (
        f"раз в {ROLE_MOVE_EVERY} круга поднимает самого раненого в стае на четверть его здоровья"
    ),
}

#: Столько о ступени, сколько нужно, чтобы понять, сколько это займёт.
RANK_NAMES: dict[EnemyRank, str] = {
    EnemyRank.NORMAL: "",
    EnemyRank.ELITE: "эпический",
    # Не «босс»: слово из мастерской, а не из Веллара.
    EnemyRank.BOSS: "хозяин логова",
}

#: Как называется прибавка, когда метке умения приходится её назвать.
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

#: Умения, весь смысл которых - правило, а не число.
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
    "cleanse_and_barrier": "снимает эффекты и ставит барьер",
    "haste_self": "ускоряет вас",
}


def attack_label(content: GameContent, character: Character, viewer: Combatant) -> Label:
    """Обычный удар и то, сколько он снимет.

    Урон назван границами броска, как и у умений: он бросается костями оружия, и
    одно число обещало бы точность, которой нет. Броня цели и крит считаются
    после, поэтому это обещание об ударе, а не предсказание хода.
    """
    low, high = blow_range(content, character, viewer.effects)
    share = BASIC_ATTACK_PERCENT / 100.0
    least = max(1, round(low * share))
    most = max(least, round(high * share))
    return label(
        f"{labels.ATTACK.text} — урон от {least} до {most}",
        labels.ATTACK.emoji,
    )


def defend_label(viewer: Combatant) -> Label:
    """Закрыться: ход уходит целиком, и метка называет, что за него дают."""
    return label(
        f"{labels.DEFEND.text} — броня плюс {defend_armor(viewer.level)}, "
        f"уклонение плюс {percent(defend_dodge())} до вашего следующего хода",
        labels.DEFEND.emoji,
    )


def target_label(one: Combatant) -> Label:
    """Кнопка выбора цели: номер, имя и сколько цели осталось.

    Номер бойца делает метку неповторимой, а здоровье стоит прямо в кнопке:
    выбирают цель по нему, и искать его в строках выше не приходится.
    """
    left = amount(one.health, one.max_health, with_percent=False)
    return label(f"Цель {one.id}. {one.name}, здоровье {left}", "🎯")


def skill_effect(
    content: GameContent, character: Character, viewer: Combatant, skill: Skill
) -> str:
    """Что это умение сделает, числами, которые можно сравнить.

    Это обещание об умении, а не предсказание хода: броня, уклонение и крит
    считаются потом. Урон называется границами, а не одним числом, - он
    бросается костями оружия, и «урон 65» обещало бы точность, которой нет.
    """
    rank = character.loadout.rank_of(skill.code)
    # Сроки называются те, что даст ранг: карточка обязана обещать ровно то, что
    # сделает движок (``skill_rules.at_rank``, ADR 0067).
    spec = skill_rules.at_rank(spec_for(skill.effect), rank)
    power = skill.power_at_rank(rank)

    if spec.category is EffectCategory.DAMAGE:
        low, high = blow_range(content, character, viewer.effects, skill.scaling)
        share = power / 100.0 * spec.damage_scale
        rank_scale = 1.0 + skill.rank_step * (rank - 1)
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
        each = round(viewer.max_health * power / 100.0)
        line = f"лечит по {each} каждый ход" if spec.dot_turns else f"лечит {each}"
        if spec.aoe:
            line = f"{line}, вас и отряд"
        if spec.cleanse_count:
            line = f"{line}, снимает эффекты"
        return line
    if spec.category is EffectCategory.BARRIER:
        return _with_extras(f"барьер {round(viewer.max_health * power / 100.0)}", spec)
    parts = (
        *_status_phrases(spec),
        _modifier_phrase(spec, power),
        SPECIAL_NAMES.get(spec.special, ""),
    )
    return ", ".join(part for part in parts if part) or "особое действие"


def _status_phrases(spec: EffectSpec) -> tuple[str, ...]:
    """Состояния, которые умение вешает: на цель и на себя, произносимой строкой.

    Провокацию называют тем, что она делает, - уводит удар, - а не именем
    состояния: «цель бьёт по вам» игрок понимает сразу, «провокация цели» - нет.
    """
    out: list[str] = []
    for one in spec.inflicts:
        if one.kind is StatusKind.TAUNT:
            who = "все противники" if spec.aoe else "цель"
            verb = "бьют" if spec.aoe else "бьёт"
            out.append(f"{who} {verb} по вам на {turns(spec.duration or one.turns)}")
        else:
            reach = "по всем" if spec.aoe else "цели"
            out.append(f"{status_name(one.kind).lower()} {reach} на {turns(one.turns)}")
    out.extend(f"вам {status_name(one.kind).lower()} на {turns(one.turns)}" for one in spec.holds)
    return tuple(out)


def _with_extras(line: str, spec: EffectSpec) -> str:
    """Довески, которые меняют не размер удара, а то, как он выбирается."""
    extras = []
    if spec.pierce:
        extras.append("пробивает броню")
    if spec.stun_turns:
        extras.append(f"цель пропускает {turns(spec.stun_turns)}")
    if spec.dot_turns:
        extras.append(f"{status_name(spec.dot_status).lower()} на {turns(spec.dot_turns)}")
    extras.extend(
        f"{status_name(one.kind).lower()} цели на {turns(one.turns)}" for one in spec.inflicts
    )
    extras.extend(
        f"вам {status_name(one.kind).lower()} на {turns(one.turns)}" for one in spec.holds
    )
    if spec.special in SPECIAL_NAMES and spec.category is not EffectCategory.SPECIAL:
        extras.append(SPECIAL_NAMES[spec.special])
    if spec.cleanse_count and spec.category is not EffectCategory.CLEANSE:
        extras.append("снимает эффекты")
    return ", ".join([line, *extras])


def _modifier_phrase(spec: EffectSpec, power: float) -> str:
    """Усиления и помехи, названные тем, что двигают, и тем, насколько."""
    if spec.cleanse_count:
        return f"снимает до {cleansed_count(spec, power)} отрицательных эффектов"

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
    """Чего умению не хватает в руках. Пусто, когда хватает."""
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


def _stealth_status(skill: Skill, viewer: Combatant) -> str:
    """Чего умению не хватает из незаметности. Пусто, когда хватает."""
    if not skill.requires_stealth or viewer.effects.has(StatusKind.UNSEEN):
        return ""
    return NEEDS_STEALTH


def _slot_status(skill: Skill, character: Character, viewer: Combatant) -> str:
    """Готовность, цена и цена применения - всегда словами."""
    cooldown = viewer.cooldown_of(skill.code)
    if cooldown > 0:
        return f"ещё {turns(cooldown)}"

    rank = character.loadout.rank_of(skill.code)
    # Откат и цена считаются по рангу: ранг укорачивает первое и удешевляет
    # второе (``skill_rules.rank_gain``, ADR 0067).
    ready_in = skill_rules.cooldown_at_rank(skill, rank)
    parts = []
    # Цена объявлена долей запаса (ADR 0058), а игроку называется числом: доля -
    # это правило, а на экране стоит то, что сейчас спишут.
    price = round(skill_cost(skill, viewer.max_resource) * skill_rules.cost_factor(rank))
    if price:
        parts.append(f"стоит {price}")
    if ready_in:
        parts.append(f"откат {turns(ready_in)}")
    if price > viewer.resource:
        parts.append(f"не хватает {price - viewer.resource}")
    else:
        parts.append(READY)
    return ", ".join(parts)


def slotted_skill(content: GameContent, character: Character, slot: int) -> Skill | None:
    """Умение в этом слоте, если оно там есть и игра его знает."""
    code = character.loadout.actives[slot]
    if code is None or not content.has_skill(code):
        return None
    return content.skill(code)


def skill_label(content: GameContent, character: Character, viewer: Combatant, slot: int) -> Label:
    """Одна кнопка панели. Номер держит метку неповторимой и на месте."""
    skill = slotted_skill(content, character, slot)
    if skill is None:
        return label(f"{slot + 1}. {EMPTY_SLOT}")

    status = (
        _weapon_status(content, character, skill)
        or _stealth_status(skill, viewer)
        or _slot_status(skill, character, viewer)
    )
    return label(
        f"{slot + 1}. {skill.name} — {skill_effect(content, character, viewer, skill)}, {status}"
    )


def racial_skill(content: GameContent, character: Character) -> Skill | None:
    code = character.loadout.racial
    if code is None or not content.has_skill(code):
        return None
    return content.skill(code)


def racial_label(content: GameContent, character: Character, viewer: Combatant) -> Label:
    skill = racial_skill(content, character)
    if skill is None:
        return label(f"Расовое умение — {EMPTY_SLOT.lower()}")
    return label(
        f"{skill.name} — расовое, {skill_effect(content, character, viewer, skill)}, "
        f"{_slot_status(skill, character, viewer)}"
    )


# --- строки о бойцах --------------------------------------------------


def foe_line(state: BattleState, one: Combatant) -> str:
    """Здоровье противника и то, как он дерётся.

    Повадка названа одним словом (ADR 0066): что она значит, рассказывает
    «Разбор боя». У чужого персонажа повадки нет - за ним стоит игрок.
    """
    rank = RANK_NAMES[one.rank]
    title = f"{one.id}. {one.name} ({rank})" if rank else f"{one.id}. {one.name}"
    line = f"{title}: здоровье {amount(one.health, one.max_health)}"
    if one.barrier:
        line = f"{line}, барьер {one.barrier}"
    if held := status_line(one):
        line = f"{line}, {held}"
    if one.effects.has(StatusKind.UNSEEN):
        # Ушёл из виду: в бою он есть, а выбрать целью нельзя, пока не проявится.
        return f"{line}. Ушёл из виду: не выбрать, пока сам не проявится."
    role = role_of(one)
    if role is None:
        return f"{line}."
    # Что повадка сулит - на экране «Разбор боя», не абзацем в каждой строке.
    return f"{line}. Дерётся как {ROLE_NAMES[role]}."


def ally_line(one: Combatant, *, viewer_id: int) -> str:
    """Строка о своём: здоровье, запас, что висит. О себе - «вы».

    Мест в отряде нет, и называть у своего нечего, кроме имени и чисел: пятеро
    в отряде дерутся тем, что каждый принёс (``domain/rules/party.py``).
    """
    if one.id == viewer_id:
        line = f"Вы: здоровье {amount(one.health, one.max_health)}"
        if one.max_resource:
            line = (
                f"{line}, {one.resource_name.lower()} "
                f"{amount(one.resource, one.max_resource, with_percent=False)}"
            )
    else:
        line = f"{one.id}. {one.name}: здоровье {amount(one.health, one.max_health)}"
    if one.barrier:
        line = f"{line}, барьер {one.barrier}"
    if held := status_line(one):
        line = f"{line}, {held}"
    if not one.alive:
        return f"{line}. Вне боя."
    return f"{line}."


def status_line(one: Combatant) -> str:
    """Что висит на бойце, словами и с оставшимся сроком.

    Состояния читаются вслух вместе со здоровьем: игрок обязан слышать, что он
    горит, а не догадываться об этом по убывающей полоске (``docs/accessibility``).
    """
    held = sorted(one.effects.statuses(), key=lambda effect: effect.name)
    return ", ".join(
        f"{effect.name.lower()} {turns(effect.turns_left)}"
        for effect in held
        if effect.status is not None and effect.status is not StatusKind.BARRIER
    )


def describe_event(event: BattleEvent, viewer_id: int = 0) -> str:
    """События не несут слов; фразы пишутся здесь.

    ``viewer_id`` позволяет обращаться к слушателю напрямую - «бьёт вас» звучит
    лучше, чем имя в неверном падеже, а русские имена нельзя склонять наугад.
    """
    hit_you = bool(viewer_id) and event.target_id == viewer_id
    you_hit = bool(viewer_id) and event.actor_id == viewer_id
    match event.kind:
        case EventKind.DAMAGE if hit_you:
            actor = event.actor or "Что-то"
            return f"{actor} наносит вам {event.amount} урона."
        case EventKind.DAMAGE if you_hit:
            return f"Вы наносите {event.amount} урона, цель: {event.target}."
        case EventKind.DAMAGE if not event.actor:
            source = f" ({event.effect_name.lower()})" if event.effect_name else ""
            return f"{event.target} теряет {event.amount} здоровья{source}."
        case EventKind.DAMAGE:
            return f"{event.actor} наносит {event.target}: {event.amount} урона."
        case EventKind.CRIT if you_hit:
            return f"Критический удар: {event.amount} урона, цель: {event.target}."
        case EventKind.CRIT if hit_you:
            return f"{event.actor} критически бьёт вас: {event.amount} урона."
        case EventKind.CRIT:
            return f"{event.actor} критически бьёт {event.target}: {event.amount} урона."
        case EventKind.MISS if hit_you:
            return f"{event.actor} промахивается по вам."
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
        case EventKind.BARRIER if you_hit:
            return f"Вы получаете барьер на {event.amount}."
        case EventKind.BARRIER:
            return f"{event.actor} получает барьер на {event.amount}."
        case EventKind.EFFECT_APPLIED:
            target = event.target or event.actor
            if (event.target_id or event.actor_id) == viewer_id:
                target = "Вы"
            return f"{target}: наложен эффект {event.effect_name} на {turns(event.turns)}."
        case EventKind.STATUS_APPLIED:
            who = event.target or event.actor
            if (event.target_id or event.actor_id) == viewer_id:
                who = "Вы"
            return f"{who}: {event.effect_name.lower()} на {turns(event.turns)}."
        case EventKind.STATUS_ENDED:
            who = "Вы" if you_hit else event.actor
            return f"{who}: {event.effect_name.lower()} проходит."
        case EventKind.IMMUNE if hit_you:
            return f"{event.actor} бьёт вас, но вы неуязвимы."
        case EventKind.IMMUNE:
            return f"{event.target} неуязвим: удар не проходит."
        case EventKind.SILENCED if you_hit:
            return "Молчание: умением сейчас не воспользоваться."
        case EventKind.SILENCED:
            return f"{event.actor} молчит: умение не сработало."
        case EventKind.CLEANSED:
            return f"Снято отрицательных эффектов: {event.amount}."
        case EventKind.STUNNED if hit_you:
            return f"Вы пропускаете {turns(event.turns)}."
        case EventKind.STUNNED:
            return f"{event.target} пропускает {turns(event.turns)}."
        case EventKind.RESOURCE:
            return f"{event.actor} восстанавливает {event.amount} ресурса."
        case EventKind.DEFEATED if hit_you:
            return "Вы повержены."
        case EventKind.DEFEATED:
            return f"{event.target} повержен."
        case EventKind.FLED if you_hit:
            return "Вы вышли из боя."
        case EventKind.FLED:
            return f"{event.actor} выходит из боя."
        case EventKind.FLEE_FAILED if you_hit:
            return "Сбежать не удалось."
        case EventKind.FLEE_FAILED:
            return f"{event.actor}: уйти не вышло."
        case EventKind.YIELDED if you_hit:
            return "Вы сдались."
        case EventKind.YIELDED:
            return f"{event.actor} сдаётся."
        case EventKind.AVOIDED:
            return "Боя удалось избежать."
        case EventKind.NOT_ENOUGH_RESOURCE:
            return f"Не хватает ресурса на умение {event.skill_name}: нужно {event.amount}."
        case EventKind.ON_COOLDOWN:
            return f"Умение {event.skill_name} на откате ещё {turns(event.turns)}."
        case EventKind.WRONG_WEAPON:
            # Отказ уже собран словами в домене.
            return event.effect_name
        case EventKind.NEEDS_STEALTH:
            return f"Умению {event.skill_name} нужна незаметность."
        case EventKind.EMPTY_SLOT:
            return "Слот пуст. Наберите умения в меню, вне боя."
        case EventKind.NO_TARGET if event.actor and not you_hit:
            return f"{event.actor} не находит цели: бить некого."
        case EventKind.NO_TARGET:
            return "Этой цели в бою нет."
        case EventKind.TURN_SKIPPED if you_hit:
            reason = f": {event.effect_name.lower()}" if event.effect_name else ""
            return f"Вы пропускаете ход{reason}."
        case EventKind.TURN_SKIPPED:
            reason = f": {event.effect_name.lower()}" if event.effect_name else ""
            return f"{event.actor} пропускает ход{reason}."
        case EventKind.ROLE_MOVE:
            return f"{event.actor} берётся за своё."
        case EventKind.JOINED if you_hit:
            return "Вы вмешались в бой. Ваш ход придёт со следующего круга."
        case EventKind.JOINED:
            return f"{event.actor} вмешивается в бой и встаёт в очередь со следующего круга."
        case _:
            return ""


def turn_lines(state: BattleState, viewer_id: int = 0) -> tuple[str, ...]:
    """Последний ход словами - весь целиком, а не последние две строки."""
    return tuple(text for event in state.events if (text := describe_event(event, viewer_id)))


# --- сам экран --------------------------------------------------------


def affix_lines(content: GameContent, state: BattleState, viewer: Combatant) -> list[str]:
    """Что несёт каждое прозвище живых врагов, словами (ADR 0042).

    Имя врага уже с приставкой; здесь - что она делает, по одной строке на
    уникальное прозвище.
    """
    from mmorpg.presentation.telegram.screens import dungeon as dungeon_screens

    seen: set[str] = set()
    lines: list[str] = []
    for one in state.combatants:
        if one.side == viewer.side or not one.alive or one.enemy is None:
            continue
        for affix_id in one.enemy.affixes:
            if affix_id in seen or not content.has_affix(affix_id):
                continue
            seen.add(affix_id)
            lines.append(dungeon_screens.affix_line(content.affix(affix_id)))
    return lines


def _sides(content: GameContent, state: BattleState, viewer: Combatant) -> list[str]:
    lines: list[str] = []
    foes = tuple(one for one in state.combatants if one.side != viewer.side and one.alive)
    if foes:
        lines.append("Против вас:")
        lines.extend(foe_line(state, one) for one in foes)
        lines.extend(affix_lines(content, state, viewer))
    allies = tuple(
        one for one in state.combatants if one.side == viewer.side and one.id != viewer.id
    )
    if allies:
        lines.append("С вами:")
        lines.extend(ally_line(one, viewer_id=viewer.id) for one in allies)
    lines.append(ally_line(viewer, viewer_id=viewer.id))
    return lines


def _has_live_foes(state: BattleState, viewer: Combatant) -> bool:
    return any(one.live and one.side != viewer.side for one in state.combatants if one.alive)


def battle_screen(
    content: GameContent,
    character: Character,
    state: BattleState,
    viewer_id: int,
    notice: str = "",
) -> Screen:
    """Панель боя того, чей сейчас ход."""
    viewer = state.by_id(viewer_id)
    if viewer is None:  # pragma: no cover - зритель всегда участник
        return waiting_screen(content, state, viewer_id, notice)

    lines = list(head(f"Бой. Круг {state.round}.", notice))
    lines.extend(_sides(content, state, viewer))
    lines.extend(turn_lines(state, viewer_id))
    target = state.target_for(viewer_id)
    if target is not None:
        lines.append(f"Ваша цель: {target.id}. {target.name}.")
    lines.append("«Разбор боя» — кто перед вами и как он дерётся.")
    lines.append("Ваш ход.")

    rows: list[tuple[Label, ...]] = [
        (attack_label(content, character, viewer),),
        # Закрыться умеет всякий: умения на это не нужно, а ход стоит целиком.
        (defend_label(viewer),),
    ]
    # Только занятые слоты: номер за умением закреплён, а пустое место кнопки не
    # получает - нажатие на «Пустой слот» стоило игроку целого хода.
    rows.extend(
        (skill_label(content, character, viewer, slot),)
        for slot in range(content.rules.active_slots)
        if slotted_skill(content, character, slot) is not None
    )
    if racial_skill(content, character) is not None:
        rows.append((racial_label(content, character, viewer),))
    foes = state.visible_foes_of(viewer_id)
    if len(foes) > 1:
        # Выбор цели ходом не считается: он ничего не делает с боем, кроме того,
        # что игра начинает целиться туда, куда сказали. Ушедшего из виду в
        # списке нет - его не выбрать, пока он сам не проявится (ADR 0043).
        rows.extend((target_label(one),) for one in foes)
    rows.append((labels.BATTLE_BREAKDOWN,))
    rows.append((labels.BAG, labels.FLEE))
    if _has_live_foes(state, viewer):
        rows.append((labels.BATTLE_YIELD,))

    return Screen(id=ScreenId.COMBAT, lines=tuple(lines), rows=tuple(rows))


def waiting_screen(
    content: GameContent, state: BattleState, viewer_id: int, notice: str = ""
) -> Screen:
    """Экран того, чей ход ещё не наступил.

    Ждать приходится только живого игрока: за породу ходит движок, и его ходы
    приходят в том же сообщении, что и ваш. Ожидание не наказывается ничем -
    таймера нет, - но и молчать оно не должно: экран говорит, чей ход, что уже
    случилось и как из боя выйти (ADR 0021).
    """
    viewer = state.by_id(viewer_id)
    current = state.active
    who = current.name if current is not None else ""
    lines = list(head(f"Бой. Круг {state.round}. Ход: {who}.", notice))
    if viewer is not None:
        lines.extend(_sides(content, state, viewer))
    lines.extend(turn_lines(state, viewer_id))
    lines.append("Ждём его хода. Таймера нет: сколько нужно, столько и ждём.")
    lines.append("«Что там в бою» — перечитать, «Сдаться» — отдать бой и выйти.")
    rows: tuple[tuple[Label, ...], ...] = (
        (labels.BATTLE_REFRESH,),
        (labels.BATTLE_BREAKDOWN,),
        (labels.BATTLE_YIELD,),
    )
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines), rows=rows)


def breakdown_screen(
    content: GameContent,
    character: Character,
    state: BattleState,
    viewer_id: int,
    notice: str = "",
) -> Screen:
    """Кто перед вами: повадка каждого врага, что она делает, и его прозвища.

    Отдельный экран нарочно: на боевой панели это был бы абзац в каждом ходу, а
    на слух абзац между делом - стена. Механику не трогает ничто. Возврат -
    «Что там в бою».
    """
    viewer = state.by_id(viewer_id)
    lines = list(head(f"Бой. Разбор боя. Круг {state.round}.", notice))
    seen: list[EnemyRole] = []
    if viewer is not None:
        for one in state.combatants:
            if one.side == viewer.side or not one.alive or one.live:
                continue
            if one.effects.has(StatusKind.UNSEEN):
                continue
            role = role_of(one)
            if role is None:
                continue
            lines.append(f"{one.id}. {one.name}: {ROLE_NAMES[role]} — {ROLE_HINTS[role]}.")
            if role not in seen:
                seen.append(role)
        lines.extend(affix_lines(content, state, viewer))
    if not seen:
        lines.append("Повадок здесь нет: перед вами не порода, а такой же приключенец.")
    lines.append(
        f"Приём заклинателя бьёт всех на {percent(CASTER_SWEEP_SCALE * 100)} его удара, "
        f"рука знахаря возвращает {percent(HEALER_SHARE * 100)} здоровья, и оба приходят "
        f"раз в {ROLE_MOVE_EVERY} круга."
    )
    return Screen(
        id=ScreenId.COMBAT,
        lines=tuple(lines),
        rows=((labels.BATTLE_REFRESH,),),
    )


def bag_screen(
    content: GameContent, entries: tuple[tuple[str, str, int], ...], notice: str = ""
) -> Screen:
    """Расходники во время боя. Живут здесь, никогда в слоте умения."""
    lines = [
        *head("Сумка. Расходники берут только отсюда.", notice),
        f"Позиций в сумке: {len(entries)}. Всё, что выпито в бою, оставляет след заслона.",
    ]
    rows: list[tuple[Label, ...]] = []
    for _item_id, name, quantity in entries:
        lines.append(f"{name}, штук {quantity}.")
        rows.append((label(f"{name} — использовать"),))
    if not entries:
        lines.append("Расходников нет.")
    return Screen(id=ScreenId.COMBAT_BAG, lines=tuple(lines), rows=tuple(rows))


def victory_screen(
    state: BattleState,
    viewer_id: int,
    level_up: str = "",
    extra: Sequence[str] = (),
    rows: Sequence[tuple[Label, ...]] = (),
    loot: Sequence[str] = (),
    experience: int = 0,
    gold: int = 0,
) -> Screen:
    """``loot`` - то, что слышит игрок: имена, никогда не коды содержимого."""
    viewer = state.by_id(viewer_id)
    lines = [
        "Победа.",
        *turn_lines(state, viewer_id),
        f"Опыт: {experience}. Золото: {gold}.",
    ]
    if loot:
        lines.append(f"Добыча: {', '.join(loot)}.")
    if level_up:
        lines.append(level_up)
    lines.extend(line for line in extra if line)
    if viewer is not None:
        lines.append(f"Здоровье после боя: {amount(viewer.health, viewer.max_health)}.")
    lines.append(
        "Дальше вниз или наверх — решать сейчас." if rows else "Нажмите «Назад», чтобы вернуться."
    )
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines), rows=tuple(rows))


def defeat_screen(
    state: BattleState, viewer_id: int, gold_lost: int = 0, extra: Sequence[str] = ()
) -> Screen:
    lines = [
        "Поражение.",
        *turn_lines(state, viewer_id),
        # Без причастия: у персонажа может быть любой род, а русское прошедшее
        # время заставляет игру угадывать (``screens/group.py``).
        "Вы приходите в себя в городе. Раны перевязаны, дальше идти можно.",
    ]
    if gold_lost:
        lines.append(f"Потеряно золота: {gold_lost}. Ячейку в банке не трогает.")
    lines.extend(line for line in extra if line)
    lines.append("Нажмите «Главное меню», чтобы продолжить.")
    return Screen(id=ScreenId.COMBAT, lines=tuple(lines))


def escaped_screen(
    fled: bool, state: BattleState, viewer_id: int = 0, extra: Sequence[str] = ()
) -> Screen:
    return Screen(
        id=ScreenId.COMBAT,
        lines=(
            "Вы вышли из боя." if fled else "Боя удалось избежать.",
            *turn_lines(state, viewer_id),
            *(line for line in extra if line),
            "Нажмите «Назад», чтобы вернуться в локацию.",
        ),
    )
