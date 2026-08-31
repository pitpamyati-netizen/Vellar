"""Двадцать три состояния: что каждое из них делает на самом деле.

Раньше половина этого была признаком внутри бойца - ``stunned`` считал ходы, а
горение отличалось от кровотечения только названием умения. Здесь проверяется,
что состояние висит на бойце по имени, что его видно, что оно кончается и что
оно делает ровно то, что за ним объявлено (``entities/statuses.py``).
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
from mmorpg.domain.entities.damage import DamageType
from mmorpg.domain.entities.effects import status_effect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import (
    CONTROL_STATUSES,
    DOT_STATUSES,
    STATUSES,
    StatusKind,
)
from mmorpg.domain.rules.combat import (
    act,
    hero_combatant,
    monster_combatant,
    open_battle,
    spend_dot,
)


def enemy(name: str = "Волк", health: int = 8_000, damage: int = 5) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=5,
        max_health=health,
        damage=damage,
        armor=0,
        initiative=1.0,
        loot=(),
        gold=10,
    )


def caster(class_id: str, *skills: str) -> Character:
    actives: list[str | None] = [*skills] + [None] * (6 - len(skills))
    return Character(
        id=1,
        user_id=1,
        name="Тест",
        race_id="human",
        class_id=class_id,
        level=100,
        loadout=SkillLoadout(actives=tuple(actives)),
    )


def start(content: GameContent, character: Character, count: int = 1) -> BattleState:
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(monster_combatant(enemy(), combatant_id=index + 2, side=1) for index in range(count)),
    ]
    return open_battle(content, {1: character}, fighters, b"status-seed")


def hero(state: BattleState) -> Combatant:
    one = state.by_id(1)
    assert one is not None
    return one


def foe(state: BattleState, index: int = 0) -> Combatant:
    one = state.by_id(index + 2)
    assert one is not None
    return one


def use(
    content: GameContent, character: Character, state: BattleState, slot: int = 0
) -> BattleState:
    return act(
        content,
        {1: character},
        state,
        BattleAction(kind=ActionKind.SKILL, slot=slot),
        b"\x01" * 16,
    )


def held(one: Combatant) -> set[StatusKind]:
    return {effect.status for effect in one.effects.statuses() if effect.status is not None}


# --- сам список -------------------------------------------------------


def test_there_are_twenty_three_statuses_and_every_one_is_described() -> None:
    assert len(StatusKind) == 23
    for kind in StatusKind:
        spec = STATUSES[kind]
        assert spec.name, kind
        assert spec.kind is kind


def test_control_and_dot_families_are_what_they_say() -> None:
    """Отнимают ход четверо, точат каждый ход трое - и это разные списки."""
    assert {
        StatusKind.STUN,
        StatusKind.FREEZE,
        StatusKind.FEAR,
    } == CONTROL_STATUSES
    assert {StatusKind.BURNING, StatusKind.POISON, StatusKind.BLEEDING} == DOT_STATUSES
    assert STATUSES[StatusKind.BURNING].damage is DamageType.FIRE
    assert STATUSES[StatusKind.POISON].damage is DamageType.POISON
    assert STATUSES[StatusKind.BLEEDING].damage is DamageType.RENDING


def test_every_status_is_reachable_from_content(content: GameContent) -> None:
    """Состояние, которое не вешает ни одно умение, - это код без содержимого.

    Кроме защиты: её вешает не умение, а кнопка, которая есть у всякого в бою
    (``rules/combat._defend``), и содержимому она поэтому не принадлежит.
    """
    from mmorpg.domain.rules.skill_effects import spec_for

    reachable: set[StatusKind] = {StatusKind.GUARD}
    for skill in content.skills:
        if not skill.is_active:
            continue
        spec = spec_for(skill.effect)
        reachable.update(one.kind for one in (*spec.inflicts, *spec.holds))
        if spec.dot_turns:
            reachable.add(spec.dot_status)
        if spec.stun_turns:
            reachable.add(StatusKind.STUN)
        if spec.category.value == "barrier":
            reachable.add(StatusKind.BARRIER)
    assert set(StatusKind) - reachable == set()


# --- один ключ на состояние -------------------------------------------


def test_the_same_status_from_two_sources_is_one_status() -> None:
    """Горение от жезла и горение от стрелы - одно горение, а не два."""
    from mmorpg.domain.entities.effects import EffectStack

    stack = EffectStack()
    stack = stack.apply(status_effect(StatusKind.BURNING, turns=2, magnitude=10, source="a"))
    stack = stack.apply(status_effect(StatusKind.BURNING, turns=4, magnitude=6, source="b"))
    assert len(stack.statuses()) == 1
    # Остаётся больший срок и большая величина: состояние обновляется, а не
    # складывается вдвое.
    assert stack.turns_of(StatusKind.BURNING) == 4
    assert stack.magnitude_of(StatusKind.BURNING) == pytest.approx(10)


# --- что состояния делают в бою ---------------------------------------


def test_silence_refuses_a_skill_without_spending_the_turn(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    silenced = state.replace_combatant(
        replace(
            hero(state),
            effects=hero(state).effects.apply(status_effect(StatusKind.SILENCE, turns=2)),
        )
    )
    after = use(content, warrior, silenced)
    assert [event.kind for event in after.events] == [EventKind.SILENCED]
    # Ход остался за игроком: отказ ходом не считается (ADR 0016).
    assert after.active is not None and after.active.id == 1
    assert foe(after).health == foe(silenced).health


def test_invulnerability_lets_nothing_through(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    guarded = state.replace_combatant(
        replace(
            foe(state),
            effects=foe(state).effects.apply(status_effect(StatusKind.INVULNERABILITY, turns=2)),
        )
    )
    after = use(content, warrior, guarded)
    assert any(event.kind is EventKind.IMMUNE for event in after.events)
    assert foe(after).health == foe(guarded).health


def test_fear_is_shaken_off_by_the_first_blow(content: GameContent) -> None:
    """Тем страх и отличается от оглушения: испуганного приводят в чувство."""
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    scared = state.replace_combatant(
        replace(
            foe(state), effects=foe(state).effects.apply(status_effect(StatusKind.FEAR, turns=3))
        )
    )
    assert StatusKind.FEAR in held(foe(scared))
    after = use(content, warrior, scared)
    assert StatusKind.FEAR not in held(foe(after))


def test_stun_and_freeze_take_the_turn_away(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    for kind in (StatusKind.STUN, StatusKind.FREEZE):
        state = start(content, warrior)
        frozen = state.replace_combatant(
            replace(foe(state), effects=foe(state).effects.apply(status_effect(kind, turns=1)))
        )
        after = use(content, warrior, frozen)
        skipped = [
            event
            for event in after.events
            if event.kind is EventKind.TURN_SKIPPED and event.actor_id == 2
        ]
        assert skipped, kind
        assert skipped[0].effect_name == STATUSES[kind].name
        # Срок кончился в его же ход: состояние не висит вечно.
        assert kind not in held(foe(after))


def test_a_charmed_fighter_strikes_its_own_side(content: GameContent) -> None:
    """Очарование не отнимает ход - оно отнимает выбор, куда его потратить."""
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior, count=2)
    charmed = state.replace_combatant(
        replace(
            foe(state), effects=foe(state).effects.apply(status_effect(StatusKind.CHARM, turns=2))
        )
    )
    after = use(content, warrior, charmed)
    struck_ally = [
        event
        for event in after.events
        if event.kind in {EventKind.DAMAGE, EventKind.CRIT}
        and event.actor_id == 2
        and event.target_id == 3
    ]
    assert struck_ally, "очарованный бьёт по своим"


def test_heal_block_stops_healing_outright(content: GameContent) -> None:
    cleric = caster("cleric", "cleric_slovo_istseleniya")
    state = start(content, cleric)
    wounded = state.replace_combatant(replace(hero(state), health=100))
    healed = use(content, cleric, wounded)
    assert hero(healed).health > 100

    blocked = wounded.replace_combatant(
        replace(
            hero(wounded),
            effects=hero(wounded).effects.apply(status_effect(StatusKind.HEAL_BLOCK, turns=3)),
        )
    )
    denied = use(content, cleric, blocked)
    assert not any(event.kind is EventKind.HEAL for event in denied.events)


def test_burning_poison_and_bleeding_each_burn_by_their_own_kind(content: GameContent) -> None:
    """Урон по ходам смягчается сопротивлением тому роду, каким он идёт."""
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    for kind, resist in (
        (StatusKind.BURNING, "resist_fire_percent"),
        (StatusKind.POISON, "resist_poison_percent"),
        (StatusKind.BLEEDING, "resist_rending_percent"),
    ):
        target = foe(state)
        plain = state.replace_combatant(
            replace(
                target, effects=target.effects.apply(status_effect(kind, turns=2, magnitude=100))
            )
        )
        hurt = spend_dot(plain, target.id)
        assert foe(hurt).health == target.health - 100, kind

        from mmorpg.domain.entities.effects import ActiveEffect

        warded = plain.replace_combatant(
            replace(
                foe(plain),
                effects=foe(plain).effects.apply(
                    ActiveEffect(id="ward", name="Оберег", modifiers={resist: 50.0}, turns_left=5)
                ),
            )
        )
        softened = spend_dot(warded, target.id)
        assert foe(softened).health == target.health - 50, kind


def test_a_barrier_absorbs_and_then_burns_out(content: GameContent) -> None:
    mage = caster("mage", "mage_kamennaya_kozha")
    state = start(content, mage)
    warded = use(content, mage, state)
    assert hero(warded).barrier > 0
    assert StatusKind.BARRIER in held(hero(warded))

    # Барьер стоит ровно столько, сколько его держат: срок вышел - и его нет.
    working = warded
    for _ in range(6):
        working = act(
            content, {1: mage}, working, BattleAction(kind=ActionKind.ATTACK), b"\x02" * 16
        )
    assert hero(working).barrier == 0
    assert StatusKind.BARRIER not in held(hero(working))


def test_haste_and_slow_move_the_queue(content: GameContent) -> None:
    """Ускорение и замедление двигают инициативу, а инициатива - это очередь."""
    fast = status_effect(StatusKind.HASTE, turns=2, magnitude=40)
    slow = status_effect(StatusKind.SLOW, turns=2, magnitude=40)
    assert fast.modifiers["initiative_percent"] == pytest.approx(40)
    assert slow.modifiers["initiative_percent"] == pytest.approx(-40)


def test_weakness_empower_and_berserk_speak_in_percentages() -> None:
    weak = status_effect(StatusKind.WEAKNESS, turns=2, magnitude=25)
    strong = status_effect(StatusKind.EMPOWER, turns=2, magnitude=25)
    raging = status_effect(StatusKind.BERSERK, turns=2, magnitude=25)
    assert weak.modifiers["damage_percent"] == pytest.approx(-25)
    assert strong.modifiers["damage_percent"] == pytest.approx(25)
    # Берсерк платит за свой урон получаемым: это его цена, а не опечатка.
    assert raging.modifiers["damage_percent"] == pytest.approx(25)
    assert raging.modifiers["damage_taken_percent"] == pytest.approx(25)


def test_resource_block_stops_the_pool_from_filling(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    spent = state.replace_combatant(replace(hero(state), resource=0))
    filling = act(content, {1: warrior}, spent, BattleAction(kind=ActionKind.ATTACK), b"\x03" * 16)
    assert hero(filling).resource > 0

    barred = spent.replace_combatant(
        replace(
            hero(spent),
            effects=hero(spent).effects.apply(status_effect(StatusKind.RESOURCE_BLOCK, turns=3)),
        )
    )
    dry = act(content, {1: warrior}, barred, BattleAction(kind=ActionKind.ATTACK), b"\x03" * 16)
    assert hero(dry).resource == 0


def test_undying_promise_also_clears_what_would_take_the_turn(content: GameContent) -> None:
    """«Вас нельзя оглушить» держится с той стороны, с какой его дают."""
    from mmorpg.domain.entities.effects import ActiveEffect
    from mmorpg.domain.rules.skill_effects import UNSTUNNABLE

    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    steady = state.replace_combatant(
        replace(
            hero(state),
            effects=hero(state)
            .effects.apply(
                ActiveEffect(id="oath", name="Клятва", modifiers={UNSTUNNABLE: 1.0}, turns_left=5)
            )
            .apply(status_effect(StatusKind.STUN, turns=2)),
        )
    )
    after = act(content, {1: warrior}, steady, BattleAction(kind=ActionKind.ATTACK), b"\x04" * 16)
    assert not any(
        event.kind is EventKind.TURN_SKIPPED and event.actor_id == 1 for event in after.events
    )


# --- уход из виду: нельзя выбрать целью, пока не проявился (ADR 0043) --


def _vanished(content: GameContent) -> tuple[Character, BattleState]:
    rogue = caster("rogue", "rogue_skrytnost")
    state = start(content, rogue)
    hidden = use(content, rogue, state, slot=0)
    assert StatusKind.UNSEEN in held(hero(hidden)), "«Скрытность» вешает незаметность"
    return rogue, hidden


def test_vanish_takes_the_hero_off_the_target_list(content: GameContent) -> None:
    rogue = caster("rogue", "rogue_skrytnost")
    state = start(content, rogue)
    before = hero(state).health
    after = use(content, rogue, state, slot=0)

    # Противник в бою остаётся, но выбрать героя не может - и бьёт впустую.
    assert foe(after).alive
    assert after.visible_foes_of(foe(after).id) == ()
    assert any(
        event.kind is EventKind.NO_TARGET and event.actor_id == foe(after).id
        for event in after.events
    )
    assert hero(after).health == before


def test_defence_keeps_the_hero_unseen_but_any_other_action_reveals(
    content: GameContent,
) -> None:
    rogue, hidden = _vanished(content)

    guarded = act(content, {1: rogue}, hidden, BattleAction(kind=ActionKind.DEFEND), b"\x07" * 16)
    assert StatusKind.UNSEEN in held(hero(guarded)), "защита незаметность не снимает"

    struck = act(content, {1: rogue}, guarded, BattleAction(kind=ActionKind.ATTACK), b"\x08" * 16)
    assert StatusKind.UNSEEN not in held(hero(struck)), "всякое другое действие выдаёт"
    assert any(
        event.kind is EventKind.STATUS_ENDED
        and event.effect_name == STATUSES[StatusKind.UNSEEN].name
        for event in struck.events
    )


def test_the_cast_that_hides_you_does_not_immediately_reveal_you(content: GameContent) -> None:
    """Тот ход, на котором незаметность повесили, её не снимает."""
    _rogue, hidden = _vanished(content)
    assert StatusKind.UNSEEN in held(hero(hidden))


def test_an_area_blow_finds_the_unseen_and_reveals_them(content: GameContent) -> None:
    rogue = caster("rogue", "rogue_veer_klinkov")
    rogue = replace(rogue, loadout=replace(rogue.loadout, ranks={"rogue_veer_klinkov": 1}))
    state = start(content, rogue, count=2)
    marked = foe(state, 0)
    state = state.replace_combatant(
        replace(marked, effects=marked.effects.apply(status_effect(StatusKind.UNSEEN, turns=3)))
    )
    for seed in range(40):
        after = act(
            content,
            {1: rogue},
            state,
            BattleAction(kind=ActionKind.SKILL, slot=0),
            seed.to_bytes(16, "big"),
        )
        struck = after.by_id(marked.id)
        if struck is not None and struck.health < marked.health:
            assert StatusKind.UNSEEN not in held(struck), "удар по всем находит и выдаёт"
            return
    pytest.fail("веер клинков ни разу не задел ушедшего за 40 семян")


def test_a_dot_reveals_the_unseen(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior)
    target = foe(state)
    afflicted = state.replace_combatant(
        replace(
            target,
            effects=target.effects.apply(status_effect(StatusKind.UNSEEN, turns=3)).apply(
                status_effect(StatusKind.POISON, turns=3, magnitude=50)
            ),
        )
    )
    assert StatusKind.UNSEEN in held(foe(afflicted))
    ticked = spend_dot(afflicted, target.id)
    assert StatusKind.UNSEEN not in held(foe(ticked)), "долетевший дот выдаёт"


def test_a_flash_grenade_reveals_everyone_and_blinds_them(content: GameContent) -> None:
    warrior = caster("warrior", "warrior_sekushchiy_roscherk")
    state = start(content, warrior, count=2)
    for index in range(2):
        one = foe(state, index)
        state = state.replace_combatant(
            replace(one, effects=one.effects.apply(status_effect(StatusKind.UNSEEN, turns=3)))
        )
    after = act(
        content,
        {1: warrior},
        state,
        BattleAction(kind=ActionKind.ITEM, item_id="flash_grenade"),
        b"\x09" * 16,
    )
    for index in range(2):
        blinded = foe(after, index)
        assert StatusKind.UNSEEN not in held(blinded), "вспышка выдаёт всех"
        assert blinded.effects.modifiers().get("accuracy_percent", 0.0) < 0, "и слепит всех"
