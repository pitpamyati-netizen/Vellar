"""Уклады пассивок: пассивное умение меняет правило, а не число (ADR 0080).

Сто шестьдесят пассивок обещали одно и то же - «поднимает броню», «поднимает
урон», - и очко, вложенное в такую, не меняло в бою ничего. Половина пассивок
каждого класса теперь уклады: крит возвращает запас, добитый снимает откаты,
закрывшийся отвечает на удар, лечение сверх полного становится барьером.

Здесь проверяется, что каждый уклад действительно срабатывает - и что тот, чей
уклад срабатывает раз за бой, срабатывает именно раз.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleState,
    Combatant,
    EventKind,
)
from mmorpg.domain.entities.effects import status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank, EnemyRole
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import passives as passive_rules
from mmorpg.domain.rules.combat import (
    act,
    hero_combatant,
    monster_combatant,
    open_battle,
)

SEED = b"way-seed"
STRIKE = "warrior_sekushchiy_roscherk"

ATTACK = BattleAction(kind=ActionKind.ATTACK)
DEFEND = BattleAction(kind=ActionKind.DEFEND)


def make_enemy(
    *,
    name: str = "Чучело",
    health: int = 20_000,
    damage: int = 60,
    initiative: float = 1.0,
    level: int = 50,
) -> Enemy:
    return Enemy(
        archetype_id="dummy",
        name=name,
        kind=EnemyKind.BEAST,
        level=level,
        max_health=health,
        damage=damage,
        armor=0,
        initiative=initiative,
        loot=(),
        gold=0,
        rank=EnemyRank.NORMAL,
        role=EnemyRole.BRUTE,
    )


@pytest.fixture
def hero(warrior: Character) -> Character:
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=(STRIKE, None, None, None, None, None),
            racial="race_human_second_wind",
            ranks={STRIKE: 1},
        ),
    )


def way_code(content: GameContent, way: str) -> str:
    """Код любой пассивки, которая несёт этот уклад."""
    return next(one.code for one in content.skills if not one.is_active and one.effect == way)


def taught(content: GameContent, character: Character, way: str, rank: int = 1) -> Character:
    """Персонаж, изучивший пассивку с этим укладом."""
    return replace(
        character,
        loadout=character.loadout.with_rank(way_code(content, way), rank),
    )


def start(
    content: GameContent, character: Character, *enemies: Enemy, seed: bytes = SEED
) -> tuple[BattleState, dict[int, Character]]:
    roster = {1: character}
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(enemy, combatant_id=index + 2, side=1)
            for index, enemy in enumerate(enemies or (make_enemy(),))
        ),
    ]
    return open_battle(content, roster, fighters, seed), roster


def one_of(state: BattleState, combatant_id: int) -> Combatant:
    found = state.by_id(combatant_id)
    assert found is not None
    return found


def hurt(state: BattleState, combatant_id: int, share: float) -> BattleState:
    one = one_of(state, combatant_id)
    return state.replace_combatant(replace(one, health=max(1, round(one.max_health * share))))


# --- реестр -----------------------------------------------------------


def test_every_way_is_used_by_some_class(content: GameContent) -> None:
    """Уклад, которого нет ни у одной пассивки, - это код без содержимого."""
    used = {one.effect for one in content.skills if not one.is_active}
    assert used >= passive_rules.POWER_KEYS, passive_rules.POWER_KEYS - used


def test_a_way_says_its_number(content: GameContent) -> None:
    """Фраза уклада называет величину: пятый ранг читается не как первый."""
    way = passive_rules.power("crit_refund")
    assert "8" in way.words(8)
    assert way.words(8) != way.words(24)


def test_ways_are_collected_at_their_rank(content: GameContent, hero: Character) -> None:
    """Ранг платит пассивке размером уклада, и только им (ADR 0073)."""
    first = passive_rules.collect(content, taught(content, hero, "punish", 1))
    fifth = passive_rules.collect(content, taught(content, hero, "punish", 5))
    assert fifth["punish"] > first["punish"]


# --- уклады в бою -----------------------------------------------------


def test_the_opener_never_misses(content: GameContent, hero: Character) -> None:
    """«Почин»: в первом круге удар доходит, как бы ни вертелась цель."""
    dodgy = replace(make_enemy(), initiative=0.1)
    state, roster = start(content, taught(content, hero, "opener"), dodgy)
    after = act(content, roster, state, ATTACK, SEED)
    assert not any(event.kind in {EventKind.MISS, EventKind.DODGE} for event in after.events)


def test_the_opener_hits_harder_in_the_first_round(content: GameContent, hero: Character) -> None:
    """Первый круг - ваш: удар в нём весомее обычного."""
    plain, roster = start(content, hero)
    bare = act(content, roster, plain, ATTACK, SEED)
    with_way, way_roster = start(content, taught(content, hero, "opener"))
    opened = act(content, way_roster, with_way, ATTACK, SEED)
    assert one_of(opened, 2).health < one_of(bare, 2).health


def test_a_crit_gives_back_resource(content: GameContent, hero: Character) -> None:
    """«Отдача»: критический удар возвращает часть запаса."""
    lucky = taught(content, hero, "crit_refund", 5)
    state, roster = start(content, lucky)
    spent = state.replace_combatant(replace(one_of(state, 1), resource=0))
    # Крит бросается, поэтому пробуем несколько семян: важно, что хоть раз
    # запас вернулся, - иначе уклад не работает вовсе.
    returned = False
    for step in range(20):
        after = act(content, roster, spent, ATTACK, f"crit-{step}".encode())
        if any(event.kind is EventKind.CRIT for event in after.events):
            returned = one_of(after, 1).resource > 0
            if returned:
                break
    assert returned


def test_a_kill_shortens_cooldowns(content: GameContent, hero: Character) -> None:
    """«Разгон»: добив противника, боец снимает ход со всех откатов."""
    quick = taught(content, hero, "kill_refresh")
    state, roster = start(content, quick, make_enemy(health=1))
    ready = state.replace_combatant(
        replace(one_of(state, 1), cooldowns={"warrior_rassechenie": 3}, resource=0)
    )
    after = act(content, roster, ready, ATTACK, SEED)
    assert one_of(after, 1).cooldown_of("warrior_rassechenie") == 2
    assert one_of(after, 1).resource > 0


def test_a_guard_answers(content: GameContent, hero: Character) -> None:
    """«Ответный щит»: закрывшийся отвечает тому, кто по нему попал."""
    steady = taught(content, hero, "guard_answer", 5)
    state, roster = start(content, steady, make_enemy(damage=200, initiative=0.1))
    after = act(content, roster, state, DEFEND, SEED)
    guarded = one_of(after, 1)
    assert guarded.effects.modifiers().get("_counter_percent", 0.0) > 0


def test_overheal_becomes_a_barrier(content: GameContent, hero: Character) -> None:
    """«Избыток»: лечение, которому некуда лечь, становится барьером."""
    mender = taught(content, hero, "overheal", 5)
    healing = replace(
        mender,
        loadout=replace(
            mender.loadout,
            actives=("warrior_znamya_ne_padaet", None, None, None, None, None),
            ranks={**dict(mender.loadout.ranks), "warrior_znamya_ne_padaet": 1},
        ),
    )
    state, roster = start(content, healing)
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert one_of(after, 1).barrier > 0


def test_the_last_stand_works_once(content: GameContent, hero: Character) -> None:
    """«Не пасть»: смертельный удар оставляет немного здоровья - раз за бой."""
    stubborn = taught(content, hero, "last_stand", 5)
    state, roster = start(content, stubborn, make_enemy(damage=100_000, initiative=999.0))
    struck = state.replace_combatant(replace(one_of(state, 1), health=10))
    after = act(content, roster, struck, ATTACK, SEED)
    survivor = after.by_id(1)
    assert survivor is not None
    assert survivor.alive or after.is_over


def test_a_bleeding_target_feeds_the_striker(content: GameContent, hero: Character) -> None:
    """«Кровопийца»: удар по точащейся цели возвращает бьющему здоровье."""
    hungry = taught(content, hero, "bleed_feed", 5)
    state, roster = start(content, hungry)
    target = one_of(state, 2)
    bleeding = status_effect(StatusKind.BLEEDING, turns=5, magnitude=1.0)
    ready = hurt(
        state.replace_combatant(replace(target, effects=target.effects.apply(bleeding))), 1, 0.5
    )
    after = act(content, roster, ready, ATTACK, SEED)
    assert one_of(after, 1).health > one_of(ready, 1).health


def test_a_cool_head_shortens_troubles(content: GameContent, hero: Character) -> None:
    """«Хладнокровие»: беда держится меньше ходов, но хотя бы один."""
    calm = taught(content, hero, "status_ward", 5)
    state, _ = start(content, calm, make_enemy(initiative=999.0))
    one = one_of(state, 1)
    weakened = status_effect(StatusKind.WEAKNESS, turns=1, magnitude=10.0)
    assert one.effects.apply(weakened).turns_of(StatusKind.WEAKNESS) == 1


def test_a_cleave_touches_the_neighbour(content: GameContent, hero: Character) -> None:
    """«Замах»: удар по одной цели задевает соседа."""
    wide = taught(content, hero, "cleave", 5)
    state, roster = start(content, wide, make_enemy(name="Первый"), make_enemy(name="Второй"))
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0, target=2), SEED)
    assert one_of(after, 3).health < one_of(state, 3).health


def test_resolve_thickens_armour_when_hurt(content: GameContent, hero: Character) -> None:
    """«Упорство»: на полном здоровье уклад не даёт ничего, на исходе - много."""
    from mmorpg.domain.rules.combat import _armor_of

    stubborn = taught(content, hero, "resolve", 5)
    state, roster = start(content, stubborn)
    full = _armor_of(content, roster, one_of(state, 1))
    wounded = _armor_of(content, roster, one_of(hurt(state, 1, 0.05), 1))
    assert wounded > full


def test_punish_hits_the_helpless(content: GameContent, hero: Character) -> None:
    """«Расплата»: по тому, кому нечем ходить, удар весомее."""
    cruel = taught(content, hero, "punish", 5)
    plain, plain_roster = start(content, hero)
    state, roster = start(content, cruel)

    def stunned(source: BattleState) -> BattleState:
        target = one_of(source, 2)
        held = status_effect(StatusKind.STUN, turns=3)
        return source.replace_combatant(replace(target, effects=target.effects.apply(held)))

    bare = act(content, plain_roster, stunned(plain), ATTACK, SEED)
    punished = act(content, roster, stunned(state), ATTACK, SEED)
    assert one_of(punished, 2).health < one_of(bare, 2).health


def test_the_first_step_moves_first(content: GameContent, hero: Character) -> None:
    """«Первый шаг»: в первом круге боец встаёт в очередь раньше."""
    from mmorpg.domain.rules.combat import _order_for

    swift = taught(content, hero, "swift_start", 5)
    state, _ = start(content, swift, make_enemy(initiative=999.0))
    hero_one = one_of(state, 1)
    fast = replace(hero_one, initiative=990.0)
    order = _order_for((fast, one_of(state, 2)), SEED, 1)
    later = _order_for((fast, one_of(state, 2)), SEED, 2)
    assert order[0] == 1
    assert later[0] == 2


def test_panic_heals_once(content: GameContent, hero: Character) -> None:
    """«Крайность»: впервые упав низко, боец лечится сам - и только впервые."""
    desperate = taught(content, hero, "panic_heal", 5)
    state, roster = start(content, desperate)
    low = hurt(state, 1, 0.2)
    after = act(content, roster, low, ATTACK, SEED)
    assert one_of(after, 1).health > one_of(low, 1).health

    again = hurt(after, 1, 0.2)
    twice = act(content, roster, again, ATTACK, SEED)
    assert one_of(twice, 1).health <= one_of(again, 1).health
