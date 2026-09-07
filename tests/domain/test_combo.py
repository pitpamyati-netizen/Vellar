"""Связка: удар по цели, на которой уже что-то держится (ADR 0081).

Шесть боевых слотов собирались один раз и навсегда, потому что собирать их было
нечем: шесть самых сильных ударов били сильнее любых шести других. Связка - это
то, ради чего панель пересобирают: умение, отвечающее на состояние, бьёт заметно
сильнее, но только если в панели лежит и то, чем это состояние вешают.

Ничего нового игрок при этом не считает: связка стоит на состояниях, которые
уже есть и уже называются вслух (ADR 0066).
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
from mmorpg.domain.entities.effects import status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank, EnemyRole
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import combo as combo_rules
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle

#: Удар воина, отвечающий на кровотечение, и умение, которым его вешают.
ANSWER = "warrior_dobivanie"
SOURCE = "warrior_rassechenie"

SEED = b"combo-seed"


def make_enemy(*, health: int = 20_000, initiative: float = 1.0) -> Enemy:
    return Enemy(
        archetype_id="dummy",
        name="Чучело",
        kind=EnemyKind.BEAST,
        level=50,
        max_health=health,
        damage=1,
        armor=0,
        initiative=initiative,
        loot=(),
        gold=0,
        rank=EnemyRank.NORMAL,
        role=EnemyRole.BRUTE,
    )


@pytest.fixture
def striker(warrior: Character) -> Character:
    return replace(
        warrior,
        level=100,
        loadout=SkillLoadout(
            actives=(ANSWER, SOURCE, None, None, None, None),
            racial="race_human_second_wind",
            ranks={ANSWER: 1, SOURCE: 1},
        ),
    )


def start(content: GameContent, character: Character) -> tuple[BattleState, dict[int, Character]]:
    roster = {1: character}
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        monster_combatant(make_enemy(), combatant_id=2, side=1),
    ]
    return open_battle(content, roster, fighters, SEED), roster


def bleeding(state: BattleState, combatant_id: int) -> BattleState:
    one = state.by_id(combatant_id)
    assert one is not None
    hurt = status_effect(StatusKind.BLEEDING, turns=5, magnitude=1.0)
    return state.replace_combatant(replace(one, effects=one.effects.apply(hurt)))


def damage_done(before: BattleState, after: BattleState, combatant_id: int) -> int:
    was, now = before.by_id(combatant_id), after.by_id(combatant_id)
    assert was is not None and now is not None
    return was.health - now.health


# --- сама связка ------------------------------------------------------


def test_a_skill_answers_a_named_status(content: GameContent) -> None:
    """Умение отвечает на состояние, названное содержимым, и только на него."""
    answer = content.skill(ANSWER)
    assert answer.answers is StatusKind.BLEEDING
    assert content.skill("warrior_sekushchiy_roscherk").answers is None


def test_the_combo_hits_harder(content: GameContent, striker: Character) -> None:
    """По цели с нужным состоянием тот же удар бьёт заметно сильнее."""
    plain, roster = start(content, striker)
    hit = act(content, roster, plain, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    without = damage_done(plain, hit, 2)

    bled = bleeding(plain, 2)
    struck = act(content, roster, bled, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    with_combo = damage_done(bled, struck, 2)

    assert with_combo > without
    assert any(event.kind is EventKind.COMBO for event in struck.events)


def test_the_combo_is_silent_without_its_status(content: GameContent, striker: Character) -> None:
    """Умение без своего состояния работает как работало и молчит об этом."""
    plain, roster = start(content, striker)
    hit = act(content, roster, plain, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert not any(event.kind is EventKind.COMBO for event in hit.events)


def test_the_factor_is_named_by_the_rules(content: GameContent) -> None:
    """Множитель связки один на игру и назван числом, а не подобран на месте."""
    answer = content.skill(ANSWER)
    plain = Character(id=1, user_id=1, name="Т", race_id="human", class_id="warrior", level=50)
    state, _ = start(content, plain)
    target = state.by_id(2)
    assert target is not None
    assert combo_rules.damage_factor(answer, target) == 1.0

    bled = bleeding(state, 2).by_id(2)
    assert bled is not None
    assert combo_rules.damage_factor(answer, bled) == pytest.approx(
        1.0 + combo_rules.DAMAGE_BONUS / 100.0
    )


def test_the_demand_is_said_aloud(content: GameContent) -> None:
    """По чему умение бьёт сильнее, игрок узнаёт до боя, а не в бою."""
    said = combo_rules.demand_line(content.skill(ANSWER))
    assert "Связка" in said
    assert combo_rules.answer_word(StatusKind.BLEEDING) in said
    assert combo_rules.demand_line(content.skill("warrior_sekushchiy_roscherk")) == ""


def test_every_class_can_build_a_combo(content: GameContent) -> None:
    """У каждого класса есть и чем повесить состояние, и чем на него ответить.

    Связка, у которой нет источника в том же классе, - это обещание, которого
    игроку нечем исполнить.
    """
    from mmorpg.domain.rules.skill_effects import spec_for

    for klass in content.classes:
        skills = [one for one in content.skills if one.owner_id == klass.id and one.is_active]
        answers = {one.answers for one in skills if one.answers is not None}
        assert answers, klass.id
        left: set[StatusKind] = set()
        for one in skills:
            spec = spec_for(one.effect)
            if spec.dot_turns:
                left.add(spec.dot_status)
            left.update(inflict.kind for inflict in spec.inflicts)
        assert answers <= left, (klass.id, answers - left)
