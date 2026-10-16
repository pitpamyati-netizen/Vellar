"""Изучение умений, поднятие рангов, заполнение панели.

Панель не растёт никогда: шесть боевых слотов и один расовый, навсегда. Пассивные
умения слотов не занимают вовсе - изученное работает (``docs/skills.md``). Вся
глубина идёт из рангов с первого по пятый, и ранг - единственное, на что тратят
очко умений. Этот модуль - единственное место, где решается, на что можно
потратить очко.

**Ранг обязан менять умение вчетвером сразу** (ADR 0067). Прежде он прибавлял
пятнадцатую долю силы и на предельном возвращал умение на ход раньше - за очко
это не читалось никак. Теперь каждый ранг:

- поднимает силу на ``rank_step`` умения (пятый ранг - 220 процентов первого при шаге 0.3);
- через ранг укорачивает откат на ход;
- через ранг удлиняет на ход всё, что умение накладывает, - кроме того, что
  отнимает ход: лишний ход оглушения бой не разменивает, а кончает;
- на 12.5 процента первого ранга удешевляет умение, до половины на пятом ранге.

**А третий ранг и пятый вдобавок спрашивают, чему умение научилось**
(``rules/skill_mastery``): игрок берёт одну из ЧЕТЫРЁХ, написанных этому умению
и никакому другому, и она меняет не размер, а само действие: удар начинает бить
по всем, лечение ложится на отряд, помеха расходится по стае. Ровно за этим ранг
и поднимают.

Всё чисто: каждая функция возвращает нового персонажа или ``None``, когда так
делать нельзя, а объясняет отказ словами вызывающий.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import (
    GameContent,
    OwnerKind,
    Skill,
    SkillKind,
    SkillMastery,
)
from mmorpg.domain.entities.statuses import CONTROL_STATUSES, StatusKind
from mmorpg.domain.rules import skill_mastery as mastery_rules
from mmorpg.domain.rules import subclass as subclass_rules
from mmorpg.domain.rules import talents as talent_rules
from mmorpg.domain.rules.skill_effects import EffectSpec, Inflict, spec_for

#: Через сколько рангов откат укорачивается на ход и наложенное держится на ход
#: дольше. Два: на пяти рангах это два хода к пятому - разница, которую слышно.
RANK_COOLDOWN_EVERY = 2
RANK_DURATION_EVERY = 2

#: На какую долю ранг удешевляет умение. 12.5 процента за повышение, половина на пятом:
#: цена - доля запаса (ADR 0058), и скидка тоже доля.
RANK_COST_STEP = 0.125

#: Дешевле этой доли умение не станет ни на каком ранге.
MIN_COST_FACTOR = 0.5

#: Чьи боевые умения занимают шесть слотов панели и разбираются у наставника.
#: Умение ветки специализации - такое же умение класса, только пришедшее не с
#: уровнем, а с выбором (ADR 0074); расовое стоит отдельно и своим слотом.
_PANEL_OWNERS = frozenset({OwnerKind.CLASS, OwnerKind.SUBCLASS})


def known_codes(character: Character) -> frozenset[str]:
    """Умения, которые персонаж действительно изучил, любого ранга."""
    return frozenset(character.loadout.ranks)


def is_known(character: Character, code: str) -> bool:
    return code in character.loadout.ranks


def belongs_to(content: GameContent, character: Character, skill: Skill) -> bool:
    """Умение своего класса, своей расы или действительно выбранной ветки.

    Сохранённый код и подпись кнопки сами по себе не дают право пользоваться
    умением. Неподходящие старые записи сохраняются для возврата у наставника.
    """
    if not content.has_skill(skill.code):
        return False
    match skill.owner_kind:
        case OwnerKind.CLASS:
            return skill.owner_id == character.class_id
        case OwnerKind.RACE:
            return skill.owner_id == character.race_id
        case OwnerKind.SUBCLASS:
            return any(one.id == skill.owner_id for one in subclass_rules.taken(content, character))


def usable(content: GameContent, character: Character, skill: Skill) -> bool:
    """Ранее изученное своё умение продолжает действовать после переноса открытия."""
    return belongs_to(content, character, skill) and is_known(character, skill.code)


def next_rank_level(content: GameContent, character: Character, skill: Skill) -> int | None:
    """Уровень первого изучения; улучшение изученного не зависит от уровня."""
    rank = character.loadout.rank_of(skill.code) if is_known(character, skill.code) else 0
    if rank >= content.rules.max_rank:
        return None
    return 1 if rank else skill.level


def _class_pool(content: GameContent, character: Character, kind: SkillKind) -> tuple[Skill, ...]:
    available = content.class_skills_up_to(character.class_id, character.level, kind)
    # Перенос уровня открытия не отнимает уже оплаченное умение у старого героя.
    previous = tuple(
        one
        for one in content.skills_of(f"class:{character.class_id}", kind)
        if one.level > character.level and is_known(character, one.code)
    )
    return (*available, *previous)


def teachable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Всё, чему этого персонажа можно учить, изученное или нет.

    Умения класса, открытые его уровнем, расовое - и умения взятых веток
    специализации (ADR 0074): ветка даёт доступ, а ранг в нём стоит очка, как у
    всякого другого умения.

    Список устойчив: умение, однажды появившись, держит своё место, поэтому
    игрок может запомнить «четвёртое» между заходами в игру. Умения веток
    дописываются В КОНЕЦ и по возрастанию ступени - взятая ветка не должна
    сдвигать то, что игрок уже выучил считать по номерам.
    """
    actives = _class_pool(content, character, SkillKind.ACTIVE)
    passives = _class_pool(content, character, SkillKind.PASSIVE)
    racial = content.racial_active(character.race_id)
    return (*actives, *passives, racial, *subclass_rules.skills(content, character))


