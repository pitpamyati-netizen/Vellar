"""Все разновидности эффектов, какие движок умеет исполнять.

``skill_effects.EFFECT_SPECS`` параметризует движок, поэтому тесты идут по
разновидностям, а не по ста двадцати восьми умениям: урон, площадь, лечение,
щиты, усиления, помехи, снятие и особые.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, Equipment, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleOutcome,
    BattleState,
    Combatant,
    EventKind,
)
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.rules.combat import (
    act,
    hero_combatant,
    is_low_health,
    monster_combatant,
    open_battle,
    spend_dot,
)
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for


def enemy(name: str = "Волк", health: int = 4_000, damage: int = 5) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name=name,
        kind=EnemyKind.BEAST,
        level=5,
        max_health=health,
        damage=damage,
        armor=2,
        initiative=9.0,
        loot=(),
        gold=10,
    )


def caster(class_id: str, race_id: str, *skills: str) -> Character:
    actives: list[str | None] = [*skills] + [None] * (6 - len(skills))
    return Character(
        id=1,
        user_id=1,
        name="Тест",
        race_id=race_id,
        class_id=class_id,
        level=100,
        loadout=SkillLoadout(actives=tuple(actives)),
    )


def start(content: GameContent, character: Character, foes: tuple[Enemy, ...]) -> BattleState:
    """Герой под номером 1, противники со второго - так всюду в этих тестах."""
    fighters = [
        hero_combatant(content, character, combatant_id=1, side=0, live=True),
        *(monster_combatant(one, combatant_id=index + 2, side=1) for index, one in enumerate(foes)),
    ]
    return open_battle(content, {1: character}, fighters, b"effects-seed")


def hero(state: BattleState) -> Combatant:
    one = state.by_id(1)
    assert one is not None
    return one


def foe(state: BattleState, index: int = 0) -> Combatant:
    one = state.by_id(index + 2)
    assert one is not None
    return one


def foes(state: BattleState) -> tuple[Combatant, ...]:
    return tuple(one for one in state.combatants if not one.is_hero)


def tweak(state: BattleState, **changes: object) -> BattleState:
    """То же состояние, но герой в нём подправлен: раны, ресурс, эффекты."""
    return state.replace_combatant(replace(hero(state), **changes))  # type: ignore[arg-type]


def use(
    content: GameContent,
    character: Character,
    state: BattleState,
    slot: int = 0,
    seed: int = 1,
) -> BattleState:
    return act(
        content,
        {1: character},
        state,
        BattleAction(kind=ActionKind.SKILL, slot=slot),
        seed.to_bytes(16, "big"),
    )


def racial(
    content: GameContent, character: Character, state: BattleState, seed: bytes = b"\x01" * 16
) -> BattleState:
    return act(content, {1: character}, state, BattleAction(kind=ActionKind.RACIAL), seed)


def strike(
    content: GameContent, character: Character, state: BattleState, seed: bytes = b"\x05" * 16
) -> BattleState:
    return act(content, {1: character}, state, BattleAction(kind=ActionKind.ATTACK), seed)


# --- лечение и щиты ---------------------------------------------------


def test_healing_restores_health(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_dlan_zhizni")
    hurt = tweak(start(content, cleric, (enemy(),)), health=10)
    healed = use(content, cleric, hurt)
    assert hero(healed).health > 10
    assert any(event.kind is EventKind.HEAL for event in healed.events)


def test_healing_cannot_exceed_the_maximum(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_dlan_zhizni")
    healed = use(content, cleric, start(content, cleric, (enemy(),)))
    assert hero(healed).health <= hero(healed).max_health


def test_percentage_healing_scales_with_maximum_health(content: GameContent) -> None:
    human = caster("warrior", "human")
    human = replace(human, loadout=replace(human.loadout, racial="race_human_second_wind"))
    hurt = tweak(start(content, human, (enemy(),)), health=1)
    healed = racial(content, human, hurt)
    assert hero(healed).health > 1


def test_shields_absorb_damage_before_health(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_kamennaya_kozha")
    shielded = use(content, mage, start(content, mage, (enemy(damage=1),)))
    assert hero(shielded).barrier > 0 or hero(shielded).health < hero(shielded).max_health


def test_a_shield_is_consumed_before_health(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_kamennaya_kozha")
    with_shield = tweak(start(content, mage, (enemy(damage=10),)), barrier=1_000)
    hit, lost = hero(with_shield).damaged(50)
    assert lost == 0
    assert hit.barrier == 950
    assert hit.health == hero(with_shield).health


# --- площадь ----------------------------------------------------------


def test_area_damage_reaches_every_enemy(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_meteor")
    state = start(content, mage, (enemy("Первый"), enemy("Второй"), enemy("Третий")))
    for attempt in range(30):
        after = use(content, mage, state, seed=attempt)
        if all(target.health < target.max_health for target in foes(after)):
            return
    pytest.fail("an area skill never hit all three enemies")


def test_chain_damage_falls_off(content: GameContent) -> None:
    assert spec_for("damage_chain").chain_falloff > 0
    assert spec_for("damage_chain").aoe is True


# --- усиления и помехи ------------------------------------------------


def test_a_self_buff_lands_as_an_effect(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_klich_splocheniya")
    buffed = use(content, warrior, start(content, warrior, (enemy(),)))
    assert len(hero(buffed).effects) >= 1
    assert any(event.kind is EventKind.EFFECT_APPLIED for event in buffed.events)


def test_damage_reduction_buffs_store_a_negative_modifier(content: GameContent) -> None:
    """Сила 30 у ``buff_damage_taken`` значит «на 30 процентов *меньше* урона»."""
    paladin = caster("paladin", "human", "paladin_krepost_dukha")
    guarded = use(content, paladin, start(content, paladin, (enemy(),)))
    assert hero(guarded).effects.modifiers()["damage_taken_percent"] < 0


def test_a_debuff_lands_on_the_enemy_as_a_penalty(content: GameContent) -> None:
    rogue = caster("rogue", "goblin", "rogue_dymovaya_shashka")
    smoked = use(content, rogue, start(content, rogue, (enemy(),)))
    effects = foe(smoked).effects
    assert len(effects) == 1
    assert effects.modifiers()["accuracy_percent"] < 0
    assert next(iter(effects)).beneficial is False


def test_vulnerability_debuffs_are_positive(content: GameContent) -> None:
    ranger = caster("ranger", "wood_elf", "ranger_metka_okhotnika")
    marked = use(content, ranger, start(content, ranger, (enemy(),)))
    assert foe(marked).effects.modifiers()["damage_taken_percent"] > 0


# --- снятие и особые --------------------------------------------------


def test_cleansing_strips_penalties(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_ochishchenie")
    state = start(content, cleric, (enemy(),))
    cursed = tweak(
        state,
        effects=hero(state).effects.apply(
            ActiveEffect(
                id="curse",
                name="Проклятие",
                modifiers={"damage_taken_percent": 20.0},
                turns_left=5,
                beneficial=False,
            )
        ),
    )
    cleansed = use(content, cleric, cursed)
    assert hero(cleansed).effects.penalties() == ()
    assert any(event.kind is EventKind.CLEANSED for event in cleansed.events)


def test_free_cast_makes_the_next_skill_cost_nothing(content: GameContent) -> None:
    elf = caster("mage", "high_elf", "mage_ognennaya_strela")
    elf = replace(elf, loadout=replace(elf.loadout, racial="race_high_elf_mana_surge"))
    surged = racial(content, elf, start(content, elf, (enemy(),)), b"\x02" * 16)
    assert hero(surged).free_cast is True

    before = hero(surged).resource
    cast = use(content, elf, surged)
    assert hero(cast).resource >= before, "the free cast must not spend resource"


def test_cooldown_reset_clears_every_cooldown(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_kamennaya_kozha", "mage_po_pamyati")
    shielded = use(content, mage, start(content, mage, (enemy(),)), slot=0)
    assert hero(shielded).cooldowns
    reset = use(content, mage, shielded, slot=1)
    assert dict(hero(reset).cooldowns) == {}


def test_evade_charges_absorb_the_next_hit(content: GameContent) -> None:
    halfling = caster("rogue", "halfling")
    halfling = replace(
        halfling, loadout=replace(halfling.loadout, racial="race_halfling_nimbleness")
    )
    dodged = racial(content, halfling, start(content, halfling, (enemy(damage=500),)), b"\x03" * 16)
    assert any(event.kind is EventKind.DODGE for event in dodged.events)
    assert hero(dodged).health == hero(dodged).max_health


def _charmer() -> Character:
    half_elf = caster("paladin", "half_elf")
    return replace(half_elf, loadout=replace(half_elf.loadout, racial="race_half_elf_charm"))


def _parley(content: GameContent, character: Character, opponent: Enemy) -> set[BattleOutcome]:
    state = start(content, character, (opponent,))
    return {
        racial(content, character, state, index.to_bytes(16, "big")).outcome for index in range(40)
    }


def test_avoid_combat_can_end_the_fight_peacefully(content: GameContent) -> None:
    bandit = replace(enemy(name="Разбойник"), kind=EnemyKind.HUMANOID)
    assert BattleOutcome.AVOIDED in _parley(content, _charmer(), bandit)


def test_avoid_combat_needs_someone_who_can_be_reasoned_with(content: GameContent) -> None:
    """«Шанс закончить бой с разумным противником миром» - с разумным.

    С волком договориться нельзя, и умение это говорит текстом, который игрок
    читает до нажатия.
    """
    assert _parley(content, _charmer(), enemy()) == {BattleOutcome.ONGOING}


def test_stuns_make_an_enemy_skip_a_turn(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_udar_shchitom")
    state = start(content, warrior, (enemy(),))
    for attempt in range(40):
        bashed = use(content, warrior, state, seed=attempt)
        skipped = [
            event
            for event in bashed.events
            if event.kind is EventKind.TURN_SKIPPED and event.actor_id != 1
        ]
        if skipped:
            assert skipped[0].effect_name == "Оглушение"
            return
    pytest.fail("удар щитом ни разу не оглушил за 40 семян")


# --- содержание и вспомогательное -------------------------------------


def test_regeneration_traits_heal_at_end_of_turn(content: GameContent) -> None:
    troll = caster("warrior", "troll", "warrior_rassechenie")
    troll = replace(troll, trait_ids=("steady_breath",))
    hurt = tweak(start(content, troll, (enemy(damage=1),)), health=100)
    later = strike(content, troll, hurt)
    assert hero(later).health >= 100


def test_low_health_helper(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_rassechenie")
    state = start(content, warrior, (enemy(),))
    assert is_low_health(state, 1) is False
    assert is_low_health(tweak(state, health=1), 1) is True


@pytest.mark.parametrize(
    "category",
    [
        EffectCategory.DAMAGE,
        EffectCategory.HEAL,
        EffectCategory.BARRIER,
        EffectCategory.BUFF,
        EffectCategory.DEBUFF,
        EffectCategory.CLEANSE,
        EffectCategory.SPECIAL,
    ],
)
def test_every_category_is_used_by_content(content: GameContent, category: EffectCategory) -> None:
    """Каждая разновидность должна встречаться, иначе в движке мёртвая ветка."""
    from mmorpg.domain.entities.content import SkillKind

    used = {
        spec_for(skill.effect).category
        for skill in content.skills
        if skill.kind is SkillKind.ACTIVE
    }
    assert category in used


# --- грани: то, что они обещают, и происходит --------------------------


def with_edge(character: Character, code: str, edge_code: str) -> Character:
    """Тот же персонаж, но с выбранной гранью и третьим рангом умения."""
    loadout = replace(
        character.loadout,
        ranks={**character.loadout.ranks, code: 3},
        edges={**character.loadout.edges, code: edge_code},
    )
    return replace(character, loadout=loadout)


def test_an_edge_that_promises_a_second_target_hits_a_second_target(
    content: GameContent,
) -> None:
    """«Размах» у рассечения: удар по одной цели задевает соседа."""
    skill = content.skill("warrior_rassechenie")
    splashing = skill.edges[1]
    assert splashing.name == "Размах"
    character = caster("warrior", "human", skill.code)
    character = replace(character, loadout=replace(character.loadout, ranks={skill.code: 3}))
    pair = (enemy("Волк"), enemy("Волчица"))
    edged = with_edge(character, skill.code, splashing.code)

    plain = use(content, character, start(content, character, pair))
    wide = use(content, edged, start(content, edged, pair))

    # Второй противник цел без грани и ранен с гранью.
    assert foe(plain, 1).health == foe(plain, 1).max_health
    assert foe(wide, 1).health < foe(wide, 1).max_health


def test_an_edge_that_promises_bleeding_leaves_the_target_bleeding(
    content: GameContent,
) -> None:
    # Умение, которое само по себе крови не пускает: кровь на цели - работа грани.
    skill = content.skill("warrior_sekushchiy_roscherk")
    bleeding = skill.edges[1]
    assert bleeding.name == "Кровопускание"
    character = caster("warrior", "human", skill.code)
    character = replace(character, loadout=replace(character.loadout, ranks={skill.code: 3}))
    pack = (enemy(),)
    edged = with_edge(character, skill.code, bleeding.code)

    plain = use(content, character, start(content, character, pack))
    cutting = use(content, edged, start(content, edged, pack))

    assert not foe(plain).effects.has(StatusKind.BLEEDING)
    assert foe(cutting).effects.has(StatusKind.BLEEDING)


def test_an_edge_that_promises_a_discount_is_a_discount(content: GameContent) -> None:
    """«Экономный шар»: тот же шар за меньшие чары."""
    skill = content.skill("mage_meteor")
    cheaper = skill.edges[1]
    character = caster("mage", "human", skill.code)
    character = replace(character, loadout=replace(character.loadout, ranks={skill.code: 3}))
    pack = (enemy(),)
    edged = with_edge(character, skill.code, cheaper.code)

    plain = use(content, character, start(content, character, pack))
    thrifty = use(content, edged, start(content, edged, pack))

    assert hero(thrifty).resource > hero(plain).resource


def test_a_passive_edge_is_not_just_a_label(content: GameContent) -> None:
    """Половина выбранных граней в игре - у пассивных умений."""
    from mmorpg.domain.rules import modifiers as mods

    skill = content.skill("warrior_stoykost")
    character = caster("warrior", "human")
    character = replace(character, loadout=replace(character.loadout, ranks={skill.code: 3}))

    plain = mods.passive_modifiers(content, character)
    edged = mods.passive_modifiers(content, with_edge(character, skill.code, skill.edges[0].code))

    assert edged != plain


def test_bleeding_actually_takes_health_every_turn(content: GameContent) -> None:
    """«и ещё 3 хода» долго было надписью: ``dot_turns`` не читал никто."""
    skill = content.skill("rogue_otravlennyy_klinok")
    character = caster("rogue", "human", skill.code)
    # Отравленный клинок просит клинок: без кинжала умение не сработает вовсе.
    character = replace(
        character,
        loadout=replace(character.loadout, ranks={skill.code: 1}),
        equipment=character.equipment.equip("weapon", "dagger@5#uncommon"),
    )

    after = use(content, character, start(content, character, (enemy(),)))
    poisoned = foe(after)
    assert poisoned.effects.penalties() != ()

    bleeding = spend_dot(after, poisoned.id)
    assert foe(bleeding).health < poisoned.health


# --- промах не оставляет ничего ---------------------------------------


def _turns_until_a_miss(
    content: GameContent, character: Character, state: BattleState, tries: int = 400
) -> tuple[BattleState, BattleState]:
    """Крутит сиды, пока умение не промахнётся. Возвращает промах и попадание."""
    missed: BattleState | None = None
    landed: BattleState | None = None
    for seed in range(1, tries):
        after = use(content, character, state, seed=seed)
        if any(event.kind is EventKind.MISS for event in after.events):
            missed = missed or after
        elif any(event.kind is EventKind.DAMAGE and event.actor_id == 1 for event in after.events):
            landed = landed or after
        if missed is not None and landed is not None:
            break
    assert missed is not None, "ни один сид не дал промаха"
    assert landed is not None, "ни один сид не дал попадания"
    return missed, landed


def test_a_missed_blow_leaves_no_debuff(content: GameContent) -> None:
    """«Промах» и «наложен эффект» в одном ходу - это один и тот же удар."""
    rogue = caster("rogue", "human", "rogue_fint")
    rogue = replace(rogue, loadout=replace(rogue.loadout, ranks={"rogue_fint": 1}))
    state = start(content, rogue, (enemy(),))

    missed, landed = _turns_until_a_miss(content, rogue, state)

    assert foe(missed).effects.penalties() == ()
    assert not any(event.kind is EventKind.EFFECT_APPLIED for event in missed.events)
    assert foe(landed).effects.penalties() != ()


def test_a_missed_blow_draws_no_blood(content: GameContent) -> None:
    """Кровотечение - тоже след удара: не попал, значит нечему течь."""
    rogue = caster("rogue", "human", "rogue_otravlennyy_klinok")
    rogue = replace(
        rogue,
        loadout=replace(rogue.loadout, ranks={"rogue_otravlennyy_klinok": 1}),
        equipment=Equipment().equip("weapon", "dagger@5#uncommon"),
    )
    state = start(content, rogue, (enemy(),))

    missed, landed = _turns_until_a_miss(content, rogue, state)

    assert foe(missed).effects.penalties() == ()
    assert spend_dot(missed, 2).by_id(2) == missed.by_id(2)
    assert foe(landed).effects.penalties() != ()


# --- то, что раньше стояло в тексте и не происходило -------------------


def test_healing_over_time_arrives_every_turn(content: GameContent) -> None:
    """«Лечит вас каждый ход 3 хода» - каждый ход, а не один раз и тишина."""
    druid = caster("druid", "human", "druid_dykhanie_roshchi")
    state = start(content, druid, (enemy(damage=0),))
    hurt = tweak(state, health=hero(state).max_health // 4)
    started = hero(hurt).health
    first = use(content, druid, hurt)
    second = strike(content, druid, first, b"\x02" * 16)
    # Каждый ход приносит свой кусок: первый ход уже вылечил, второй лечит ещё.
    assert hero(first).health > started
    assert hero(second).health > hero(first).health


def test_a_shield_burns_out_with_its_skill(content: GameContent) -> None:
    """«Поглощает урон 3 хода» - и на четвёртом его нет."""
    mage = caster("mage", "high_elf", "mage_kamennaya_kozha")
    working = use(content, mage, start(content, mage, (enemy(damage=0),)))
    assert hero(working).barrier > 0
    for turn in range(3):
        working = strike(content, mage, working, bytes([turn + 3]) * 16)
    assert hero(working).barrier == 0


def test_a_riposte_answers_the_blow_it_took(content: GameContent) -> None:
    """«3 хода вы отвечаете на каждый удар по вам» - раньше не отвечали ничем."""
    warrior = caster("warrior", "human", "warrior_otvetnyy_vypad")
    state = start(content, warrior, (enemy(damage=40),))
    for seed in range(1, 60):
        answered = use(content, warrior, state, seed=seed)
        landed = any(
            event.kind in {EventKind.DAMAGE, EventKind.CRIT} and event.target_id == 1
            for event in answered.events
        )
        if not landed:
            continue  # удар по воину не дошёл - отвечать нечем
        assert any(
            event.kind in {EventKind.DAMAGE, EventKind.CRIT} and event.target == "Волк"
            for event in answered.events
        )
        return
    pytest.fail("ни на одном семени удар по воину не дошёл")


def test_undying_keeps_the_last_stand_standing(content: GameContent) -> None:
    """«Не даёт вам пасть 3 хода» - и не даёт."""
    warrior = caster("warrior", "human", "warrior_posledniy_rubezh")
    doomed = tweak(start(content, warrior, (enemy(damage=100_000),)), health=1)
    stood = use(content, warrior, doomed)
    assert hero(stood).alive
    assert stood.winner != 1


def test_a_slowed_enemy_loses_its_place_in_the_queue(content: GameContent) -> None:
    """Потеря инициативы - это потерянная очередь, а не строка на экране.

    Инициатива в новом движке решает ровно одно и делает это буквально: кто
    быстрее, тот бьёт раньше (ADR 0021). Снятые проценты двигают бойца назад.
    """
    ranger = caster("ranger", "human", "ranger_lovushka")
    swift = replace(enemy(damage=10), initiative=10_000.0)
    state = start(content, ranger, (swift,))
    assert state.order[0] == 2, "быстрый противник ходит первым"

    for seed in range(40):
        after = use(content, ranger, state, seed=seed)
        snared = after.by_id(2)
        if snared is None or not snared.effects.modifiers().get("initiative_percent", 0.0):
            continue
        assert snared.effects.modifiers()["initiative_percent"] < 0
        return
    pytest.fail("the snare never landed across 40 seeds")


def test_a_beast_hunter_hits_beasts_harder(content: GameContent) -> None:
    """Прибавка, которая смотрит на породу цели, наконец её видит."""
    from mmorpg.domain.rules.combat import situational_damage

    beast = monster_combatant(enemy(), combatant_id=2, side=1)
    plain = situational_damage(
        {},
        spec=None,
        magic=False,
        target=beast,
        target_health_ratio=1.0,
        attacker_health_ratio=1.0,
        round_number=2,
    )
    hunter = situational_damage(
        {"beast_damage_percent": 20.0},
        spec=None,
        magic=False,
        target=beast,
        target_health_ratio=1.0,
        attacker_health_ratio=1.0,
        round_number=2,
    )
    assert plain == pytest.approx(1.0)
    assert hunter == pytest.approx(1.2)


def test_a_magic_blow_and_a_physical_one_are_told_apart(content: GameContent) -> None:
    from mmorpg.domain.rules.combat import situational_damage

    beast = monster_combatant(enemy(), combatant_id=2, side=1)
    bundle = {"magic_damage_percent": 30.0, "physical_damage_percent": 10.0}
    kwargs = {
        "target": beast,
        "target_health_ratio": 1.0,
        "attacker_health_ratio": 1.0,
        "round_number": 2,
    }
    fiery = situational_damage(bundle, spec=spec_for("damage_fire"), magic=True, **kwargs)
    iron = situational_damage(bundle, spec=spec_for("damage"), magic=False, **kwargs)
    assert fiery == pytest.approx(1.3)
    assert iron == pytest.approx(1.1)


def test_a_hero_counts_as_a_humanoid_for_the_bonuses(content: GameContent) -> None:
    """В поединке двоих прибавка «по гуманоидам» считается так же, как в поле."""
    from mmorpg.domain.rules.combat import situational_damage

    warrior = caster("warrior", "human")
    target = hero_combatant(content, warrior, combatant_id=2, side=1, live=True)
    factor = situational_damage(
        {"humanoid_damage_percent": 25.0},
        spec=None,
        magic=False,
        target=target,
        target_health_ratio=1.0,
        attacker_health_ratio=1.0,
        round_number=2,
    )
    assert factor == pytest.approx(1.25)


def test_stolen_gold_is_a_share_of_what_the_target_carries(content: GameContent) -> None:
    """Кража - доля кошелька обворованного, а не написанное число (ADR 0007)."""
    goblin = caster("rogue", "goblin")
    goblin = replace(goblin, loadout=replace(goblin.loadout, racial="race_goblin_dirty_trick"))
    rich = start(content, goblin, (replace(enemy(), gold=1_000),))
    poor = start(content, goblin, (replace(enemy(), gold=10),))
    taken = racial(content, goblin, rich, b"\x05" * 16)
    scraps = racial(content, goblin, poor, b"\x05" * 16)
    assert taken.gold > scraps.gold


# --- стихия чужого удара ---------------------------------------------


def test_resistance_is_counted_by_the_kind_of_damage_the_blow_carries(
    content: GameContent,
) -> None:
    """Сопротивление считается по роду урона и по его половине разом."""
    from mmorpg.domain.entities.damage import DamageType
    from mmorpg.domain.rules.combat import incoming_damage_factor

    warm = {"resist_cold_percent": 40.0}
    assert incoming_damage_factor(warm, DamageType.COLD) == pytest.approx(0.6)
    assert incoming_damage_factor(warm, DamageType.FIRE) == pytest.approx(1.0)

    # Латы держат удар вообще, стёганка под ними - именно колющий: складываются.
    plated = {"resist_physical_percent": 20.0, "resist_piercing_percent": 15.0}
    assert incoming_damage_factor(plated, DamageType.PIERCING) == pytest.approx(0.65)
    assert incoming_damage_factor(plated, DamageType.SLASHING) == pytest.approx(0.8)
    assert incoming_damage_factor(plated, DamageType.FIRE) == pytest.approx(1.0)


def test_a_skill_carries_its_own_kind_of_damage(content: GameContent) -> None:
    """Умение бьёт своим родом урона, чем бы ни был вооружён его хозяин."""
    from mmorpg.domain.entities.damage import DamageType
    from mmorpg.domain.rules.combat import damage_type_of

    warrior = caster("warrior", "human")
    striker = hero_combatant(content, warrior, combatant_id=1, side=0, live=True)
    # Без умения бьёт то, что в руках; голыми руками - дробящий.
    assert damage_type_of(striker, None) is DamageType.BLUDGEONING
    assert damage_type_of(striker, spec_for("damage_fire")) is DamageType.FIRE
    assert damage_type_of(striker, spec_for("damage_piercing")) is DamageType.PIERCING
    assert damage_type_of(striker, spec_for("damage_aoe_slow")) is DamageType.COLD


def test_an_archetype_that_names_no_damage_kind_strikes_by_its_breed(
    content: GameContent,
) -> None:
    from mmorpg.domain.procgen.enemies import element_of as archetype_element

    by_id = {archetype.id: archetype for archetype in content.enemy_archetypes}
    # Зверь рвёт, тварь бьёт по разуму, стихия огня - огнём.
    assert str(archetype_element(by_id["grey_wolf"])) == "rending"
    assert str(archetype_element(by_id["void_spawn"])) == "mental"
    assert str(archetype_element(by_id["fire_elemental"])) == "fire"
    # Объявленное содержимым сильнее породы: истукан бьёт камнем, а не чарами.
    assert str(archetype_element(by_id["stone_golem"])) == "bludgeoning"


def test_a_blow_aimed_at_a_body_already_down_does_not_raise(content: GameContent) -> None:
    """Удар, у которого не нашлось названной цели, находит живую и не падает."""
    rogue = caster("rogue", "human", "rogue_otravlennyy_klinok")
    rogue = replace(
        rogue,
        loadout=replace(rogue.loadout, ranks={"rogue_otravlennyy_klinok": 1}),
        equipment=Equipment().equip("weapon", "dagger@5#uncommon"),
    )
    state = start(content, rogue, (enemy(), enemy(name="Второй")))
    # Первый уже лежит, но бой не окончен: цель нажатия - именно он.
    state = state.replace_combatant(replace(foe(state), health=0))
    after = act(
        content,
        {1: rogue},
        state,
        BattleAction(kind=ActionKind.SKILL, slot=0, target=2),
        b"aimed-at-a-corpse",
    )
    assert not after.is_over
    assert (after.round, after.cursor) != (state.round, state.cursor)
