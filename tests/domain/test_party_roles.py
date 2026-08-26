"""Места в отряде и защита: что они делают на самом деле.

Пятеро в отряде, у каждого своё дело, и ни одно дело не даётся даром
(``entities/party.py``, ADR 0025). Здесь проверяется не текст на кнопке, а
числа: щит и правда держит удар, дозорный и правда ходит раньше, а стая и правда
идёт на щита, пока он держится.

Защита - тот же разговор о числах: ход отдан обороне, и за него дают брони по
уровню и треть уклонения.
"""

from __future__ import annotations

from dataclasses import replace

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
from mmorpg.domain.entities.party import (
    ROLE_DUTIES,
    ROLE_MODIFIERS,
    ROLE_NAMES,
    SHIELD_HOLDS_ABOVE,
    PartyRole,
    role_by_word,
    role_name,
)
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
from mmorpg.domain.rules.modifiers import EFFECTIVE_KEYS
from mmorpg.domain.rules.stats import derived_stats

SEED = b"roles-seed-00001"


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
    party: list[tuple[Character, PartyRole | None]],
    enemies: tuple[Enemy, ...] = (),
) -> tuple[BattleState, dict[int, Character]]:
    """Бой отряда со стаей: у каждого своё место, у каждого свой номер."""
    roster: dict[int, Character] = {}
    fighters: list[Combatant] = []
    next_id = 1
    for character, place in party:
        fighters.append(
            hero_combatant(
                content,
                character,
                combatant_id=next_id,
                side=ATTACKERS,
                live=True,
                role=place,
            )
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


# --- сам список мест ---------------------------------------------------


def test_every_place_is_named_and_says_what_it_does() -> None:
    """Место без имени и без дела - это строка на экране, а не место."""
    for role in PartyRole:
        assert ROLE_NAMES[role]
        assert ROLE_DUTIES[role]
        assert role in ROLE_MODIFIERS


def test_no_place_promises_what_the_engine_does_not_count() -> None:
    """Прибавка под ключом, которого никто не читает, - обещание (правило 7)."""
    for role, bundle in ROLE_MODIFIERS.items():
        for key in bundle:
            assert key in EFFECTIVE_KEYS, f"{role}: {key}"


def test_the_leader_promises_nothing_and_the_others_pay_for_what_they_get() -> None:
    """Вожак - права, а не числа; остальные места платят за прибавку."""
    assert not ROLE_MODIFIERS[PartyRole.LEADER]
    for role in (PartyRole.SHIELD, PartyRole.MENDER, PartyRole.BLADE, PartyRole.SCOUT):
        bundle = ROLE_MODIFIERS[role]
        assert any(value > 0 for value in bundle.values()), role
        assert any(value < 0 or key == "damage_taken_percent" for key, value in bundle.items()), (
            f"{role} даётся даром"
        )


def test_a_place_is_taken_by_the_word_the_player_types() -> None:
    assert role_by_word("щит") is PartyRole.SHIELD
    assert role_by_word("TANK") is PartyRole.SHIELD
    assert role_by_word("лекарь") is PartyRole.MENDER
    assert role_by_word("ничего") is None
    assert role_name(None) == "без места"


# --- отряд из пятерых --------------------------------------------------


def test_five_fit_into_the_party_and_the_sixth_does_not() -> None:
    assert party_rules.MAX_MEMBERS == 5
    party = party_rules.Party(leader_id=1, members=(1, 2, 3, 4, 5))
    assert party.full
    assert party.with_member(6).size == 5


def test_a_place_belongs_to_one_person() -> None:
    """Два щита в отряде - это не строй, а двое, которых бьют по очереди."""
    party = party_rules.Party(leader_id=1, members=(1, 2, 3))
    taken = party.with_role(2, PartyRole.SHIELD)
    assert taken.role_of(2) is PartyRole.SHIELD
    assert taken.with_role(3, PartyRole.SHIELD).role_of(3) is None
    assert party_rules.role_refusal(taken, 3, PartyRole.SHIELD, "Мирна")


def test_the_leader_stands_leader_without_choosing() -> None:
    party = party_rules.Party(leader_id=1, members=(1, 2))
    assert party.role_of(1) is PartyRole.LEADER
    assert party.role_of(2) is None
    assert party_rules.role_refusal(party, 1, PartyRole.LEADER, "")


def test_leaving_frees_the_place() -> None:
    """Щит, которого нет в бою, - не щит, а строка в списке."""
    party = party_rules.Party(leader_id=1, members=(1, 2)).with_role(2, PartyRole.SHIELD)
    left = party.without(2)
    assert left.holder_of(PartyRole.SHIELD) == 0


def test_a_place_can_be_given_up() -> None:
    party = party_rules.Party(leader_id=1, members=(1, 2)).with_role(2, PartyRole.BLADE)
    assert party.with_role(2, None).role_of(2) is None


# --- что места делают в бою --------------------------------------------


def test_the_shield_holds_more_and_hits_softer(content: GameContent) -> None:
    """Броня выше, удар слабее - и оба числа считает движок, а не описание."""
    hero = a_hero("Аргус", 1)
    plain = derived_stats(content, hero)
    state, _ = build(content, [(hero, PartyRole.SHIELD)], enemies=(make_enemy(),))
    shielded = one(state, 1)
    assert derived_stats(content, hero, shielded.effects).armor > plain.armor
    assert shielded.effects.modifiers()["damage_percent"] < 0


def test_the_scout_goes_earlier_and_holds_less(content: GameContent) -> None:
    """Дозорный и правда ходит раньше: инициатива - это очередь удара."""
    hero = a_hero("Мирна", 1)
    plain, _ = build(content, [(hero, None)], enemies=(make_enemy(),))
    scouted, _ = build(content, [(hero, PartyRole.SCOUT)], enemies=(make_enemy(),))
    assert one(scouted, 1).initiative > one(plain, 1).initiative
    assert one(scouted, 1).max_health < one(plain, 1).max_health


def test_the_place_survives_a_cleanse(content: GameContent) -> None:
    """Место снять нельзя: это не то, что повесили, а то, кем боец стоит."""
    state, _ = build(content, [(a_hero("Аргус", 1), PartyRole.SHIELD)], enemies=(make_enemy(),))
    shield = one(state, 1)
    cleansed = shield.effects.cleanse(5, beneficial=True)
    assert cleansed.modifiers().get("armor_percent") == pytest.approx(40.0)


def whom_the_pack_hits(
    content: GameContent, roster: dict[int, Character], state: BattleState, pack_id: int
) -> int:
    """Кого волк ударил первым. Ходы игроков крутятся, пока очередь не дойдёт.

    Смотреть приходится по событиям, а не по здоровью: удар может и не дойти, а
    выбор цели волк делает всё равно.
    """
    working = state
    for _ in range(len(state.order) * 2):
        if working.is_over:
            break
        working = act(content, roster, working, BattleAction(kind=ActionKind.ATTACK), SEED)
        for event in working.events:
            if event.actor_id == pack_id and event.target_id:
                return event.target_id
    return 0


def test_the_pack_goes_for_the_shield_while_it_holds(content: GameContent) -> None:
    """Пока щит на ногах, стая идёт на него, а не на того, кто мягче."""
    shield, mender = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, roster = build(
        content,
        [(shield, PartyRole.SHIELD), (mender, PartyRole.MENDER)],
        enemies=(make_enemy(health=90_000),),
    )
    hurt = one(state, 2)
    # Лекарь ранен куда сильнее щита: без места стая пошла бы на него.
    state = state.replace_combatant(replace(hurt, health=max(1, hurt.max_health // 10)))
    assert whom_the_pack_hits(content, roster, state, pack_id=3) == 1


def test_the_pack_smells_the_broken_shield(content: GameContent) -> None:
    """Ниже четверти здоровья щит перестаёт держать: держать его - дело лекаря."""
    shield, mender = a_hero("Аргус", 1), a_hero("Мирна", 2)
    state, roster = build(
        content,
        [(shield, PartyRole.SHIELD), (mender, PartyRole.MENDER)],
        enemies=(make_enemy(health=90_000),),
    )
    broken, hurt = one(state, 1), one(state, 2)
    # Щит ниже четверти, лекарь ранен ещё сильнее: пока щит держался, били его -
    # и это соседний тест; теперь стая берётся за того, кому осталось меньше.
    state = state.replace_combatant(
        replace(broken, health=int(broken.max_health * SHIELD_HOLDS_ABOVE) - 1)
    )
    state = state.replace_combatant(replace(hurt, health=max(1, hurt.max_health // 10)))
    assert whom_the_pack_hits(content, roster, state, pack_id=3) == 2


# --- защита ------------------------------------------------------------


def test_defending_gives_armour_by_level_and_a_third_of_a_dodge(content: GameContent) -> None:
    """Ровно то, что обещает кнопка: уровень трижды и треть уклонения."""
    hero = a_hero("Аргус", 1, level=20)
    state, roster = build(content, [(hero, None)], enemies=(make_enemy(health=9_000),))
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
    state, roster = build(content, [(a_hero("Аргус", 1), None)], enemies=(make_enemy(),))
    after = act(content, roster, state, BattleAction(kind=ActionKind.DEFEND), SEED)
    assert one(after, 1).effects.turns_of(StatusKind.GUARD) >= 1


def test_defending_is_a_turn_that_happened(content: GameContent) -> None:
    """Закрыться - состоявшийся ход: противник отвечает, а след помнит оборону."""
    state, roster = build(content, [(a_hero("Аргус", 1), None)], enemies=(make_enemy(),))
    after = act(content, roster, state, BattleAction(kind=ActionKind.DEFEND), SEED)
    assert one(after, 1).trace.last is ActionTag.GUARD
