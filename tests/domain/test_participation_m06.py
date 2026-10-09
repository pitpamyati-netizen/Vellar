"""Участие, поддержка и общий фонд: активный, поздний, павший и вышедший."""

import json
from dataclasses import replace

import pytest

from mmorpg.application.services.battle import begin, deserialise, serialise
from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.combat import ActionKind, BattleAction
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRole
from mmorpg.domain.rules import adventure, combat, expedition, participation, quests
from mmorpg.presentation.telegram.flows import combat as flow
from mmorpg.presentation.telegram.flows.state import Descent, PlayState
from mmorpg.presentation.telegram.screens.combat import phase_lines


def hero(key=1, cls="warrior"):
    return Character(
        id=key, user_id=1000 + key, name=f"Герой {key}", race_id="human", class_id=cls, level=15
    )


def battle(content, count=2, cls="warrior"):
    enemy = Enemy(
        archetype_id="grey_wolf",
        name="Враг",
        kind=EnemyKind.BEAST,
        level=1,
        max_health=100000,
        damage=1,
        armor=0,
        initiative=0,
        loot=(),
        gold=10,
    )
    return begin(
        content,
        battle_id="m06",
        attackers=[(hero(key, cls), True) for key in range(1, count + 1)],
        enemies=(enemy,),
        seed=b"m06",
        participation_rule=1,
    )


@pytest.mark.parametrize("count", [1, 2, 5])
def test_pool_is_bounded_and_cooperation_keeps_one_common_fund(count):
    shares = participation.reward_shares(101, count)
    assert len(shares) == count
    assert sum(shares) == 101 * (100 + 20 * (count - 1)) // 100
    assert max(shares) - min(shares) <= 1
    assert sum(shares) <= 101 * 180 // 100
    assert participation.reward_shares(-1, count) == (0,) * count


def test_invalid_sizes_and_idle_do_not_increase_pool(content):
    assert participation.reward_shares(100, 0) == ()
    with pytest.raises(ValueError):
        participation.reward_shares(100, 6)
    session, _ = battle(content)
    active = replace(session.state.heroes()[0], actions=1)
    idle = session.state.heroes()[1]
    assert participation.eligible(active)
    assert not participation.eligible(idle)
    assert participation.eligible(replace(active, health=0))
    assert not participation.eligible(replace(active, left=True))
    assert not participation.eligible(replace(active, live=False))


@pytest.mark.parametrize(
    "cls", ["warrior", "barbarian", "paladin", "ranger", "rogue", "mage", "cleric", "druid"]
)
def test_universal_assist_is_unavailable_for_every_class(content, cls):
    session, roster = battle(content, cls=cls)
    actor = session.state.active
    screen = flow.render(content, roster[actor.id], session, actor.id)
    assert not any(
        text.startswith("Прикрыть товарища") for row in screen.button_texts() for text in row
    )
    for text in (
        "/прикрыть",
        "прикрыть",
        "Прикрыть товарища",
        "Прикрыть товарища — Герой 2, барьер 50",
    ):
        assert flow.action_for(content, roster[actor.id], session, actor.id, text) is None
        unchanged, notice = flow.advance(content, roster, session, actor.id, text)
        assert unchanged == session and notice
        assert all(one.barrier == 0 and one.actions == 0 for one in unchanged.state.heroes())


def test_refused_skill_reading_and_focus_are_not_participation(content):
    session, roster = battle(content)
    actor = session.state.active
    for text in ("/обновить", "/разбор", "/цель 3", "/умение 6"):
        changed, _ = flow.advance(content, roster, session, actor.id, text)
        assert changed.state.by_id(actor.id).actions == 0


def test_defence_counts_and_participation_survives_serialisation(content):
    session, roster = battle(content)
    actor = session.state.active
    state = combat.act(
        content, roster, session.state, BattleAction(ActionKind.DEFEND), session.seed
    )
    session = replace(session, state=state)
    assert state.by_id(actor.id).actions == 1
    assert deserialise(serialise(session)) == session
    descent = Descent(city_id="farhold", credits=((1, 2),), excluded=(2,), participation_rule=1)
    assert PlayState.deserialise(PlayState(descent=descent).serialise()).descent == descent


def test_full_run_late_join_and_withdrawal_credit(content):
    session, _ = battle(content, 5)
    members = tuple(replace(one, actions=1) for one in session.state.heroes())
    credit = participation.room_credit((), members[:2], 0)
    assert credit == ((1, 1), (2, 1))
    credit = participation.room_credit(credit, members, 1)
    assert credit == ((1, 2), (2, 2))
    credit = participation.room_credit(credit, (members[0], replace(members[1], left=True)), 2)
    assert credit == ((1, 3),)
    credit = participation.room_credit(credit, (replace(members[0], health=0), *members[1:]), 3)
    assert credit == ((1, 4),)