def cost_to_learn(content: GameContent, character: Character, skill: Skill) -> int:
    """Во что обойдётся следующий ранг этого умения. Ноль, когда ранг предельный.

    Цена одна на все ранги (``ProgressionRules.rank_cost``): за очко ранг платит
    тем, что умение делает, а не тем, чего он стоит (ADR 0067).
    """
    if not is_known(character, skill.code):
        return content.rules.rank_cost
    if character.loadout.rank_of(skill.code) >= content.rules.max_rank:
        return 0
    return content.rules.rank_cost


def spent_on(content: GameContent, character: Character, code: str) -> int:
    """Сколько очков лежит в этом умении - столько же и вернёт наставник."""
    return character.loadout.rank_of(code) * content.rules.rank_cost


def fork_rivals(content: GameContent, skill: Skill) -> tuple[Skill, ...]:
    """Умения, с которыми это спорит за одно место. Пусто - оно ни с чем не спорит."""
    if not skill.fork:
        return ()
    return tuple(
        other
        for other in content.skills
        if other.fork == skill.fork and other.code != skill.code and other.owner == skill.owner
    )


def fork_taken(content: GameContent, character: Character, skill: Skill) -> Skill | None:
    """Соперник по развилке, которого уже взяли. ``None`` - развилка свободна."""
    return next(
        (rival for rival in fork_rivals(content, skill) if is_known(character, rival.code)), None
    )


def learnable(content: GameContent, character: Character, skill: Skill) -> bool:
    """Можно ли прямо сейчас потратить очко на это умение.

    Принадлежность, уровень, очки и развилка проверяются здесь, независимо
    от того, из какого экрана или сохранения пришёл запрос.
    """
    required = next_rank_level(content, character, skill)
    if not belongs_to(content, character, skill) or required is None or character.level < required:
        return False
    cost = cost_to_learn(content, character, skill)
    if cost < 1 or character.unspent_skill_points < cost:
        return False
    return fork_taken(content, character, skill) is None


def learn(content: GameContent, character: Character, skill: Skill) -> Character | None:
    """Изучить умение или поднять его на ранг. ``None``, когда так делать нельзя.

    Отказать может любое из двух: не хватило очков, место в развилке уже занято.
    Что именно случилось, читает вызывающий через ``learnable`` и ``fork_taken``, -
    здесь решается только «да».
    """
    if not learnable(content, character, skill):
        return None
    cost = cost_to_learn(content, character, skill)
    loadout = character.loadout
    if not is_known(character, skill.code):
        updated = loadout.with_rank(skill.code, 1)
    else:
        updated = loadout.with_rank(skill.code, loadout.rank_of(skill.code) + 1)
    return replace(
        character,
        loadout=updated,
        unspent_skill_points=character.unspent_skill_points - cost,
    )


