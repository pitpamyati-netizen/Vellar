"""Прозвища-модификаторы врагов в бою (ADR 0042).

Множители запекаются при сборке (``test_procgen``); здесь проверяется, что
механика прозвища работает в движке: отражение и вампиризм читаются с
бойца-породы, статус по попаданию вешается на цель, а на обычной стае ничего
из этого не срабатывает.
"""

from __future__ import annotations

from dataclasses import replace

from mmorpg.domain.entities import Character, GameContent
from mmorpg.domain.entities.combat import ActionKind, BattleAction, BattleState, EventKind
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import StatusKind, status_name
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle

SEED = b"affix-seed-0001"


def _enemy(*, affixes: tuple[str, ...] = (), health: int = 400) -> Enemy:
    return Enemy(
        archetype_id="rockjaw",
        name="Камнеед",
        kind=EnemyKind.ABERRATION,
        level=20,
        max_health=health,
        damage=18,
        armor=2,
        initiative=20.0,
        loot=(),
        gold=10,
        affixes=affixes,
    )


def _fight(
    content: GameContent, hero: Character, enemy: Enemy, *, attach: str = ""
) -> tuple[BattleState, dict[int, Character]]:
    roster = {1: hero}
    foe = monster_combatant(enemy, combatant_id=2, side=1)
    if attach:
        effect = content.affix(attach).effect()
        assert effect is not None
        foe = replace(foe, effects=foe.effects.apply(effect))
    hero_one = hero_combatant(content, hero, combatant_id=1, side=0, live=False)
    return open_battle(content, roster, [hero_one, foe], SEED), roster


def _run(
    content: GameContent, state: BattleState, roster: dict[int, Character], rounds: int
) -> BattleState:
    for turn in range(rounds):
        current = state.active
        if current is None or state.is_over:
            break
        action = BattleAction(kind=ActionKind.ATTACK)
        state = act(content, roster, state, action, turn.to_bytes(16, "big"))
    return state


def test_venombite_poisons_on_a_basic_monster_hit(content: GameContent, warrior: Character) -> None:
    hero = replace(warrior, level=20)
    roster = {1: hero}
    foe = monster_combatant(_enemy(affixes=("venombite",), health=6_000), combatant_id=2, side=1)
    hero_one = hero_combatant(content, hero, combatant_id=1, side=0, live=True)
    state = open_battle(content, roster, [hero_one, foe], SEED)
    poisoned = False
    for turn in range(40):
        if state.active is None or state.is_over:
            break
        state = act(
            content, roster, state, BattleAction(kind=ActionKind.ATTACK), turn.to_bytes(16, "big")
        )
        one = state.by_id(1)
        if one is not None and one.effects.has(StatusKind.POISON):
            poisoned = True
    assert poisoned, "«Гнилозубый» должен когда-нибудь отравить того, кого ударил"


def test_a_plain_pack_never_poisons(content: GameContent, warrior: Character) -> None:
    hero = replace(warrior, level=20)
    state, roster = _fight(content, hero, _enemy())
    state = _run(content, state, roster, rounds=30)
    hero_one = state.by_id(1)
    assert hero_one is None or not hero_one.effects.has(StatusKind.POISON)


def test_thornback_reflects_damage_to_the_attacker(
    content: GameContent, warrior: Character
) -> None:
    hero = replace(warrior, level=20)
    plain_state, plain_roster = _fight(content, hero, _enemy())
    plain_state = _run(content, plain_state, plain_roster, rounds=4)
    plain_left = plain_state.by_id(2).health

    thorn_state, thorn_roster = _fight(
        content, hero, _enemy(affixes=("thornback",)), attach="thornback"
    )
    thorn_state = _run(content, thorn_state, thorn_roster, rounds=4)
    # Тот же размен ударов, но иглистый теряет ещё и от собственного отражения:
    # герой бьёт сильнее, чем порода, поэтому разница видна на здоровье героя.
    assert thorn_state.by_id(1).health < plain_state.by_id(1).health
    assert plain_left is not None


def test_bloodletter_heals_the_monster_on_its_strike(
    content: GameContent, warrior: Character
) -> None:
    hero = replace(warrior, level=20)
    plain_state, plain_roster = _fight(content, hero, _enemy(health=5_000))
    plain_state = _run(content, plain_state, plain_roster, rounds=10)

    blood_state, blood_roster = _fight(
        content, hero, _enemy(affixes=("bloodletter",), health=5_000), attach="bloodletter"
    )
    blood_state = _run(content, blood_state, blood_roster, rounds=10)
    assert blood_state.by_id(2).health > plain_state.by_id(2).health


def test_a_monster_dodges_more_with_an_evasive_modifier(
    content: GameContent, warrior: Character
) -> None:
    """``_dodge_of`` теперь читает эффект и у породы (ADR 0042)."""
    from types import MappingProxyType

    from mmorpg.domain.entities.effects import ActiveEffect

    hero = replace(warrior, level=20)

    def health_after(evasive: bool) -> int:
        roster = {1: hero}
        foe = monster_combatant(_enemy(health=5_000), combatant_id=2, side=1)
        if evasive:
            evasion = ActiveEffect(
                id="affix:test",
                name="Юркий",
                modifiers=MappingProxyType({"dodge_percent": 60.0}),
                turns_left=1,
                beneficial=True,
                permanent=True,
            )
            foe = replace(foe, effects=foe.effects.apply(evasion))
        hero_one = hero_combatant(content, hero, combatant_id=1, side=0, live=False)
        state = open_battle(content, roster, [hero_one, foe], SEED)
        state = _run(content, state, roster, rounds=12)
        return state.by_id(2).health

    assert health_after(evasive=True) > health_after(evasive=False)


def _recloaks_within(content: GameContent, enemy: Enemy, hero: Character, turns: int) -> bool:
    roster = {1: hero}
    foe = monster_combatant(enemy, combatant_id=2, side=1)
    hero_one = hero_combatant(content, hero, combatant_id=1, side=0, live=True)
    state = open_battle(content, roster, [hero_one, foe], SEED)
    for turn in range(turns):
        if state.is_over:
            break
        state = act(
            content,
            roster,
            state,
            BattleAction(kind=ActionKind.ATTACK, target=2),
            turn.to_bytes(16, "big"),
        )
        if any(
            event.kind is EventKind.STATUS_APPLIED
            and event.actor_id == 2
            and event.effect_name == status_name(StatusKind.UNSEEN)
            for event in state.events
        ):
            return True
    return False


def test_a_stalking_pack_goes_back_out_of_sight_after_it_strikes(
    content: GameContent, warrior: Character
) -> None:
    hero = replace(warrior, level=20)
    assert _recloaks_within(content, _enemy(affixes=("stalking",), health=8_000), hero, turns=16)


def test_a_plain_pack_never_goes_out_of_sight(content: GameContent, warrior: Character) -> None:
    hero = replace(warrior, level=20)
    assert not _recloaks_within(content, _enemy(health=8_000), hero, turns=16)
