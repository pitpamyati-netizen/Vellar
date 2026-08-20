"""Every effect category the engine can execute.

``skill_effects.EFFECT_SPECS`` parametrises the engine, so these tests walk the
categories rather than the 128 individual skills: damage, area damage, healing,
shields, buffs, debuffs, cleansing and the specials.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    CombatAction,
    CombatOutcome,
    CombatState,
    EventKind,
)
from mmorpg.domain.entities.effects import ActiveEffect
from mmorpg.domain.entities.location import Enemy, EnemyKind
from mmorpg.domain.rules.combat import (
    is_low_health,
    resolve_turn,
    spend_bleeding,
    start_combat,
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


def use(
    content: GameContent, character: Character, state: CombatState, slot: int = 0, seed: int = 1
) -> CombatState:
    return resolve_turn(
        content,
        character,
        state,
        CombatAction(kind=ActionKind.SKILL, slot=slot),
        seed.to_bytes(16, "big"),
    )


# --- healing and shields ---------------------------------------------


def test_healing_restores_health(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_mend")
    state = start_combat(content, cleric, (enemy(),))
    hurt = replace(state, player=replace(state.player, health=10))
    healed = use(content, cleric, hurt)
    assert healed.player.health > 10
    assert any(event.kind is EventKind.HEAL for event in healed.events)


def test_healing_cannot_exceed_the_maximum(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_mend")
    state = start_combat(content, cleric, (enemy(),))
    healed = use(content, cleric, state)
    assert healed.player.health <= healed.player.max_health


def test_percentage_healing_scales_with_maximum_health(content: GameContent) -> None:
    human = caster("warrior", "human")
    human = replace(human, loadout=replace(human.loadout, racial="race_human_second_wind"))
    state = start_combat(content, human, (enemy(),))
    hurt = replace(state, player=replace(state.player, health=1))
    healed = resolve_turn(content, human, hurt, CombatAction(kind=ActionKind.RACIAL), b"\x01" * 16)
    assert healed.player.health > 1


def test_shields_absorb_damage_before_health(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_arcane_shield")
    state = start_combat(content, mage, (enemy(damage=1),))
    shielded = use(content, mage, state)
    assert shielded.player.shield > 0 or shielded.player.health < shielded.player.max_health


def test_a_shield_is_consumed_before_health(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_arcane_shield")
    state = start_combat(content, mage, (enemy(damage=10),))
    with_shield = replace(state, player=replace(state.player, shield=1_000))
    hit, lost = with_shield.player.damaged(50)
    assert lost == 0
    assert hit.shield == 950
    assert hit.health == with_shield.player.health


# --- area damage ------------------------------------------------------


def test_area_damage_reaches_every_enemy(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_fireball")
    state = start_combat(content, mage, (enemy("Первый"), enemy("Второй"), enemy("Третий")))
    for attempt in range(30):
        after = use(content, mage, state, seed=attempt)
        if all(target.health < target.enemy.max_health for target in after.enemies):
            return
    pytest.fail("an area skill never hit all three enemies")


def test_chain_damage_falls_off(content: GameContent) -> None:
    assert spec_for("damage_chain").chain_falloff > 0
    assert spec_for("damage_chain").aoe is True


# --- buffs and debuffs ------------------------------------------------


def test_a_self_buff_lands_as_an_effect(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_rally")
    state = start_combat(content, warrior, (enemy(),))
    buffed = use(content, warrior, state)
    assert len(buffed.player.effects) >= 1
    assert any(event.kind is EventKind.EFFECT_APPLIED for event in buffed.events)


def test_damage_reduction_buffs_store_a_negative_modifier(content: GameContent) -> None:
    """A power of 30 on buff_damage_taken means 30 percent *less* damage."""
    paladin = caster("paladin", "human", "paladin_aegis")
    state = start_combat(content, paladin, (enemy(),))
    guarded = use(content, paladin, state)
    modifiers = guarded.player.effects.modifiers()
    assert modifiers["damage_taken_percent"] < 0


def test_a_debuff_lands_on_the_enemy_as_a_penalty(content: GameContent) -> None:
    rogue = caster("rogue", "goblin", "rogue_smoke_bomb")
    state = start_combat(content, rogue, (enemy(),))
    smoked = use(content, rogue, state)
    effects = smoked.enemies[0].effects
    assert len(effects) == 1
    assert effects.modifiers()["accuracy_percent"] < 0
    assert next(iter(effects)).beneficial is False


def test_vulnerability_debuffs_are_positive(content: GameContent) -> None:
    ranger = caster("ranger", "wood_elf", "ranger_hunters_mark")
    state = start_combat(content, ranger, (enemy(),))
    marked = use(content, ranger, state)
    assert marked.enemies[0].effects.modifiers()["damage_taken_percent"] > 0


# --- cleansing and specials -------------------------------------------


def test_cleansing_strips_penalties(content: GameContent) -> None:
    cleric = caster("cleric", "aasimar", "cleric_purify")
    state = start_combat(content, cleric, (enemy(),))
    cursed = replace(
        state,
        player=replace(
            state.player,
            effects=state.player.effects.apply(
                ActiveEffect(
                    id="curse",
                    name="Проклятие",
                    modifiers={"damage_taken_percent": 20.0},
                    turns_left=5,
                    beneficial=False,
                )
            ),
        ),
    )
    cleansed = use(content, cleric, cursed)
    assert cleansed.player.effects.penalties() == ()
    assert any(event.kind is EventKind.CLEANSED for event in cleansed.events)


def test_free_cast_makes_the_next_skill_cost_nothing(content: GameContent) -> None:
    elf = caster("mage", "high_elf", "mage_firebolt")
    elf = replace(elf, loadout=replace(elf.loadout, racial="race_high_elf_mana_surge"))
    state = start_combat(content, elf, (enemy(),))
    surged = resolve_turn(content, elf, state, CombatAction(kind=ActionKind.RACIAL), b"\x02" * 16)
    assert surged.player.free_cast is True

    before = surged.player.resource
    cast = use(content, elf, surged)
    assert cast.player.resource >= before, "the free cast must not spend resource"


def test_cooldown_reset_clears_every_cooldown(content: GameContent) -> None:
    mage = caster("mage", "high_elf", "mage_arcane_shield", "mage_time_slip")
    state = start_combat(content, mage, (enemy(),))
    shielded = use(content, mage, state, slot=0)
    assert shielded.player.cooldowns
    reset = use(content, mage, shielded, slot=1)
    assert dict(reset.player.cooldowns) == {}


def test_evade_charges_absorb_the_next_hit(content: GameContent) -> None:
    halfling = caster("rogue", "halfling")
    halfling = replace(
        halfling, loadout=replace(halfling.loadout, racial="race_halfling_nimbleness")
    )
    state = start_combat(content, halfling, (enemy(damage=500),))
    dodged = resolve_turn(
        content, halfling, state, CombatAction(kind=ActionKind.RACIAL), b"\x03" * 16
    )
    assert any(event.kind is EventKind.DODGE for event in dodged.events)
    assert dodged.player.health == dodged.player.max_health


def test_avoid_combat_can_end_the_fight_peacefully(content: GameContent) -> None:
    half_elf = caster("paladin", "half_elf")
    half_elf = replace(half_elf, loadout=replace(half_elf.loadout, racial="race_half_elf_charm"))
    state = start_combat(content, half_elf, (enemy(),))
    outcomes = {
        resolve_turn(
            content,
            half_elf,
            state,
            CombatAction(kind=ActionKind.RACIAL),
            index.to_bytes(16, "big"),
        ).outcome
        for index in range(40)
    }
    assert CombatOutcome.AVOIDED in outcomes


def test_stuns_make_an_enemy_skip_a_turn(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_shield_bash")
    state = start_combat(content, warrior, (enemy(),))
    for attempt in range(40):
        bashed = use(content, warrior, state, seed=attempt)
        if any(event.kind is EventKind.STUNNED for event in bashed.events):
            return
    pytest.fail("shield bash never stunned across 40 seeds")


# --- upkeep and helpers -----------------------------------------------


def test_regeneration_traits_heal_at_end_of_turn(content: GameContent) -> None:
    troll = caster("warrior", "troll", "warrior_cleave")
    troll = replace(troll, trait_ids=("steady_breath",))
    state = start_combat(content, troll, (enemy(damage=1),))
    hurt = replace(state, player=replace(state.player, health=100))
    later = resolve_turn(content, troll, hurt, CombatAction(kind=ActionKind.ATTACK), b"\x05" * 16)
    assert later.player.health >= 100


def test_low_health_helper(content: GameContent) -> None:
    warrior = caster("warrior", "human", "warrior_cleave")
    state = start_combat(content, warrior, (enemy(),))
    assert is_low_health(state) is False
    hurt = replace(state, player=replace(state.player, health=1))
    assert is_low_health(hurt) is True


@pytest.mark.parametrize(
    "category",
    [
        EffectCategory.DAMAGE,
        EffectCategory.HEAL,
        EffectCategory.SHIELD,
        EffectCategory.BUFF,
        EffectCategory.DEBUFF,
        EffectCategory.CLEANSE,
        EffectCategory.SPECIAL,
    ],
)
def test_every_category_is_used_by_content(content: GameContent, category: EffectCategory) -> None:
    """Each category must be represented, otherwise the engine has dead branches."""
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
    skill = content.skill("warrior_cleave")
    splashing = skill.edges[1]
    hero = caster("warrior", "human", skill.code)
    hero = replace(hero, loadout=replace(hero.loadout, ranks={skill.code: 3}))
    pair = (enemy("Волк"), enemy("Волчица"))

    plain = use(content, hero, start_combat(content, hero, pair))
    wide = use(
        content, with_edge(hero, skill.code, splashing.code), start_combat(content, hero, pair)
    )

    # Второй противник цел без грани и ранен с гранью.
    assert plain.enemies[1].health == plain.enemies[1].enemy.max_health
    assert wide.enemies[1].health < wide.enemies[1].enemy.max_health


def test_an_edge_that_promises_bleeding_leaves_the_target_bleeding(
    content: GameContent,
) -> None:
    skill = content.skill("warrior_cleave")
    bleeding = skill.edges[0]
    hero = caster("warrior", "human", skill.code)
    hero = replace(hero, loadout=replace(hero.loadout, ranks={skill.code: 3}))
    foes = (enemy(),)

    plain = use(content, hero, start_combat(content, hero, foes))
    cutting = use(
        content, with_edge(hero, skill.code, bleeding.code), start_combat(content, hero, foes)
    )

    assert plain.enemies[0].effects.penalties() == ()
    assert cutting.enemies[0].effects.penalties() != ()


def test_an_edge_that_promises_a_discount_is_a_discount(content: GameContent) -> None:
    """«Экономный шар»: тот же шар за меньшие чары."""
    skill = content.skill("mage_fireball")
    cheaper = skill.edges[1]
    hero = caster("mage", "human", skill.code)
    hero = replace(hero, loadout=replace(hero.loadout, ranks={skill.code: 3}))
    foes = (enemy(),)

    plain = use(content, hero, start_combat(content, hero, foes))
    thrifty = use(
        content, with_edge(hero, skill.code, cheaper.code), start_combat(content, hero, foes)
    )

    assert thrifty.player.resource > plain.player.resource


def test_a_passive_edge_is_not_just_a_label(content: GameContent) -> None:
    """Половина выбранных граней в игре - у постоянных умений, и они не делали ничего."""
    from mmorpg.domain.rules import modifiers as mods

    skill = content.skill("warrior_toughness")
    hero = caster("warrior", "human")
    hero = replace(
        hero,
        loadout=replace(hero.loadout, passives=(skill.code, None, None), ranks={skill.code: 3}),
    )

    plain = mods.passive_modifiers(content, hero)
    edged = mods.passive_modifiers(content, with_edge(hero, skill.code, skill.edges[0].code))

    assert edged != plain


def test_bleeding_actually_takes_health_every_turn(content: GameContent) -> None:
    """«и ещё 3 хода» долго было надписью: ``dot_turns`` не читал никто.

    Проверяется не «эффект висит», а «здоровья стало меньше», потому что сломано
    было именно второе.
    """
    skill = content.skill("rogue_poison_blade")
    hero = caster("rogue", "human", skill.code)
    # Отравленный клинок просит клинок: без кинжала умение не сработает вовсе, и
    # тест мерил бы отказ, а не кровотечение.
    hero = replace(
        hero,
        loadout=replace(hero.loadout, ranks={skill.code: 1}),
        equipment=hero.equipment.equip("weapon", "poacher_daggers"),
    )

    after = use(content, hero, start_combat(content, hero, (enemy(),)))
    poisoned = after.enemies[0]
    assert poisoned.effects.penalties() != ()

    bleeding = spend_bleeding(after)
    assert bleeding.enemies[0].health < poisoned.health