def forget(content: GameContent, character: Character, skill: Skill) -> Character | None:
    """Забыть умение целиком и вернуть вложенные в него очки.

    Берётся наставником. Умение уходит из панели вместе с очками, потому что слот с
    умением, которого никто не знает, был бы кнопкой, не делающей ничего.

    Расовое умение не разбирается: его не выбирали и очков за него не платили.
    Наставник брал за него деньги, возвращал очко - и умение оставалось на месте,
    потому что расовый слот заводит ранг заново (``SkillLoadout.__post_init__``).
    Отказ здесь и есть то, чем эта сделка кончается.
    """
    if not is_known(character, skill.code):
        return None
    if skill.owner_kind not in _PANEL_OWNERS or skill.code == character.loadout.racial:
        return None
    refund = spent_on(content, character, skill.code)
    ranks = {key: value for key, value in character.loadout.ranks.items() if key != skill.code}
    actives = tuple(None if code == skill.code else code for code in character.loadout.actives)
    # Грань уходит вместе с умением: чему было научено то, чего никто не знает,
    # не значит ничего, а выученное заново начинается с чистого листа.
    loadout = character.loadout.without_masteries(skill.code)
    return replace(
        character,
        loadout=replace(loadout, ranks=ranks, actives=actives),
        unspent_skill_points=character.unspent_skill_points + refund,
    )


def forgettable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Что наставник действительно может разобрать - и вернуть за это очки.

    Умения класса и взятых веток: расовое не выбирали, очков за него не
    платили, и забрать его не выйдет (см. ``forget``).
    """
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and content.skill(code).owner_kind in _PANEL_OWNERS
        and code != character.loadout.racial
    )


def reclaim_lost(content: GameContent, character: Character) -> Character | None:
    """Умения, которых в игре больше нет, забываются и возвращают очки.

    Содержимое переживает сохранённого персонажа не только в одну сторону:
    умение можно и убрать. Без этой уборки разбойник, у которого «Удар в спину»
    перестал существовать, остался бы с пустой панелью, с очком, вложенным в
    ничто, и без единого боевого умения — то есть без игры.

    Возвращается ровно столько очков, сколько стоило умение, — так же, как их
    возвращает наставник. ``None``, когда терять нечего.
    """
    loadout = character.loadout
    lost = [code for code in loadout.ranks if not content.has_skill(code)]
    if not lost:
        return None

    gone = set(lost)
    points = sum(spent_on(content, character, code) for code in lost)
    ranks = {key: value for key, value in loadout.ranks.items() if key not in gone}
    actives = tuple(None if code in gone else code for code in loadout.actives)
    masteries = {key: value for key, value in loadout.masteries.items() if key not in gone}
    # Расовое умение не выбирают, поэтому его не забывают, а заменяют на то,
    # которое у этой расы есть сейчас.
    racial = loadout.racial
    if racial in gone:
        fresh = content.race(character.race_id).active_code
        racial = fresh if content.has_skill(fresh) else None
    return replace(
        character,
        loadout=replace(loadout, ranks=ranks, actives=actives, racial=racial, masteries=masteries),
        unspent_skill_points=character.unspent_skill_points + points,
    )


def equippable(
    content: GameContent, character: Character, slot: int | None = None
) -> tuple[Skill, ...]:
    """Изученные боевые умения вне панели или уже в выбранном слоте.

    Только боевые: пассивному умению слот не нужен, и предлагать его к укладке
    значило бы обещать, что без укладки оно не работает.
    """
    in_panel = set(character.loadout.equipped_actives())
    if slot is not None and 0 <= slot < len(character.loadout.actives):
        in_panel.discard(character.loadout.actives[slot])
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and content.skill(code).kind is SkillKind.ACTIVE
        and content.skill(code).owner_kind in _PANEL_OWNERS
        and usable(content, character, content.skill(code))
        and code not in in_panel
    )


def known_passives(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Пассивные умения, которые персонаж изучил. Все они и работают."""
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and content.skill(code).kind is SkillKind.PASSIVE
        and usable(content, character, content.skill(code))
    )