def test_catalogue_and_phase_warning_are_deterministic(content):
    city = content.city("farhold")
    route = city.dungeon("farhold_flooded_drift")
    assert len(route.encounters) == 4
    assert len({one.enemies for one in route.encounters}) == 4
    assert expedition.meeting(content, "missing", "missing", 0) is None
    assert expedition.meeting(content, "farhold", "missing", 0) is None
    assert expedition.meeting(content, "farhold", route.id, 4) is None
    assert expedition.meeting(content, "farhold", route.id, 0) == route.encounters[0]
    encounter = route.encounters[2]
    args = {"seed": b"m06", "level": 15, "stakes": 1.0, "bounty": 1.0}
    enemies = expedition.foes(content, encounter, **args)
    assert enemies == expedition.foes(content, encounter, **args)
    session, _ = begin(
        content, battle_id="phase", attackers=[(hero(), True)], enemies=enemies, seed=b"phase"
    )
    caster = next(one for one in session.state.living() if one.enemy)
    assert caster.enemy.role is EnemyRole.CASTER
    warning = phase_lines(session.state)
    next_round = next(
        n
        for n in range(session.state.round, session.state.round + 3)
        if (n + caster.id) % combat.ROLE_MOVE_EVERY == 0
    )
    assert f"круге {next_round}" in warning[0]
    assert phase_lines(replace(session.state, combatants=())) == ()


def test_descent_quest_counts_only_the_right_place_and_stops_at_target(content):
    character = hero()
    character = replace(character, quests=character.quests.take("farhold_pump_house"))
    log, steps = quests.record_descent(
        content, character, city_id="farhold", dungeon_id="farhold_flooded_drift"
    )
    assert steps[0].progress == 1
    assert not quests.record_descent(
        content,
        replace(character, quests=log),
        city_id="farhold",
        dungeon_id="farhold_flooded_drift",
    )[1]
    assert not quests.record_descent(content, character, city_id="farhold", dungeon_id="wrong")[1]
    assert not quests.record_descent(
        content, character, city_id="wrong", dungeon_id="farhold_flooded_drift"
    )[1]


def test_group_health_does_not_multiply_damage_loot_or_base_reward(content):
    encounter = content.city("farhold").deep_dungeon.encounters[0]
    args = {"seed": b"health", "level": 15, "stakes": 1.0, "bounty": 1.0}
    solo = expedition.foes(content, encounter, **args)
    group = expedition.foes(content, encounter, participants=5, **args)
    assert group[0].max_health == solo[0].max_health * 2
    assert replace(group[0], max_health=solo[0].max_health) == solo[0]
    with pytest.raises(ValueError):
        expedition.foes(content, encounter, participants=6, **args)


def test_three_compositions_finish_and_each_member_has_full_credit(content):
    from scripts.m06_descent_report import COMPOSITIONS, probe

    for composition in COMPOSITIONS:
        for trial in range(4):
            result = probe(content, composition, trial)
            assert result["completed"] == 4, result
            assert result["full_participants"] == list(range(1, len(composition) + 1)), result


@pytest.mark.parametrize("members", [1, 2, 5])
def test_bottom_has_one_item_and_one_bounded_fund(content, members):
    for index in range(16):
        args = {"level": 15, "seed": f"bottom{index}".encode()}
        solo = adventure.descent_prize(content, hero(), **args)
        prizes = [
            adventure.descent_prize(
                content, hero(key + 1), members=members, share_index=key, **args
            )
            for key in range(members)
        ]
        assert sum(one.gold for one in prizes) == sum(
            participation.reward_shares(solo.gold, members)
        )
        assert sum(bool(one.item_id) for one in prizes) == bool(solo.item_id)
        assert {one.item_id for one in prizes if one.item_id} == (
            {solo.item_id} if solo.item_id else set()
        )


def test_old_battle_and_descent_do_not_get_a_retroactive_participation_requirement(content):
    session, _ = battle(content)
    old = json.loads(serialise(session))
    old.pop("participation_rule")
    old.pop("briefing")
    for actor in old["combatants"]:
        actor.pop("actions")
    restored = deserialise(json.dumps(old))
    assert restored.participation_rule == 0 and not restored.briefing
    assert all(one.actions == 0 for one in restored.state.combatants)
    state = json.loads(PlayState(descent=Descent(city_id="farhold")).serialise())
    state["descent"] = state["descent"][:12]
    assert PlayState.deserialise(json.dumps(state)).descent.participation_rule == 0
