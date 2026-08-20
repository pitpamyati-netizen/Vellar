"""Learning skills, raising their ranks, choosing an edge, filling the panel.

The panel never grows: six active slots and one racial, forever. Постоянные
умения слотов не занимают вовсе - изученное работает (``docs/skills.md``). All
depth comes from ranks one to five and from the single edge chosen at rank three.
This module is the only place that decides what a skill point may be spent on.

Everything is pure: each function returns a new character or ``None`` when the
move is not allowed, and the caller explains the refusal in words.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.content import (
    EdgeEffect,
    GameContent,
    OwnerKind,
    Skill,
    SkillKind,
)
from mmorpg.domain.rules import edges as edge_rules

# Что делают две грани третьего ранга, здесь больше не решается: каждая грань
# объявляет это сама, в ``skills.toml``, и словарь объявления живёт в
# ``domain/rules/edges.py``. Раньше здесь стояли два числа на все 128 умений -
# плюс двадцать процентов силы первой грани и минус тридцать пять процентов
# стоимости второй, - и ровно из-за них ни одно описание грани не было правдой.


def known_codes(character: Character) -> frozenset[str]:
    """Skills the character has actually learned, at any rank."""
    return frozenset(character.loadout.ranks)


def is_known(character: Character, code: str) -> bool:
    return code in character.loadout.ranks


def _class_pool(content: GameContent, character: Character, kind: SkillKind) -> tuple[Skill, ...]:
    return content.class_skills_up_to(character.class_id, character.level, kind)


def teachable(content: GameContent, character: Character) -> tuple[Skill, ...]:
    """Every skill of the character's class unlocked by their level, learned or not.

    The list is stable: a skill keeps its place once it appears, so a player can
    remember "the fourth one" between sessions.
    """
    actives = _class_pool(content, character, SkillKind.ACTIVE)
    passives = _class_pool(content, character, SkillKind.PASSIVE)
    racial = content.racial_active(character.race_id)
    return (*actives, *passives, racial)


def cost_to_learn(content: GameContent, character: Character, skill: Skill) -> int:
    """One point to learn, one point per rank after that. Zero when maxed out."""
    if not is_known(character, skill.code):
        return 1
    if character.loadout.rank_of(skill.code) >= content.rules.max_rank:
        return 0
    return 1


def edge_rank_for(content: GameContent, character: Character) -> int:
    """Ранг, на котором этому персонажу открывается грань.

    Обычно третий. Печать Палаты открывает её на ранг раньше за каждое перерождение -
    это и есть «Печать открывает грани умений» (``domain/rules/turning.py``).
    Ниже первого не опускается: грань у неизученного умения выбирать не на чем.
    """
    return max(1, content.rules.edge_rank - max(0, character.seals))


def needs_edge(content: GameContent, character: Character, skill: Skill) -> bool:
    """Whether this skill is sitting at the rank where an edge must be picked."""
    if not is_known(character, skill.code):
        return False
    if character.loadout.edge_of(skill.code) is not None:
        return False
    return character.loadout.rank_of(skill.code) >= edge_rank_for(content, character)


def learn(content: GameContent, character: Character, skill: Skill) -> Character | None:
    """Learn a skill, or raise it one rank. ``None`` when it cannot be paid for."""
    if character.unspent_skill_points < 1:
        return None
    loadout = character.loadout
    if not is_known(character, skill.code):
        updated = loadout.with_rank(skill.code, 1)
    else:
        rank = loadout.rank_of(skill.code)
        if rank >= content.rules.max_rank:
            return None
        updated = loadout.with_rank(skill.code, rank + 1)
    return replace(
        character,
        loadout=updated,
        unspent_skill_points=character.unspent_skill_points - 1,
    )


def choose_edge(character: Character, skill: Skill, edge_code: str) -> Character | None:
    """Fix the rank-three choice. It is free, and it is not taken back for free."""
    if not is_known(character, skill.code):
        return None
    if character.loadout.edge_of(skill.code) is not None:
        return None
    if all(edge.code != edge_code for edge in skill.edges):
        return None
    return replace(character, loadout=character.loadout.with_edge(skill.code, edge_code))


def clear_edge(character: Character, skill: Skill) -> Character:
    """Unpick an edge. The mentor charges for this; the rule itself is free."""
    edges = {key: value for key, value in character.loadout.edges.items() if key != skill.code}
    return replace(character, loadout=replace(character.loadout, edges=edges))


def forget(content: GameContent, character: Character, skill: Skill) -> Character | None:
    """Unlearn a skill entirely and hand its points back.

    Used by the mentor. The skill leaves the panel with it, because a slot
    holding a skill nobody knows would be a button that does nothing.

    Расовое умение не разбирается: его не выбирали и очков за него не платили.
    Наставник брал за него деньги, возвращал очко - и умение оставалось на месте,
    потому что расовый слот заводит ранг заново (``SkillLoadout.__post_init__``).
    Отказ здесь и есть то, чем эта сделка кончается.
    """
    if not is_known(character, skill.code):
        return None
    if skill.owner_kind is not OwnerKind.CLASS or skill.code == character.loadout.racial:
        return None
    rank = character.loadout.rank_of(skill.code)
    ranks = {key: value for key, value in character.loadout.ranks.items() if key != skill.code}
    edges = {key: value for key, value in character.loadout.edges.items() if key != skill.code}
    actives = tuple(None if code == skill.code else code for code in character.loadout.actives)
    return replace(
        character,
        loadout=replace(character.loadout, ranks=ranks, edges=edges, actives=actives),
        unspent_skill_points=character.unspent_skill_points + rank,
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
    points = sum(loadout.rank_of(code) for code in lost)
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
    """Known battle skills that are not already in a slot.

    Только боевые: постоянному умению слот не нужен, и предлагать его к укладке
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
    """Постоянные умения, которые персонаж изучил. Все они и работают."""
    return tuple(
        content.skill(code)
        for code in sorted(known_codes(character))
        if content.has_skill(code) and content.skill(code).kind is SkillKind.PASSIVE
    )


def put_in_slot(
    content: GameContent, character: Character, slot: int, code: str | None
) -> Character | None:
    """Place a known battle skill into a panel slot, or empty it with ``None``."""
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
        # A skill sits in one slot at a time: putting it in a second one would
        # give it two buttons.
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
    """The multiplier the chosen edge puts on a skill's power."""
    return edge_rules.power_factor(chosen_edge(character, skill))


def cost_factor(character: Character, skill: Skill) -> float:
    """The multiplier the chosen edge puts on a skill's cost."""
    return edge_rules.cost_factor(chosen_edge(character, skill))
