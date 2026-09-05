"""Изучение умений, поднятие рангов, выбор грани, заполнение панели.

Панель не растёт никогда: шесть боевых слотов и один расовый, навсегда. Пассивные
умения слотов не занимают вовсе - изученное работает (``docs/skills.md``). Вся
глубина идёт из рангов с первого по пятый и из единственной грани, выбираемой на
третьем ранге. Этот модуль — единственное место, где решается, на что можно
потратить очко умений.

Всё чисто: каждая функция возвращает нового персонажа или ``None``, когда так
делать нельзя, а объясняет отказ словами вызывающий.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionTag
from mmorpg.domain.entities.content import (
    EdgeEffect,
    GameContent,
    OwnerKind,
    Skill,
    SkillKind,
)
from mmorpg.domain.rules import edges as edge_rules

#: Ветви развития - те же три тега, которыми умение оставляет след в бою.
BRANCHES: tuple[ActionTag, ...] = (ActionTag.PRESS, ActionTag.GUARD, ActionTag.PRECISION)

#: На сколько ходов предельный ранг возвращает умение раньше. Пятый ранг стоит
#: вчетверо дороже первого, и одной прибавкой к силе он этого не отрабатывает:
#: чем ближе умение к пределу, тем чаще оно в руках (ADR 0024).
MASTERY_COOLDOWN = 1

#: Как ветвь называется вслух. Игрок слышит слово, а не код.
BRANCH_NAMES: dict[ActionTag, str] = {
    ActionTag.PRESS: "Напор",
    ActionTag.GUARD: "Заслон",
    ActionTag.PRECISION: "Финт",
}

# Что делают две грани третьего ранга, здесь не решается: каждая грань
# объявляет это сама, в ``skills.toml``, а словарь объявления живёт в
# ``domain/rules/edges.py``.


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

    Цена растёт с рангом (``ProgressionRules.rank_costs``): вширь дёшево, вглубь
    дорого, и дерево целиком дороже того, что игра выдаёт за всю полосу уровней
    (ADR 0024).
    """
    if not is_known(character, skill.code):
        return content.rules.rank_cost(1)
    rank = character.loadout.rank_of(skill.code)
    if rank >= content.rules.max_rank:
        return 0
    return content.rules.rank_cost(rank + 1)


def spent_on(content: GameContent, character: Character, code: str) -> int:
    """Сколько очков лежит в этом умении - столько же и вернёт наставник."""
    rank = character.loadout.rank_of(code)
    return sum(content.rules.rank_cost(step) for step in range(1, rank + 1))


def branch_of(skill: Skill) -> ActionTag | None:
    """Ветвь умения. У расового её нет: оно вне классового дерева."""
    if skill.owner_kind is not OwnerKind.CLASS:
        return None
    return skill.branch


def branch_points(content: GameContent, character: Character) -> dict[ActionTag, int]:
    """Сколько очков вложено в каждую ветвь. Три числа, и они решают всё.

    Считается по изученному, а не запоминается: производного не хранится
    (``Claude.md``, правило 8).
    """
    tally = dict.fromkeys(BRANCHES, 0)
    for code in character.loadout.ranks:
        if not content.has_skill(code):
            continue
        branch = branch_of(content.skill(code))
        if branch is None:
            continue
        tally[branch] = tally[branch] + spent_on(content, character, code)
    return tally


def tier_of(content: GameContent, skill: Skill) -> int:
    """Ступень ветви, на которой стоит умение."""
    return content.rules.tier_of_level(skill.level)


def gate_of(content: GameContent, skill: Skill) -> int:
    """Сколько очков в своей ветви требует это умение. Ноль - первая ступень."""
    if branch_of(skill) is None:
        return 0
    return content.rules.gate_for_tier(tier_of(content, skill))


