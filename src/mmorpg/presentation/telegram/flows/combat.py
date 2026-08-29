"""Поток боя: одно нажатие - один ход, одно сообщение каждому участнику.

Сам движок живёт в ``domain.rules.combat``, а бой как запись - в
``application.services.battle``: он один на всех участников, и лежит в общем
хранилище, а не в данных автомата каждого игрока (ADR 0021). Здесь остаётся то,
что делает слой представления: кнопка превращается в действие, а состояние -
в экран, свой для каждого, кто на него смотрит.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from mmorpg.application.services.battle import BattleSession
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleEvent,
    BattleOutcome,
    BattleState,
    EventKind,
    Verdict,
)
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.entities.location import Enemy, EnemyRank
from mmorpg.domain.procgen.enemies import generate_group
from mmorpg.domain.rules.combat import act
from mmorpg.presentation.telegram.keyboards import labels
from mmorpg.presentation.telegram.keyboards.labels import Label
from mmorpg.presentation.telegram.routing import Intent, resolve
from mmorpg.presentation.telegram.screens import combat as screens
from mmorpg.presentation.telegram.screens.base import Screen


def spawn_for_node(
    content: GameContent,
    *,
    seed: bytes,
    biome: str,
    level: int,
    rank: EnemyRank = EnemyRank.NORMAL,
    stakes: float = 1.0,
    bounty: float = 1.0,
    dungeon: bool = False,
    affix_chance: float = 0.0,
    affix_count: int = 0,
) -> tuple[Enemy, ...]:
    """Кто стоит в этом узле. То же семя - те же противники, всегда.

    ``stakes`` и ``bounty`` поднимают ставку боя: в локации они всегда единица,
    а в данже их задаёт выбранная сложность (``domain/rules/dungeon.py``).

    ``dungeon`` разводит два пула пород; ``affix_chance``/``affix_count`` -
    прозвища-модификаторы, которые вешает выбранная сложность или ступень узла
    (``domain/rules/dungeon.py``, ADR 0042).
    """
    return generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome=biome,
        level=level,
        rank=rank,
        elite_titles=content.elite_titles,
        stakes=stakes,
        bounty=bounty,
        dungeon=dungeon,
        affixes=content.affixes,
        affix_chance=affix_chance,
        affix_count=affix_count,
    )


# --- экраны -----------------------------------------------------------


def render(
    content: GameContent,
    character: Character,
    session: BattleSession,
    viewer_id: int,
    notice: str = "",
    extra: Sequence[str] = (),
    rows: Sequence[tuple[Label, ...]] = (),
    gold_lost: int = 0,
    experience: int = 0,
    gold: int = 0,
    loot: Sequence[str] = (),
) -> Screen:
    """Экран этого боя для этого участника."""
    state = session.state
    match state.verdict_for(viewer_id):
        case Verdict.VICTORY:
            return screens.victory_screen(
                state,
                viewer_id,
                extra=extra,
                rows=rows,
                loot=loot,
                experience=experience,
                gold=gold,
            )
        case Verdict.DEFEAT:
            return screens.defeat_screen(state, viewer_id, gold_lost, extra=extra)
        case Verdict.FLED:
            return screens.escaped_screen(True, state, viewer_id, extra=extra)
        case Verdict.AVOIDED:
            return screens.escaped_screen(False, state, viewer_id, extra=extra)
        case _:
            current = state.active
            if current is not None and current.id == viewer_id:
                return screens.battle_screen(content, character, state, viewer_id, notice)
            return screens.waiting_screen(content, state, viewer_id, notice)


def action_for(
    content: GameContent,
    character: Character,
    session: BattleSession,
    viewer_id: int,
    text: str,
) -> BattleAction | None:
    """Нажатую кнопку или набранную команду - в действие боя."""
    screen = render(content, character, session, viewer_id)
    command = resolve(text, screen)

    match command.intent:
        case Intent.ATTACK:
            return BattleAction(kind=ActionKind.ATTACK)
        case Intent.DEFEND:
            return BattleAction(kind=ActionKind.DEFEND)
        case Intent.FLEE:
            return BattleAction(kind=ActionKind.FLEE)
        case Intent.YIELD:
            return BattleAction(kind=ActionKind.YIELD)
        case Intent.RACIAL:
            return BattleAction(kind=ActionKind.RACIAL)
        case Intent.SKILL if command.number is not None:
            return BattleAction(kind=ActionKind.SKILL, slot=command.number - 1)
        case Intent.SELECT:
            return _action_from_label(content, character, session, viewer_id, command.argument)
        case Intent.UNKNOWN:
            return _from_older_label(command.argument)
        case _:
            return None


def _from_older_label(argument: str) -> BattleAction | None:
    """Кнопка прежней панели, у которой с тех пор выросла надпись.

    Надписи удара и защиты растут вместе с числами, которые они называют, и
    нажатие на старую клавиатуру не должно запирать игрока: слово в начале
    надписи решает то же, что решала вся она (правило доступности 12).
    """
    grown = (
        (labels.ATTACK.text, ActionKind.ATTACK),
        (labels.DEFEND.text, ActionKind.DEFEND),
    )
    for text, kind in grown:
        if argument.strip().startswith(f"{text} —"):
            return BattleAction(kind=kind)
    return None


def _action_from_label(
    content: GameContent,
    character: Character,
    session: BattleSession,
    viewer_id: int,
    argument: str,
) -> BattleAction | None:
    state = session.state
    viewer = state.by_id(viewer_id)
    for slot in range(content.rules.active_slots):
        if argument.startswith(f"{slot + 1}."):
            return BattleAction(kind=ActionKind.SKILL, slot=slot)
    if viewer is not None:
        for one in state.foes_of(viewer_id):
            if screens.target_label(one).matches(argument):
                return BattleAction(kind=ActionKind.FOCUS, target=one.id)
        racial = screens.racial_label(content, character, viewer)
        if racial.matches(argument):
            return BattleAction(kind=ActionKind.RACIAL)
        # Метка несёт свои числа; голое слово, которое игрок набирал раньше,
        # работает по-прежнему.
        if screens.attack_label(content, character, viewer).matches(argument):
            return BattleAction(kind=ActionKind.ATTACK)
        if screens.defend_label(viewer).matches(argument):
            return BattleAction(kind=ActionKind.DEFEND)
    if labels.ATTACK.matches(argument):
        return BattleAction(kind=ActionKind.ATTACK)
    if labels.DEFEND.matches(argument):
        return BattleAction(kind=ActionKind.DEFEND)
    if labels.FLEE.matches(argument):
        return BattleAction(kind=ActionKind.FLEE)
    if labels.BATTLE_YIELD.matches(argument):
        return BattleAction(kind=ActionKind.YIELD)
    return None


def advance(
    content: GameContent,
    roster: Mapping[int, Character],
    session: BattleSession,
    viewer_id: int,
    text: str,
) -> tuple[BattleSession, str]:
    """Разобрать нажатие и исполнить ход. Второй член - что сказать о непонятом."""
    state = session.state
    if state.is_over:
        return session, ""

    character = roster.get(viewer_id)
    if character is None:  # pragma: no cover - зритель всегда в списке
        return session, ""

    # «Что там в бою» - не ход, а просьба сказать всё заново: ждущему больше
    # нечего нажать, и нажатие обязано что-то делать (``Claude.md``, правило 9).
    if resolve(text, render(content, character, session, viewer_id)).intent is Intent.REFRESH:
        return session, ""

    action = action_for(content, character, session, viewer_id, text)
    if action is None:
        return session, "Не узнал действие. Нажмите кнопку из панели боя."

    current = state.active
    if current is None or current.id != viewer_id:
        # Не свой ход: сдаться можно всегда, всё прочее ждёт очереди.
        if action.kind is ActionKind.YIELD:
            return yield_out_of_turn(content, roster, session, viewer_id), ""
        return session, "Сейчас не ваш ход. Дождитесь своей очереди."

    updated = act(content, roster, state, action, session.seed)
    return replace(session, state=updated), ""


def yield_out_of_turn(
    content: GameContent,
    roster: Mapping[int, Character],
    session: BattleSession,
    viewer_id: int,
) -> BattleSession:
    """Сдаться, не дожидаясь своей очереди.

    Это и есть выход из боя, который бросили с той стороны: ждать нечего, а
    ходить не дают. Бой доигрывается без сдавшегося - если он был последним на
    своей стороне, поле остаётся за противником (ADR 0021).
    """
    state = session.state
    one = state.by_id(viewer_id)
    if one is None or not one.alive:  # pragma: no cover - сдаётся только живой
        return session
    left = state.replace_combatant(replace(one, left=True))
    left = replace(
        left,
        events=(BattleEvent(kind=EventKind.YIELDED, actor_id=one.id, actor=one.name),),
    )
    current = left.active
    if current is not None and current.id == viewer_id:
        # Очередь стояла на сдавшемся: движок передвинет её сам и доиграет
        # ходы тех, за кого ходит он.
        return replace(
            session,
            state=act(content, roster, left, BattleAction(kind=ActionKind.YIELD), session.seed),
        )
    return replace(session, state=_settle_if_over(left))


def _settle_if_over(state: BattleState) -> BattleState:
    """Не кончился ли бой оттого, что кто-то из него вышел."""
    standing = {side: state.living(side) for side in (0, 1)}
    if standing[0] and standing[1]:
        return state
    winner = 0 if standing[0] else 1
    loser = 1 - winner
    walked_out = all(one.left for one in state.combatants if one.side == loser)
    return replace(
        state,
        outcome=BattleOutcome.FLED if walked_out else BattleOutcome.DECIDED,
        winner=winner,
    )
