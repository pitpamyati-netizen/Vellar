"""Изучение умений, поднятие рангов, заполнение панели.

Панель не растёт никогда: шесть боевых слотов и один расовый, навсегда. Пассивные
умения слотов не занимают вовсе - изученное работает (``docs/skills.md``). Вся
глубина идёт из рангов с первого по пятый, и ранг - единственное, на что тратят
очко умений. Этот модуль - единственное место, где решается, на что можно
потратить очко.

**Ранг обязан менять умение вчетвером сразу** (ADR 0067). Прежде он прибавлял
пятнадцатую долю силы и на предельном возвращал умение на ход раньше - за очко
это не читалось никак. Теперь каждый ранг:

- поднимает силу на ``rank_step`` умения (пятый ранг - вдвое против первого);
- через ранг укорачивает откат на ход;
- через ранг удлиняет на ход всё, что умение накладывает, - кроме того, что
  отнимает ход: лишний ход оглушения бой не разменивает, а кончает;
- на десятую долю удешевляет умение, до половины на пятом ранге.

Всё чисто: каждая функция возвращает нового персонажа или ``None``, когда так
делать нельзя, а объясняет отказ словами вызывающий.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import GameContent, OwnerKind, Skill, SkillKind
from mmorpg.domain.entities.statuses import CONTROL_STATUSES
from mmorpg.domain.rules.skill_effects import EffectSpec, Inflict

#: Через сколько рангов откат укорачивается на ход и наложенное держится на ход
#: дольше. Два: на пяти рангах это два хода к пятому - разница, которую слышно.
RANK_COOLDOWN_EVERY = 2
RANK_DURATION_EVERY = 2

#: На какую долю ранг удешевляет умение. Десятая за ранг, половина на пятом:
#: цена - доля запаса (ADR 0058), и скидка тоже доля.
RANK_COST_STEP = 0.1

#: Дешевле этой доли умение не станет ни на каком ранге.
MIN_COST_FACTOR = 0.5


def known_codes(character: Character) -> frozenset[str]:
    """Умения, которые персонаж действительно изучил, любого ранга."""
    return frozenset(character.loadout.ranks)


def is_known(character: Character, code: str) -> bool:
    return code in character.loadout.ranks


def _class_pool(content: GameContent, character: Character, kind: SkillKind) -> tuple[Skill, ...]:
    return content.class_skills_up_to(character.class_id, character.level, kind)


def teachable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Все умения класса персонажа, открытые его уровнем, изученные или нет.

    Список устойчив: умение, однажды появившись, держит своё место, поэтому игрок
    может запомнить «четвёртое» между заходами в игру.
    """
    actives = _class_pool(content, character, SkillKind.ACTIVE)
    passives = _class_pool(content, character, SkillKind.PASSIVE)
    racial = content.racial_active(character.race_id)
    return (*actives, *passives, racial)


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

    Два условия, и каждое объясняется словами: хватает очков, свободна развилка.
    Ранг предельный - тратить тоже не на что.
    """
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
    if skill.owner_kind is not OwnerKind.CLASS or skill.code == character.loadout.racial:
        return None
    refund = spent_on(content, character, skill.code)
    ranks = {key: value for key, value in character.loadout.ranks.items() if key != skill.code}
    actives = tuple(None if code == skill.code else code for code in character.loadout.actives)
    return replace(
        character,
        loadout=replace(character.loadout, ranks=ranks, actives=actives),
        unspent_skill_points=character.unspent_skill_points + refund,
    )


def forgettable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Что наставник действительно может разобрать - и вернуть за это очки.

    Только умения класса: расовое не выбирали, очков за него не платили, и
    забрать его не выйдет (см. ``forget``).
    """
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and content.skill(code).owner_kind is OwnerKind.CLASS
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
    # Расовое умение не выбирают, поэтому его не забывают, а заменяют на то,
    # которое у этой расы есть сейчас.
    racial = loadout.racial
    if racial in gone:
        fresh = content.race(character.race_id).active_code
        racial = fresh if content.has_skill(fresh) else None
    return replace(
        character,
        loadout=replace(loadout, ranks=ranks, actives=actives, racial=racial),
        unspent_skill_points=character.unspent_skill_points + points,
    )


def equippable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Изученные боевые умения, которых ещё нет ни в одном слоте.

    Только боевые: пассивному умению слот не нужен, и предлагать его к укладке
    значило бы обещать, что без укладки оно не работает.
    """
    in_panel = set(character.loadout.equipped_actives())
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code)
        and content.skill(code).kind is SkillKind.ACTIVE
        and content.skill(code).owner_kind is OwnerKind.CLASS
        and code not in in_panel
    )


def known_passives(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Пассивные умения, которые персонаж изучил. Все они и работают."""
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code) and content.skill(code).kind is SkillKind.PASSIVE
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
    if code is not None and content.skill(code).kind is not SkillKind.ACTIVE:
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
        cooldown_cut=steps // RANK_COOLDOWN_EVERY,
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


def at_rank(spec: EffectSpec, rank: int) -> EffectSpec:
    """Действие умения так, как его удлинил ранг.

    Возвращается новое описание: сами описания неизменяемы и общие для всех, у
    кого это умение есть, поэтому править их на месте нельзя.
    """
    bonus = rank_gain(rank).duration_bonus
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
