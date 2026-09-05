"""Как ведёт себя движок боя и что он обещает содержимому."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillKind, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleOutcome,
    BattleState,
    EventKind,
    Verdict,
)
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle
from mmorpg.domain.rules.skill_effects import DAMAGE_TAGS, known_effects, spec_for

SEED = b"combat-seed-0001"


def make_enemy(level: int = 3, health: int = 60, damage: int = 8, name: str = "Волк") -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=level,
        max_health=health,
        damage=damage,
        armor=2,
        initiative=9.0,
        loot=("wolf_pelt",),
        gold=12,
    )


@pytest.fixture
def fighter(warrior: Character) -> Character:
    return replace(
        warrior,
        level=10,
        loadout=SkillLoadout(
            actives=(
                "warrior_rassechenie",
                "warrior_provokatsiya",
                "warrior_udar_shchitom",
                None,
                None,
                None,
            ),
            racial="race_human_second_wind",
            # Постоянное умение слота не занимает: изучено - значит работает.
            ranks={"warrior_stoykost": 1},
        ),
    )


def seed_for(turn: int) -> bytes:
    """Своё семя на попытку - так их выдаёт слой приложения."""
    return turn.to_bytes(16, "big")


def start(
    content: GameContent,
    character: Character,
    *enemies: Enemy,
    seed: bytes = SEED,
) -> tuple[BattleState, dict[int, Character]]:
    """Один герой против стаи. Номер героя всегда 1, противники со второго."""
    roster = {1: character}
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(
            monster_combatant(enemy, combatant_id=index + 2, side=1)
            for index, enemy in enumerate(enemies)
        ),
    ]
    return open_battle(content, roster, fighters, seed), roster


def attack(
    content: GameContent,
    roster: dict[int, Character],
    state: BattleState,
    seed: bytes = SEED,
) -> BattleState:
    return act(content, roster, state, BattleAction(kind=ActionKind.ATTACK), seed)


def foe(state: BattleState, index: int = 0) -> object:
    return [one for one in state.combatants if not one.is_hero][index]


def act_until_hit(
    content: GameContent,
    roster: dict[int, Character],
    state: BattleState,
    action: BattleAction,
) -> BattleState:
    """Крутить семена, пока удар не дойдёт: промах - это тоже состоявшийся ход."""
    for attempt in range(50):
        after = act(content, roster, state, action, seed_for(attempt))
        if any(
            event.kind in {EventKind.DAMAGE, EventKind.CRIT}
            for event in after.events
            if event.actor_id == 1
        ):
            return after
    raise AssertionError("no seed produced a landed hit")


# --- договор с содержимым --------------------------------------------


def test_every_content_effect_has_an_implementation(content: GameContent) -> None:
    """Содержимое волно добавлять умения, но не поведение, которого нет в движке."""
    used = {skill.effect for skill in content.skills if skill.kind is SkillKind.ACTIVE}
    missing = sorted(used - known_effects())
    assert not missing, f"effects declared in content but not implemented: {missing}"


def test_every_implemented_effect_is_used_by_content(content: GameContent) -> None:
    """И в обратную сторону: мёртвых спецификаций в движке не держат.

    Кроме одной семьи: удар каждым родом урона объявлен целиком, все шестнадцать
    родов и по цели, и по всем (``skill_effects.DAMAGE_TAGS``). Это не шестьдесят
    четыре разных поведения, а одно, у которого объявлен род, - ровно так же, как
    у снаряжения объявлен вид, а вещи собираются из него (ADR 0015). Содержимое
    берёт из семьи то, что ему нужно, и незанятое не мёртвое: оно ждёт умения.
    """
    used = {skill.effect for skill in content.skills if skill.kind is SkillKind.ACTIVE}
    family = (
        {"damage"}
        | {f"damage_{tag}" for tag in DAMAGE_TAGS}
        | {f"damage_aoe_{tag}" for tag in DAMAGE_TAGS}
    )
    assert not sorted(known_effects() - used - family)


def test_spec_lookup_fails_loudly() -> None:
    with pytest.raises(KeyError, match="no implementation"):
        spec_for("no_such_effect")


# --- начало боя ------------------------------------------------------


def test_combat_starts_at_full_health(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy())
    hero = state.by_id(1)
    assert hero is not None
    assert state.round == 1
    assert state.outcome is BattleOutcome.ONGOING
    assert hero.health == hero.max_health
    assert hero.resource == hero.max_resource
    assert len(state.living(1)) == 1


def test_group_fights_are_supported(content: GameContent, fighter: Character) -> None:
    state, _ = start(content, fighter, make_enemy(), make_enemy(name="Волчица"))
    assert len(state.living(1)) == 2
    assert {one.id for one in state.combatants} == {1, 2, 3}


def test_the_queue_is_ordered_by_initiative(content: GameContent, fighter: Character) -> None:
    """Инициатива - это очередь удара, и больше она ничего не делает (ADR 0021)."""
    swift = hero_combatant(content, fighter, combatant_id=1, side=0, live=True)
    slow = monster_combatant(make_enemy(health=5_000), combatant_id=2, side=1)
    quick = monster_combatant(
        replace(make_enemy(health=5_000, name="Быстрый"), initiative=999.0),
        combatant_id=3,
        side=1,
    )
    state = open_battle(content, {1: fighter}, [swift, slow, quick], SEED)
    # Быстрый противник успевает ударить до того, как очередь дойдёт до игрока.
    assert state.order[0] == 3
    assert any(event.actor_id == 3 for event in state.events)


def test_the_turn_waits_for_the_live_player(content: GameContent, fighter: Character) -> None:
    """За кого ходит движок - ходит сразу; живого бой ждёт, сколько нужно."""
    state, _ = start(content, fighter, make_enemy(health=5_000))
    awaiting = state.awaiting
    assert awaiting is not None
    assert awaiting.id == 1


# --- воспроизводимость -----------------------------------------------


def test_a_turn_is_reproducible(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy())
    assert attack(content, roster, state) == attack(content, roster, state)


def test_a_different_seed_can_change_the_roll(content: GameContent, fighter: Character) -> None:
    """Против того, кто много выше уровнем, бросок снова что-то решает."""
    state, roster = start(content, fighter, make_enemy(level=fighter.level + 15, health=400))
    results = {
        attack(content, roster, state, seed=seed_for(index)).by_id(2).health  # type: ignore[union-attr]
        for index in range(40)
    }
    assert len(results) > 1


# --- обычный ход -----------------------------------------------------


def test_attack_damages_the_enemy(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=500))
    after = act_until_hit(content, roster, state, BattleAction(kind=ActionKind.ATTACK))
    target = after.by_id(2)
    assert target is not None
    assert target.health < 500


def test_attacks_can_miss(content: GameContent, fighter: Character) -> None:
    """Точность что-то значит: на многих семенах какие-то удары не доходят."""
    state, roster = start(content, fighter, make_enemy(health=5_000))
    kinds = {
        event.kind
        for attempt in range(60)
        for event in attack(content, roster, state, seed_for(attempt)).events
    }
    assert EventKind.MISS in kinds
    assert EventKind.DAMAGE in kinds


def test_enemies_answer_in_the_same_round(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=500, damage=40))
    after = attack(content, roster, state)
    hero = after.by_id(1)
    assert hero is not None
    assert hero.health < hero.max_health or any(
        event.kind is EventKind.DODGE for event in after.events
    )


def test_defeating_the_last_enemy_ends_the_fight(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=1))
    for turn in range(12):
        state = attack(content, roster, state, seed_for(turn))
        if state.is_over:
            break
    assert state.verdict_for(1) is Verdict.VICTORY
    assert state.experience > 0
    assert state.gold > 0
    assert "wolf_pelt" in state.loot


def test_losing_ends_the_fight(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(level=200, health=90_000, damage=9_000))
    for turn in range(30):
        state = attack(content, roster, state, seed_for(turn))
        if state.is_over:
            break
    assert state.verdict_for(1) is Verdict.DEFEAT
    hero = state.by_id(1)
    assert hero is not None
    assert hero.health == 0


def test_a_finished_fight_ignores_further_turns(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=1))
    turn = 0
    while not state.is_over:
        state = attack(content, roster, state, seed_for(turn))
        turn += 1
    frozen = attack(content, roster, state)
    assert frozen.round == state.round
    assert frozen.outcome is state.outcome


# --- умения -----------------------------------------------------------


def test_using_a_skill_spends_the_resource(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=500))
    before = state.by_id(1)
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0), SEED).by_id(1)
    assert before is not None and after is not None
    assert after.resource < before.resource


def test_an_empty_slot_answers_instead_of_failing(content: GameContent, fighter: Character) -> None:
    """Пустой слот отвечает фразой, а не падением, - и хода не тратит."""
    state, roster = start(content, fighter, make_enemy())
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=4), SEED)
    assert any(event.kind is EventKind.EMPTY_SLOT for event in after.events)
    assert after.outcome is BattleOutcome.ONGOING
    assert (after.round, after.cursor) == (state.round, state.cursor)


def test_a_skill_on_cooldown_is_refused_politely(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=5_000))
    state = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=2), SEED)
    hero = state.by_id(1)
    assert hero is not None and hero.cooldown_of("warrior_udar_shchitom") > 0
    blocked = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=2), SEED)
    assert any(event.kind is EventKind.ON_COOLDOWN for event in blocked.events)


def test_cooldowns_tick_down_and_expire(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=5_000))
    state = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=2), SEED)
    hero = state.by_id(1)
    assert hero is not None
    remaining = hero.cooldown_of("warrior_udar_shchitom")
    assert remaining == 3
    for _ in range(remaining):
        state = attack(content, roster, state)
    hero = state.by_id(1)
    assert hero is not None
    assert hero.cooldown_of("warrior_udar_shchitom") == 0


def test_a_rank_returns_the_skill_earlier_and_cheaper(
    content: GameContent, fighter: Character
) -> None:
    """Ранг обязан менять умение, а не только его силу (ADR 0067).

    Прежде очко, вложенное в ранг, прибавляло пятнадцатую долю урона и больше
    ничего. Теперь предельный ранг возвращает умение на два хода раньше и стоит
    вполовину дешевле - и то, и другое слышно в первом же бою.
    """
    plain, _ = start(content, fighter, make_enemy(health=5_000))
    plain = act(content, {1: fighter}, plain, BattleAction(kind=ActionKind.SKILL, slot=2), SEED)
    novice = plain.by_id(1)
    assert novice is not None

    top = content.rules.max_rank
    master = replace(fighter, loadout=fighter.loadout.with_rank("warrior_udar_shchitom", top))
    state, roster = start(content, master, make_enemy(health=5_000))
    state = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=2), SEED)
    hero = state.by_id(1)
    assert hero is not None
    assert hero.cooldown_of("warrior_udar_shchitom") == (
        novice.cooldown_of("warrior_udar_shchitom") - skill_rules.rank_gain(top).cooldown_cut
    )
    # И запас списан меньший: скидка ранга - такая же объявленная механика.
    assert hero.resource > novice.resource


def test_not_enough_resource_is_refused_politely(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=5_000))
    hero = state.by_id(1)
    assert hero is not None
    state = state.replace_combatant(replace(hero, resource=0))
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert any(event.kind is EventKind.NOT_ENOUGH_RESOURCE for event in after.events)


def test_rank_makes_a_skill_stronger(content: GameContent, fighter: Character) -> None:
    ranked = replace(fighter, loadout=fighter.loadout.with_rank("warrior_rassechenie", 5))
    cleave = BattleAction(kind=ActionKind.SKILL, slot=0)
    seed = seed_for(1)
    plain, plain_roster = start(content, fighter, make_enemy(health=5_000), seed=seed)
    strong_state, strong_roster = start(content, ranked, make_enemy(health=5_000), seed=seed)
    weak = act(content, plain_roster, plain, cleave, seed).by_id(2)
    strong = act(content, strong_roster, strong_state, cleave, seed).by_id(2)
    assert weak is not None and strong is not None
    assert weak.health < 5_000, "the reference hit must land"
    assert strong.health < weak.health


def test_area_skills_hit_every_enemy(content: GameContent, fighter: Character) -> None:
    aoe = replace(
        fighter,
        level=20,
        # Вихрь клинков просит клинок: умение с требованием к оружию без оружия
        # не срабатывает, и это проверяет отдельный тест.
        equipment=fighter.equipment.equip("weapon", "sword@1#common"),
        loadout=replace(
            fighter.loadout, actives=("warrior_vikhr_klinkov", None, None, None, None, None)
        ),
    )
    state, roster = start(
        content, aoe, make_enemy(health=900), make_enemy(health=900, name="Волчица")
    )
    whirl = BattleAction(kind=ActionKind.SKILL, slot=0)
    for attempt in range(50):
        after = act(content, roster, state, whirl, seed_for(attempt))
        struck = [one for one in after.combatants if not one.is_hero]
        if all(one.health < 900 for one in struck):
            return
    raise AssertionError("an area skill should be able to hit both enemies")


def test_racial_skill_uses_the_separate_slot(content: GameContent, fighter: Character) -> None:
    """Расовое активное умение никогда не соперничает с шестью слотами класса."""
    state, roster = start(content, fighter, make_enemy(health=900, damage=30))
    hero = state.by_id(1)
    assert hero is not None
    state = state.replace_combatant(replace(hero, health=hero.max_health // 2))
    after = act(content, roster, state, BattleAction(kind=ActionKind.RACIAL), SEED)
    assert any(event.kind is EventKind.HEAL for event in after.events)


def test_a_taunt_lands_on_the_enemy_and_braces_the_taunter(
    content: GameContent, fighter: Character
) -> None:
    """Провокация вешает вызов на цель и прикрывает провокатора броней (ADR 0027)."""
    state, roster = start(content, fighter, make_enemy(health=900))
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=1), SEED)

    target = after.by_id(2)
    assert target is not None
    assert target.effects.has(StatusKind.TAUNT)
    # Величина вызова - номер провокатора: по нему движок ведёт удар цели.
    assert round(target.effects.magnitude_of(StatusKind.TAUNT)) == 1

    taunter = after.by_id(1)
    assert taunter is not None
    assert taunter.effects.modifiers()["armor_percent"] > 0


# --- цель -------------------------------------------------------------


def test_choosing_a_target_costs_no_turn(content: GameContent, fighter: Character) -> None:
    """Смена цели ходом не считается: ничего не произошло (правило 3)."""
    state, roster = start(
        content, fighter, make_enemy(health=900), make_enemy(health=900, name="Волчица")
    )
    after = act(content, roster, state, BattleAction(kind=ActionKind.FOCUS, target=3), SEED)
    hero = after.by_id(1)
    assert hero is not None and hero.focus == 3
    assert (after.round, after.cursor) == (state.round, state.cursor)
    assert not after.events


def test_the_blow_lands_on_the_chosen_target(content: GameContent, fighter: Character) -> None:
    state, roster = start(
        content, fighter, make_enemy(health=900), make_enemy(health=900, name="Волчица")
    )
    aimed = act(content, roster, state, BattleAction(kind=ActionKind.FOCUS, target=3), SEED)
    struck = act_until_hit(content, roster, aimed, BattleAction(kind=ActionKind.ATTACK))
    hit = [event for event in struck.events if event.actor_id == 1 and event.amount]
    assert hit and all(event.target_id == 3 for event in hit)


# --- расходники и бегство ---------------------------------------------


def test_potions_heal_without_taking_a_skill_slot(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=900))
    hero = state.by_id(1)
    assert hero is not None
    state = state.replace_combatant(replace(hero, health=10))
    after = act(
        content,
        roster,
        state,
        BattleAction(kind=ActionKind.ITEM, item_id="small_healing_potion"),
        SEED,
    )
    healed = after.by_id(1)
    assert healed is not None and healed.health > 10


def test_fleeing_can_end_the_fight(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=900))
    outcomes = {
        act(content, roster, state, BattleAction(kind=ActionKind.FLEE), bytes([index] * 16)).outcome
        for index in range(30)
    }
    assert BattleOutcome.FLED in outcomes
    assert BattleOutcome.ONGOING in outcomes


def test_yielding_hands_the_field_over(content: GameContent, fighter: Character) -> None:
    """Сдача - единственная дверь из боя, который бросили с той стороны."""
    state, roster = start(content, fighter, make_enemy(health=9_000))
    after = act(content, roster, state, BattleAction(kind=ActionKind.YIELD), SEED)
    assert after.is_over
    assert after.verdict_for(1) is Verdict.FLED
    assert any(event.kind is EventKind.YIELDED for event in after.events)


# --- содержание -------------------------------------------------------


def test_resource_regenerates_each_turn(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=5_000))
    hero = state.by_id(1)
    assert hero is not None
    drained = state.replace_combatant(replace(hero, resource=0))
    after = attack(content, roster, drained).by_id(1)
    assert after is not None and after.resource > 0


def test_effects_expire_over_time(content: GameContent, fighter: Character) -> None:
    state, roster = start(content, fighter, make_enemy(health=9_000))
    state = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=1), SEED)
    target = state.by_id(2)
    assert target is not None and len(target.effects) == 1
    for _ in range(3):
        state = attack(content, roster, state)
    target = state.by_id(2)
    assert target is not None and len(target.effects) == 0


def test_combat_never_uses_a_wall_clock(content: GameContent, fighter: Character) -> None:
    """Правило 13: бой ждёт игрока столько, сколько тому нужно."""
    import inspect

    from mmorpg.domain.rules import combat

    source = inspect.getsource(combat)
    for forbidden in ("time.time", "datetime", "sleep", "timeout"):
        assert forbidden not in source
