"""Отряд и защита: что на самом деле считает движок.

Отряд - объединение игроков, и ничего больше: пятеро, окно уровней, общий бой и
делёж поровну (``domain/rules/party.py``, ADR 0026). Мест в отряде нет, поэтому
и проверять здесь нечего сверх того, что отряд ничего никому не прибавляет.

Защита - разговор о числах: ход отдан обороне, и за него дают брони по уровню и
треть уклонения.
"""

from __future__ import annotations

import random

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ATTACKERS,
    DEFENDERS,
    ActionKind,
    ActionTag,
    BattleAction,
    BattleState,
    Combatant,
)
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import party as party_rules
from mmorpg.domain.rules.combat import (
    DEFEND_ARMOR_PER_LEVEL,
    act,
    defend_armor,
    defend_dodge,
    hero_combatant,
    monster_combatant,
    open_battle,
)
from mmorpg.domain.rules.stats import derived_stats

SEED = b"party-seed-00001"


def make_enemy(name: str = "Волк", health: int = 900, damage: int = 12) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=10,
        max_health=health,
        damage=damage,
        armor=2,
        initiative=9.0,
        loot=(),
        gold=10,
    )


def a_hero(name: str, character_id: int, *, level: int = 10) -> Character:
    return Character(
        id=character_id,
        user_id=1000 + character_id,
        name=name,
        race_id="human",
        class_id="warrior",
        level=level,
        loadout=SkillLoadout(
            actives=("warrior_rassechenie", None, None, None, None, None),
            racial="race_human_second_wind",
        ),
    )


def build(
    content: GameContent,
    party: list[Character],
    enemies: tuple[Enemy, ...] = (),
) -> tuple[BattleState, dict[int, Character]]:
    """Бой отряда со стаей: у каждого свой номер и ничего сверх своего."""
    roster: dict[int, Character] = {}
    fighters: list[Combatant] = []
    next_id = 1
    for character in party:
        fighters.append(
            hero_combatant(content, character, combatant_id=next_id, side=ATTACKERS, live=True)
        )
        roster[next_id] = character
        next_id += 1
    for enemy in enemies:
        fighters.append(monster_combatant(enemy, combatant_id=next_id, side=DEFENDERS))
        next_id += 1
    return open_battle(content, roster, fighters, SEED), roster


def one(state: BattleState, combatant_id: int) -> Combatant:
    found = state.by_id(combatant_id)
    assert found is not None
    return found


# --- отряд как объединение --------------------------------------------


def test_five_fit_into_the_party_and_the_sixth_does_not() -> None:
    assert party_rules.MAX_MEMBERS == 5
    party = party_rules.Party(leader_id=1, members=(1, 2, 3, 4, 5))
    assert party.full
    assert party.with_member(6).size == 5


def test_a_party_of_one_is_a_party() -> None:
    """Заведённый отряд живёт и пустым: в него ещё только зовут."""
    party = party_rules.Party(leader_id=1)
    assert party.members == (1,)
    assert party.alone and not party.disbanded


def test_leaving_shrinks_the_party_and_the_leader_ends_it() -> None:
    party = party_rules.Party(leader_id=1, members=(1, 2, 3))
    assert party.without(3).members == (1, 2)
    assert party.without(1).disbanded


def test_the_party_gives_a_fighter_nothing(content: GameContent) -> None:
    """Бой впятером - тот же бой: числа бойца от отряда не меняются ничем."""
    hero = a_hero("Аргус", 1)
    alone, _ = build(content, [hero], enemies=(make_enemy(),))
    together, _ = build(content, [hero, a_hero("Мирна", 2)], enemies=(make_enemy(),))
    plain, shared = one(alone, 1), one(together, 1)
    assert (plain.max_health, plain.initiative) == (shared.max_health, shared.initiative)
    assert not shared.effects.modifiers()
    assert derived_stats(content, hero, shared.effects) == derived_stats(content, hero)


def test_the_pack_goes_for_the_one_who_has_least_left(content: GameContent) -> None:
    """Перехватить удар в отряде нечем: стая добивает раненого."""
    from dataclasses import replace

    state, roster = build(
        content,
        [a_hero("Аргус", 1), a_hero("Мирна", 2)],
        enemies=(make_enemy(health=90_000),),
    )
    hurt = one(state, 2)
    state = state.replace_combatant(replace(hurt, health=max(1, hurt.max_health // 10)))

    working = state
    struck = 0
    for _ in range(len(state.order) * 2):
        if working.is_over:
            break
        working = act(content, roster, working, BattleAction(kind=ActionKind.ATTACK), SEED)
        for event in working.events:
            if event.actor_id == 3 and event.target_id:
                struck = event.target_id
                break
        if struck:
            break
    assert struck == 2


def test_the_pay_is_split_and_nothing_is_lost() -> None:
    assert party_rules.split(7, 2) == (4, 3)
    assert sum(party_rules.split(101, 5)) == 101
    assert party_rules.split(10, 0) == ()


def test_the_loot_goes_round_the_party() -> None:
    """Добыча раздаётся по кругу, а не оседает у собравшего отряд."""
    shares = party_rules.distribute(("a", "b", "c", "d"), (1, 2), random.Random(7))
    assert sorted(len(items) for items in shares.values()) == [2, 2]
    assert sorted(item for items in shares.values() for item in items) == ["a", "b", "c", "d"]


# --- защита ------------------------------------------------------------


def test_defending_gives_armour_by_level_and_a_third_of_a_dodge(content: GameContent) -> None:
    """Ровно то, что обещает кнопка: уровень трижды и треть уклонения."""
    hero = a_hero("Аргус", 1, level=20)
    state, roster = build(content, [hero], enemies=(make_enemy(health=9_000),))
    assert defend_armor(20) == round(20 * DEFEND_ARMOR_PER_LEVEL)

    plain = derived_stats(content, hero)
    after = act(content, roster, state, BattleAction(kind=ActionKind.DEFEND), SEED)
    guarded = one(after, 1)
    assert guarded.effects.has(StatusKind.GUARD)
    stats = derived_stats(content, hero, guarded.effects)
    assert stats.armor == plain.armor + defend_armor(20)
    assert stats.dodge == pytest.approx(min(plain.dodge + defend_dodge(), 75.0))


def test_defending_holds_until_the_next_turn_of_its_own(content: GameContent) -> None:
    """Срок укорачивается в конце того же хода, и защита обязана это пережить."""
    state, roster = build(content, [a_hero("Аргус", 1)], enemies=(make_enemy(),))
    after = act(content, roster, state, BattleAction(kind=ActionKind.DEFEND), SEED)
    assert one(after, 1).effects.turns_of(StatusKind.GUARD) >= 1


def test_defending_is_a_turn_that_happened(content: GameContent) -> None:
    """Закрыться - состоявшийся ход: противник отвечает, а след помнит оборону."""
    state, roster = build(content, [a_hero("Аргус", 1)], enemies=(make_enemy(),))
    after = act(content, roster, state, BattleAction(kind=ActionKind.DEFEND), SEED)
    assert one(after, 1).trace.last is ActionTag.GUARD