def put_in_slot(
    content: GameContent, character: Character, slot: int, code: str | None
) -> Character | None:
    """Положить изученное боевое умение в слот панели или очистить слот через ``None``."""
    if not 0 <= slot < content.rules.active_slots:
        return None
    if code is not None and not is_known(character, code):
        return None
    if code is not None and not content.has_skill(code):
        return None
    if code is not None:
        skill = content.skill(code)
        if (
            skill.kind is not SkillKind.ACTIVE
            or skill.owner_kind not in _PANEL_OWNERS
            or not usable(content, character, skill)
        ):
            return None
    loadout = character.loadout
    if code is not None:
        # Умение лежит разом в одном слоте: положить его во второй значило бы дать ему
        # две кнопки.
        loadout = replace(
            loadout, actives=tuple(None if item == code else item for item in loadout.actives)
        )
    return replace(character, loadout=loadout.with_active(slot, code))


# --- что даёт ранг ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankGain:
    """Что ранг прибавил к умению сверх первого - числами, а не словами.

    Читается и боем, и экраном: карточка умения обязана называть ровно то, что
    считает движок (``Claude.md``, правило 7).
    """

    rank: int
    cooldown_cut: int
    duration_bonus: int
    cost_factor: float

    @property
    def changes_anything(self) -> bool:
        return bool(self.cooldown_cut or self.duration_bonus) or self.cost_factor < 1.0


def rank_gain(rank: int) -> RankGain:
    """Поправки, которые этот ранг вносит в умение. Первый ранг не меняет ничего."""
    steps = max(0, rank - 1)
    return RankGain(
        rank=max(1, rank),
        cooldown_cut=(steps + 1) // RANK_COOLDOWN_EVERY,
        duration_bonus=steps // RANK_DURATION_EVERY,
        cost_factor=max(MIN_COST_FACTOR, 1.0 - RANK_COST_STEP * steps),
    )


def cost_factor(rank: int) -> float:
    """Во сколько раз ранг удешевляет умение."""
    return rank_gain(rank).cost_factor


def cooldown_at_rank(skill: Skill, rank: int) -> int:
    """Откат умения на этом ранге. Ниже нуля не опускается."""
    return max(0, skill.cooldown - rank_gain(rank).cooldown_cut)


def _stretched(held: tuple[Inflict, ...], bonus: int) -> tuple[Inflict, ...]:
    """Продлить наложенное - всё, кроме того, что отнимает ход.

    Лишний ход оглушения или оцепенения бой не разменивает, а кончает: цель
    просто не ходит. Всё прочее - горение, ускорение, метка - держится дольше.
    """
    return tuple(
        one if one.kind in CONTROL_STATUSES else replace(one, turns=one.turns + bonus)
        for one in held
    )


def at_rank(spec: EffectSpec, rank: int, rank_step: float = 0.3) -> EffectSpec:
    """Действие умения так, как его удлинил ранг.

    Возвращается новое описание: сами описания неизменяемы и общие для всех, у
    кого это умение есть, поэтому править их на месте нельзя.
    """
    return _rank_duration(_rank_numbers(spec, rank, rank_step), rank_gain(rank).duration_bonus)


def _rank_duration(spec: EffectSpec, bonus: int) -> EffectSpec:
    if not bonus:
        return spec
    return replace(
        spec,
        duration=spec.duration + bonus if spec.duration else 0,
        dot_turns=spec.dot_turns + bonus if spec.dot_turns else 0,
        barrier_turns=spec.barrier_turns + bonus if spec.barrier_turns else 0,
        inflicts=_stretched(spec.inflicts, bonus),
        holds=_stretched(spec.holds, bonus),
    )