def gate_met(content: GameContent, character: Character, skill: Skill) -> bool:
    """Открыта ли ступень: хватает ли вложенного в ветвь этого умения."""
    branch = branch_of(skill)
    if branch is None:
        return True
    return branch_points(content, character)[branch] >= gate_of(content, skill)


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

    Три условия, и каждое объясняется словами: хватает очков, открыта ступень
    ветви, свободна развилка. Ранг предельный - тратить тоже не на что.
    """
    cost = cost_to_learn(content, character, skill)
    if cost < 1 or character.unspent_skill_points < cost:
        return False
    if fork_taken(content, character, skill) is not None:
        return False
    return is_known(character, skill.code) or gate_met(content, character, skill)


def edge_rank_for(content: GameContent) -> int:
    """Ранг, на котором открывается грань. Одно число на всю игру (ADR 0048): то,
    что названо в ``skills.toml``.
    """
    return content.rules.edge_rank


def needs_edge(content: GameContent, character: Character, skill: Skill) -> bool:
    """Стоит ли это умение на ранге, где нужно выбрать грань."""
    if not is_known(character, skill.code):
        return False
    if character.loadout.edge_of(skill.code) is not None:
        return False
    return character.loadout.rank_of(skill.code) >= edge_rank_for(content)


def learn(content: GameContent, character: Character, skill: Skill) -> Character | None:
    """Изучить умение или поднять его на ранг. ``None``, когда так делать нельзя.

    Отказать может любое из трёх: не хватило очков, ступень ветви ещё закрыта,
    место в развилке уже занято. Что именно случилось, читает вызывающий через
    ``learnable``, ``gate_met`` и ``fork_taken``, - здесь решается только «да».
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


def choose_edge(character: Character, skill: Skill, edge_code: str) -> Character | None:
    """Закрепить выбор третьего ранга. Он бесплатен, и обратно его бесплатно не берут."""
    if not is_known(character, skill.code):
        return None
    if character.loadout.edge_of(skill.code) is not None:
        return None
    if all(edge.code != edge_code for edge in skill.edges):
        return None
    return replace(character, loadout=character.loadout.with_edge(skill.code, edge_code))


def clear_edge(character: Character, skill: Skill) -> Character:
    """Распустить грань. Наставник за это берёт; само правило бесплатно."""
    edges = {key: value for key, value in character.loadout.edges.items() if key != skill.code}
    return replace(character, loadout=replace(character.loadout, edges=edges))


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
    if undercuts_branch(content, character, skill):
        return None
    refund = spent_on(content, character, skill.code)
    ranks = {key: value for key, value in character.loadout.ranks.items() if key != skill.code}
    edges = {key: value for key, value in character.loadout.edges.items() if key != skill.code}
    actives = tuple(None if code == skill.code else code for code in character.loadout.actives)
    return replace(
        character,
        loadout=replace(character.loadout, ranks=ranks, edges=edges, actives=actives),
        unspent_skill_points=character.unspent_skill_points + refund,
    )


def undercuts_branch(content: GameContent, character: Character, skill: Skill) -> bool:
    """Уронит ли разбор этого умения ветвь ниже того, что в ней уже открыто.

    Без этой проверки ветви не было бы вовсе: можно было бы набрать дешёвых
    умений натиска, открыть его четвёртую ступень, взять её - и разобрать всё,
    на чём она стояла. Гейт, который проверяется только при покупке, - это не
    гейт, а пошлина (ADR 0024).
    """
    branch = branch_of(skill)
    if branch is None:
        return False
    left = branch_points(content, character)[branch] - spent_on(content, character, skill.code)
    return any(
        gate_of(content, other) > left
        for code in character.loadout.ranks
        if code != skill.code and content.has_skill(code)
        for other in (content.skill(code),)
        if branch_of(other) is branch
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
        and not undercuts_branch(content, character, content.skill(code))
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
    edges = {key: value for key, value in loadout.edges.items() if key not in gone}
    actives = tuple(None if code in gone else code for code in loadout.actives)
    # Расовое умение не выбирают, поэтому его не забывают, а заменяют на то,
    # которое у этой расы есть сейчас.
    racial = loadout.racial
    if racial in gone:
        fresh = content.race(character.race_id).active_code
        racial = fresh if content.has_skill(fresh) else None
    return replace(
        character,
        loadout=replace(
            loadout,
            ranks=ranks,
            edges=edges,
            actives=actives,
            racial=racial,
        ),
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


def chosen_edge(character: Character, skill: Skill) -> EdgeEffect | None:
    """Механика грани, выбранной этим персонажем. ``None`` — грань не выбрана.

    Грань, которой у умения больше нет, читается как невыбранная: содержимое
    переживает сохранённого персонажа (``Claude.md``, правило 8).
    """
    code = character.loadout.edge_of(skill.code)
    if code is None:
        return None
    return next((edge.effect for edge in skill.edges if edge.code == code), None)


def power_factor(character: Character, skill: Skill) -> float:
    """Множитель, который выбранная грань кладёт на силу умения."""
    return edge_rules.power_factor(chosen_edge(character, skill))


def cost_factor(character: Character, skill: Skill) -> float:
    """Множитель, который выбранная грань кладёт на цену умения."""
    return edge_rules.cost_factor(chosen_edge(character, skill))
