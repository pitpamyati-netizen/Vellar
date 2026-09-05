"""Повадки пород: чем громила отличается от воина, заклинателя и знахаря.

Тегов, следа и намерений в бою больше нет (ADR 0066). Разница между
противниками осталась, и она вся в том, что каждый делает в свой ход: громила
на исходе сил бьёт сильнее, воин закрывается, разбойник добивает наверняка,
заклинатель раз в несколько кругов бьёт всю сторону, знахарь поднимает своего.

Случайности здесь нет вовсе: чей сейчас приём - чистая функция круга и номера
бойца, поэтому экран и движок всегда называют одно и то же.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleState,
    EventKind,
)
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank, EnemyRole
from mmorpg.domain.rules.combat import (
    BRUTE_FURY_SCALE,
    ROLE_MOVE_EVERY,
    hero_combatant,
    is_wounded,
    monster_combatant,
    open_battle,
    role_action,
    role_move_due,
    role_of,
)

ATTACK = BattleAction(kind=ActionKind.ATTACK)

#: Инициатива, при которой противник ходит раньше героя - тогда его ход приходит
#: прямо в ``open_battle``, и по нему видно, что делает повадка.
FAST = 999.0


def make_enemy(
    *,
    role: EnemyRole = EnemyRole.BRUTE,
    initiative: float = 9.0,
    health: int = 4_000,
    damage: int = 40,
    armor: int = 0,
    rank: EnemyRank = EnemyRank.NORMAL,
    level: int = 5,
    name: str = "Волк",
) -> Enemy:
    return Enemy(
        archetype_id="test_beast",
        name=name,
        kind=EnemyKind.BEAST,
        level=level,
        max_health=health,
        damage=damage,
        armor=armor,
        initiative=initiative,
        loot=(),
        gold=10,
        rank=rank,
        role=role,
    )


@pytest.fixture
def fighter(warrior: Character) -> Character:
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=("warrior_sekushchiy_roscherk", None, None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


def start(
    content: GameContent,
    character: Character,
    *enemies: Enemy,
    seed: bytes = b"role-seed",
) -> tuple[BattleState, dict[int, Character]]:
    """Герой под номером 1, противники со второго."""
    roster = {1: character}
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(enemy, combatant_id=index + 2, side=1)
            for index, enemy in enumerate(enemies)
        ),
    ]
    return open_battle(content, roster, fighters, seed), roster


def health_of(state: BattleState, combatant_id: int) -> int:
    one = state.by_id(combatant_id)
    assert one is not None
    return one.health


def wounded(state: BattleState, combatant_id: int, share: float) -> BattleState:
    one = state.by_id(combatant_id)
    assert one is not None
    return state.replace_combatant(replace(one, health=max(1, round(one.max_health * share))))


# --- повадка вообще -------------------------------------------------------


def test_a_hero_has_no_disposition(content: GameContent, fighter: Character) -> None:
    """За персонажем стоит игрок, а не порода: повадки у него нет."""
    state, _ = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    assert role_of(hero) is None


def test_the_move_is_spread_across_the_pack(content: GameContent, fighter: Character) -> None:
    """Приём разведён номером: трое заклинателей не бьют по всем разом."""
    state, _ = start(
        content,
        fighter,
        make_enemy(role=EnemyRole.CASTER, name="Первый"),
        make_enemy(role=EnemyRole.CASTER, name="Второй"),
        make_enemy(role=EnemyRole.CASTER, name="Третий"),
    )
    for step in range(ROLE_MOVE_EVERY):
        round_state = replace(state, round=state.round + step)
        due = [
            role_move_due(round_state, one)
            for one in round_state.combatants
            if one.side != 0 and one.alive
        ]
        assert sum(due) == 1


def test_the_move_is_the_same_every_time_it_is_asked(
    content: GameContent, fighter: Character
) -> None:
    state, _ = start(content, fighter, make_enemy(role=EnemyRole.CASTER))
    foe = state.by_id(2)
    assert foe is not None
    assert role_move_due(state, foe) == role_move_due(state, foe)


# --- громила --------------------------------------------------------------


def brute_blow(content: GameContent, character: Character, *, share: float) -> int:
    """Сколько снимет с героя первый удар громилы, которому осталась эта доля.

    Противник быстрее героя, поэтому его ход приходит прямо в ``open_battle``:
    считать нужно только разницу здоровья.
    """
    enemy = make_enemy(role=EnemyRole.BRUTE, initiative=FAST, damage=200, level=character.level)
    hero = hero_combatant(content, character, combatant_id=1, side=0, live=True)
    foe = monster_combatant(enemy, combatant_id=2, side=1)
    foe = replace(foe, health=max(1, round(foe.max_health * share)))
    state = open_battle(content, {1: character}, [hero, foe], b"brute-seed")
    return hero.health - health_of(state, 1)


def test_a_wounded_brute_hits_harder(content: GameContent, fighter: Character) -> None:
    """Ярость громилы - настоящий множитель, и она приходит с ранами."""
    healthy = brute_blow(content, fighter, share=1.0)
    furious = brute_blow(content, fighter, share=0.1)
    assert furious > healthy
    assert furious == pytest.approx(healthy * BRUTE_FURY_SCALE, rel=0.05)


# --- воин -----------------------------------------------------------------


def test_a_wounded_warrior_closes_up(content: GameContent, fighter: Character) -> None:
    """Раненый воин закрывается - это его повадка, и ход уходит на оборону."""
    state, _ = start(content, fighter, make_enemy(role=EnemyRole.WARRIOR))
    hurt = wounded(state, 2, 0.1)
    foe = hurt.by_id(2)
    assert foe is not None
    assert is_wounded(foe)
    chosen = role_action(hurt, foe, target_id=1)
    assert chosen is not None
    assert chosen.kind is ActionKind.DEFEND


def test_a_whole_warrior_just_fights(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy(role=EnemyRole.WARRIOR))
    foe = state.by_id(2)
    assert foe is not None
    assert role_action(state, foe, target_id=1) is None


def test_an_elite_warrior_never_turtles(content: GameContent, fighter: Character) -> None:
    """Хозяин логова весь бой давит: открытость - его цена за длину боя."""
    state, _ = start(content, fighter, make_enemy(role=EnemyRole.WARRIOR, rank=EnemyRank.BOSS))
    hurt = wounded(state, 2, 0.1)
    foe = hurt.by_id(2)
    assert foe is not None
    assert role_action(hurt, foe, target_id=1) is None


# --- заклинатель ----------------------------------------------------------


def test_a_caster_sweeps_the_whole_side(content: GameContent, warrior: Character) -> None:
    """Приём заклинателя достаётся всем, кто стоит против него."""
    ally = replace(warrior, id=4, user_id=44, name="Мирна")
    roster = {1: warrior, 4: ally}
    caster = make_enemy(
        role=EnemyRole.CASTER, initiative=FAST, damage=120, level=warrior.level, name="Ведун"
    )
    fighters = [
        hero_combatant(content, warrior, combatant_id=1, side=0, live=True),
        hero_combatant(content, ally, combatant_id=4, side=0, live=True),
        monster_combatant(caster, combatant_id=2, side=1),
    ]
    state = open_battle(content, roster, fighters, b"sweep-seed")
    assert any(event.kind is EventKind.ROLE_MOVE for event in state.events)
    touched = {
        event.target_id
        for event in state.events
        if event.kind in {EventKind.DAMAGE, EventKind.CRIT, EventKind.DODGE, EventKind.MISS}
    }
    assert touched == {1, 4}


# --- знахарь --------------------------------------------------------------


def test_a_healer_lifts_the_worst_off_ally(content: GameContent, fighter: Character) -> None:
    """Рука знахаря идёт тому, кому осталось меньше всех."""
    healer = make_enemy(role=EnemyRole.HEALER, initiative=FAST, name="Знахарь")
    hurt_one = make_enemy(role=EnemyRole.BRUTE, name="Волчица")
    fighters = [
        hero_combatant(content, fighter, combatant_id=1, side=0, live=True),
        monster_combatant(healer, combatant_id=2, side=1),
        replace(
            monster_combatant(hurt_one, combatant_id=3, side=1),
            health=round(hurt_one.max_health * 0.2),
        ),
    ]
    before = round(hurt_one.max_health * 0.2)
    state = open_battle(content, {1: fighter}, fighters, b"heal-seed")
    assert any(event.kind is EventKind.ROLE_MOVE for event in state.events)
    assert health_of(state, 3) > before


def test_a_healer_with_nobody_to_lift_just_fights(content: GameContent, fighter: Character) -> None:
    """Приём объявляют, только когда есть кого поднимать."""
    state, _ = start(
        content,
        fighter,
        make_enemy(role=EnemyRole.HEALER),
        make_enemy(role=EnemyRole.BRUTE, name="Волчица"),
    )
    foe = state.by_id(2)
    assert foe is not None
    while not role_move_due(state, foe):
        state = replace(state, round=state.round + 1)
        foe = state.by_id(2)
        assert foe is not None
    assert role_action(state, foe, target_id=1) is None