def _rank_numbers(spec: EffectSpec, rank: int, rank_step: float) -> EffectSpec:
    if rank <= 1:
        return spec
    factor = 1.0 + rank_step * (rank - 1)

    # Числовые довески тоже развиваются. Служебные признаки и номера целей
    # не являются силой и не умножаются. Грани применяются после этой правки.
    def grown(held: tuple[Inflict, ...]) -> tuple[Inflict, ...]:
        numeric = {
            StatusKind.WEAKNESS,
            StatusKind.EMPOWER,
            StatusKind.BERSERK,
            StatusKind.RESOURCE_REGEN,
            StatusKind.HEALTH_REGEN,
            StatusKind.HASTE,
            StatusKind.SLOW,
            StatusKind.BARRIER,
        }
        return tuple(
            replace(one, value=one.value * factor)
            if one.kind in numeric and one.value is not None
            else one
            for one in held
        )

    return replace(
        spec,
        inflicts=grown(spec.inflicts),
        holds=grown(spec.holds),
        bonus_heal=spec.bonus_heal * factor,
        bonus_barrier=spec.bonus_barrier * factor,
        self_modifiers=tuple(
            replace(one, value=one.value * factor)
            if one.value is not None
            and not one.key.startswith("_")
            and ((one.value < 0) if one.key == "damage_taken_percent" else (one.value > 0))
            else one
            for one in spec.self_modifiers
        ),
        target_modifiers=tuple(
            replace(one, value=one.value * factor)
            if one.value is not None and not one.key.startswith("_")
            else one
            for one in spec.target_modifiers
        ),
    )


# --- чему умение научено ----------------------------------------------


def spec_of(skill: Skill) -> EffectSpec:
    """Описание эффекта умения без поправок - то, из чего считается всё прочее."""
    return spec_for(skill.effect)


def effective_spec(content: GameContent, character: Character, skill: Skill) -> EffectSpec:
    rank = character.loadout.rank_of(skill.code)
    ranked = _rank_numbers(spec_for(skill.effect), rank, skill.rank_step)
    faceted = mastery_rules.applied(
        skill, ranked, character.loadout.masteries_of(skill.code), rank=rank
    )
    # Добавленные гранью состояния получают прежнюю прибавку срока от ранга.
    stretched = _rank_duration(faceted, rank_gain(rank).duration_bonus)
    return talent_rules.applied(content, character, skill, stretched)


def masteries_of(character: Character, skill: Skill) -> tuple[str, ...]:
    """Коды действующих граней; исходные записи остаются в сохранении."""
    rank = character.loadout.rank_of(skill.code) if is_known(character, skill.code) else 0
    return tuple(
        one.code
        for one in mastery_rules.known(skill, character.loadout.masteries_of(skill.code), rank=rank)
    )


def taken_masteries(character: Character, skill: Skill) -> tuple[SkillMastery, ...]:
    """Грани этого умения так, как их знает игра сейчас."""
    return mastery_rules.known(skill, masteries_of(character, skill))


def mastery_tier_due(content: GameContent, character: Character, skill: Skill) -> int | None:
    """Ступень грани, которую этому умению пора выбрать. ``None`` - нечего.

    Пассивному умению граней не предлагают вовсе: у пассивки нет ни хода, ни
    цели, и менять в её действии нечего - ранг платит ей размером (ADR 0073).
    """
    if not skill.is_active or not usable(content, character, skill):
        return None
    rank = character.loadout.rank_of(skill.code)
    return mastery_rules.pending_tier(skill, rank, character.loadout.masteries_of(skill.code))


def mastery_choices(
    content: GameContent, character: Character, skill: Skill
) -> tuple[SkillMastery, ...]:
    """Из чего этому умению сейчас выбирают. Пусто - выбирать нечего или не пора.

    Список свой у каждого умения: общего, из которого выбирали бы все, нет
    (ADR 0083).
    """
    tier = mastery_tier_due(content, character, skill)
    if tier is None:
        return ()
    return mastery_rules.offered(skill, tier)


def choose_mastery(
    content: GameContent, character: Character, skill: Skill, code: str
) -> Character | None:
    """Научить умение грани. ``None`` - так делать нельзя.

    Выбор необратим, как выбор ветки: обратимый выбор - это настройка. Вернуть
    его можно только вместе с самим умением, разобрав его у наставника.
    """
    if code not in {one.code for one in mastery_choices(content, character, skill)}:
        return None
    return replace(character, loadout=character.loadout.with_mastery(skill.code, code))


def waiting_for_mastery(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Умения, которым игрок ещё не сказал, чему они научились.

    Читается экраном умений и главным меню: ранг, купленный и не потраченный на
    выбор, - это очко, лежащее без дела, и молчать о нём нельзя.
    """
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and mastery_tier_due(content, character, content.skill(code)) is not None
    )
