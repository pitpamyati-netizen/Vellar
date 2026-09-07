"""Уклады веток: специализация меняет сам бой, а не числа в нём (ADR 0082).

Ветка давала умение, свёрток прибавок и правку сетки - и потому Некромант
отличался от Пиромана родом урона, а не тем, как за него играют. Уклад - это
правило, которое движок исполняет весь бой: некромант поднимает павших, бард
кладёт своё усиление на весь отряд, палач добивает и снимает откаты, заслон
вызывает удар на себя.
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
from mmorpg.domain.rules import subclass_powers as way_rules
from mmorpg.domain.rules.combat import (
    act,
    hero_combatant,
    monster_combatant,
    open_battle,
    with_companions,
)

SEED = b"subclass-way-seed"
ATTACK = BattleAction(kind=ActionKind.ATTACK)


def make_enemy(
    *,
    name: str = "Чучело",
    health: int = 20_000,
    damage: int = 40,
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
        loot=("wolf_pelt",),
        gold=7,
        rank=EnemyRank.NORMAL,
        role=EnemyRole.BRUTE,
    )


def branch_with(content: GameContent, way: str) -> str:
    """Первая ветка, несущая этот уклад."""
    return next(one.id for one in content.subclasses if one.power == way)


def hero_of(content: GameContent, way: str, *, level: int = 150) -> Character:
    """Персонаж того класса, чья ветка несёт этот уклад, с этой веткой."""
    branch = content.subclass(branch_with(content, way))
    character = Character(
        id=1,
        user_id=100,
        name="Тест",
        race_id="human",
        class_id=branch.class_id,
        level=level,
        loadout=SkillLoadout(racial="race_human_second_wind"),
    )
    return character.with_subclass(branch.id)


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


# --- реестр и содержимое ----------------------------------------------


def test_every_branch_names_a_way(content: GameContent) -> None:
    """Ветка без уклада отличалась бы от соседней одними процентами."""
    silent = [one.id for one in content.subclasses if not one.power]
    assert not silent, silent


def test_every_way_is_taken_by_some_branch(content: GameContent) -> None:
    """Уклад, которого не несёт ни одна ветка, - это код без содержимого."""
    taken = {one.power for one in content.subclasses}
    assert taken >= way_rules.POWER_KEYS, way_rules.POWER_KEYS - taken


def test_a_branch_does_not_repeat_its_parent(content: GameContent) -> None:
    """Ребёнок добавляет правило, а не повторяет родительское.

    Ветка, чей уклад уже есть у родителя, не даёт игроку ничего нового: три
    ступени превратились бы в одну, взятую трижды.
    """
    ways = {one.id: one.power for one in content.subclasses}
    repeated = [
        one.id for one in content.subclasses if one.parent and ways.get(one.parent) == one.power
    ]
    assert not repeated, repeated


def test_a_way_says_what_it_does(content: GameContent) -> None:
    """Текст уклада написан по тому, что делает движок, и не пуст."""
    for way in way_rules.POWERS:
        assert way.name and way.text, way.code


# --- уклады в бою -----------------------------------------------------


def test_the_dead_rise_once(content: GameContent) -> None:
    """«Поднять павшего»: убитый встаёт на вашей стороне, и слуга один."""
    hero = hero_of(content, "raise_dead")
    state, roster = start(content, hero, make_enemy(health=1), make_enemy(health=1, name="Второй"))
    after = act(content, roster, state, BattleAction(kind=ActionKind.ATTACK, target=2), SEED)
    assert any(event.kind is EventKind.SUMMONED for event in after.events)
    servant = next(one for one in after.combatants if one.master_id == 1)
    assert servant.side == one_of(after, 1).side
    # Плату павший приносит сам: у слуги её нет.
    assert servant.enemy is not None
    assert servant.enemy.gold == 0
    assert servant.enemy.loot == ()

    # Второго слугу не поднимают.
    again = act(content, roster, after, BattleAction(kind=ActionKind.ATTACK, target=3), SEED)
    assert sum(1 for one in again.combatants if one.master_id == 1) == 1


def test_a_servant_leaves_with_its_master(content: GameContent) -> None:
    """Слуга уходит с поля вместе с тем, кто его привёл."""
    from mmorpg.domain.rules.combat import _dismissed

    hero = hero_of(content, "raise_dead")
    state, _ = start(content, hero, make_enemy(health=1))
    fallen = state.replace_combatant(replace(one_of(state, 1), health=0))
    servant = replace(
        monster_combatant(make_enemy(), combatant_id=9, side=0),
        master_id=1,
    )
    with_servant = replace(fallen, combatants=(*fallen.combatants, servant))
    assert _dismissed(with_servant).by_id(9).left  # type: ignore[union-attr]


def test_a_companion_joins_the_battle(content: GameContent) -> None:
    """«Зверь рядом»: спутник входит в бой сам и дерётся за хозяина."""
    hero = hero_of(content, "beastmaster")
    state, _ = start(content, hero)
    companions = [one for one in state.combatants if one.master_id == 1]
    assert len(companions) == 1
    assert companions[0].side == one_of(state, 1).side


def test_companions_respect_the_line(content: GameContent) -> None:
    """Там, где пятеро уже стоят, спутнику войти некуда."""
    hero = hero_of(content, "beastmaster")
    crowd = [
        replace(
            hero_combatant(content, hero, combatant_id=index + 1, side=0, live=True),
            master_id=0,
        )
        for index in range(5)
    ]
    assert len(with_companions(crowd)) == 5


def test_siphon_feeds_the_caster(content: GameContent) -> None:
    """«Вытягивание»: тёмный удар возвращает бьющему часть нанесённого.

    Берётся Оккультист: у него и уклад, и своё тёмное умение, - и видно, что
    ветка работает целиком, а не половиной.
    """
    branch = content.subclass("mage_occultist")
    assert branch.power == "siphon"
    hero = Character(
        id=1,
        user_id=100,
        name="Тест",
        race_id="human",
        class_id=branch.class_id,
        level=150,
        loadout=SkillLoadout(racial="race_human_second_wind"),
    ).with_subclass(branch.id)
    skill = content.skill(branch.skill_code)
    armed = replace(
        hero,
        loadout=replace(
            hero.loadout,
            actives=(skill.code, None, None, None, None, None),
            ranks={skill.code: 1},
        ),
    )
    state, roster = start(content, armed)
    wounded = state.replace_combatant(replace(one_of(state, 1), health=100))
    after = act(content, roster, wounded, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert one_of(after, 1).health > 100


def test_plague_stretches_what_it_leaves(content: GameContent) -> None:
    """«Порча»: наложенное держится дольше и точит сильнее."""
    from mmorpg.domain.rules.combat import _weathered
    from mmorpg.domain.rules.skill_effects import spec_for

    hero = hero_of(content, "plague")
    state, _ = start(content, hero)
    spec = spec_for("damage_venom")
    weathered = _weathered(spec, one_of(state, 1))
    assert weathered.dot_turns == spec.dot_turns + way_rules.PLAGUE_TURNS
    assert weathered.dot_scale == pytest.approx(spec.dot_scale * way_rules.PLAGUE_SCALE)


def test_the_stormcaller_spreads_the_blow(content: GameContent) -> None:
    """«Буревестник»: удар по всем сильнее, одноцелевой задевает соседа."""
    from mmorpg.domain.rules.combat import _weathered
    from mmorpg.domain.rules.skill_effects import spec_for

    hero = hero_of(content, "stormcaller")
    state, _ = start(content, hero)
    single = _weathered(spec_for("damage"), one_of(state, 1))
    sweep = _weathered(spec_for("damage_aoe"), one_of(state, 1))
    assert single.splash == pytest.approx(way_rules.STORM_SPLASH)
    assert sweep.damage_scale > 1.0


def test_the_bulwark_pulls_the_blow_onto_itself(content: GameContent) -> None:
    """«Заслон»: ударенный смотрит на того, кто его ударил."""
    hero = hero_of(content, "bulwark")
    state, roster = start(content, hero)
    after = act(content, roster, state, ATTACK, SEED)
    assert one_of(after, 2).effects.has(StatusKind.TAUNT)


def test_the_executioner_finishes(content: GameContent) -> None:
    """«Палач»: по цели ниже половины бьёт сильнее, а добив - снимает откаты."""
    hero = hero_of(content, "executioner")
    state, roster = start(content, hero, make_enemy(health=1))
    ready = state.replace_combatant(replace(one_of(state, 1), cooldowns={"some_skill": 4}))
    after = act(content, roster, ready, ATTACK, SEED)
    assert one_of(after, 1).cooldown_of("some_skill") == 0


def test_the_lullaby_punishes_the_held(content: GameContent) -> None:
    """«Морок»: по цели, у которой отняли ход, бьют заметно сильнее."""
    hero = hero_of(content, "lullaby")
    plain = replace(hero, subclass_ids=())
    state, roster = start(content, hero)
    bare_state, bare_roster = start(content, plain)

    def held(source: BattleState) -> BattleState:
        target = one_of(source, 2)
        stun = status_effect(StatusKind.STUN, turns=3)
        return source.replace_combatant(replace(target, effects=target.effects.apply(stun)))

    with_way = act(content, roster, held(state), ATTACK, SEED)
    without = act(content, bare_roster, held(bare_state), ATTACK, SEED)
    assert one_of(with_way, 2).health < one_of(without, 2).health


def test_the_duelist_needs_one_foe(content: GameContent) -> None:
    """«Поединщик»: один на один бьёт сильнее, в толпе - как все."""
    from mmorpg.domain.rules.combat import _way_damage

    hero = hero_of(content, "duelist")
    alone, _ = start(content, hero)
    crowd, _ = start(content, hero, make_enemy(), make_enemy(name="Второй"))
    assert _way_damage(alone, one_of(alone, 1), one_of(alone, 2)) > _way_damage(
        crowd, one_of(crowd, 1), one_of(crowd, 2)
    )


def test_the_berserker_grows_with_wounds(content: GameContent) -> None:
    """«Раж»: чем меньше здоровья, тем сильнее удар."""
    from mmorpg.domain.rules.combat import _way_damage

    hero = hero_of(content, "berserker")
    state, _ = start(content, hero)
    full = one_of(state, 1)
    wounded = replace(full, health=1)
    assert _way_damage(state, wounded, one_of(state, 2)) > _way_damage(
        state, full, one_of(state, 2)
    )


def test_the_warlord_lifts_the_party(content: GameContent) -> None:
    """«Военачальник»: пока он жив, вся его сторона бьёт сильнее."""
    from mmorpg.domain.rules.combat import _way_damage

    hero = hero_of(content, "warlord")
    state, _ = start(content, hero)
    assert _way_damage(state, one_of(state, 1), one_of(state, 2)) > 1.0


def test_the_sanctuary_leaves_a_barrier(content: GameContent) -> None:
    """«Заступничество»: вылеченное держится ещё и барьером."""
    hero = hero_of(content, "sanctuary")
    healing = next(
        one
        for one in content.skills
        if one.owner_id == hero.class_id and one.is_active and one.effect.startswith("heal")
    )
    armed = replace(
        hero,
        loadout=replace(
            hero.loadout,
            actives=(healing.code, None, None, None, None, None),
            ranks={healing.code: 1},
        ),
    )
    state, roster = start(content, armed)
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert one_of(after, 1).barrier > 0


def test_the_trickster_keeps_the_shadow_once(content: GameContent) -> None:
    """«Ловкач»: первый удар из незаметности хозяина не выдаёт."""
    hero = hero_of(content, "trickster")
    state, roster = start(content, hero, make_enemy(initiative=0.1))
    hidden = state.replace_combatant(
        replace(
            one_of(state, 1),
            effects=one_of(state, 1).effects.apply(status_effect(StatusKind.UNSEEN, turns=5)),
        )
    )
    after = act(content, roster, hidden, ATTACK, SEED)
    assert one_of(after, 1).effects.has(StatusKind.UNSEEN)


def test_the_zealot_heals_with_light(content: GameContent) -> None:
    """«Ревнитель»: святой удар лечит самого раненого в отряде."""
    hero = hero_of(content, "zealot")
    holy = next(
        one
        for one in content.skills
        if one.owner_id == hero.class_id
        and one.is_active
        and ("holy" in one.effect or "light" in one.effect)
    )
    armed = replace(
        hero,
        loadout=replace(
            hero.loadout,
            actives=(holy.code, None, None, None, None, None),
            ranks={holy.code: 1},
        ),
    )
    state, roster = start(content, armed)
    wounded = state.replace_combatant(replace(one_of(state, 1), health=200))
    after = act(content, roster, wounded, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert one_of(after, 1).health > 200


def test_the_refrain_covers_the_party(content: GameContent) -> None:
    """«Припев»: усиление ложится на весь отряд, а не на одного певца."""
    hero = hero_of(content, "refrain")
    buff = next(
        one
        for one in content.skills
        if one.owner_id == hero.class_id
        and one.is_active
        and one.effect.startswith("buff_")
        and one.effect not in {"buff_evade_full", "buff_free_cast", "buff_cooldown_reset"}
    )
    armed = replace(
        hero,
        loadout=replace(
            hero.loadout,
            actives=(buff.code, None, None, None, None, None),
            ranks={buff.code: 1},
        ),
    )
    mate = replace(armed, id=2, name="Товарищ")
    roster = {1: armed, 2: mate}
    fighters = [
        hero_combatant(content, armed, combatant_id=1, side=0, live=True),
        hero_combatant(content, mate, combatant_id=2, side=0, live=False),
        monster_combatant(make_enemy(), combatant_id=3, side=1),
    ]
    state = open_battle(content, roster, fighters, SEED)
    after = act(content, roster, state, BattleAction(kind=ActionKind.SKILL, slot=0), SEED)
    assert len(one_of(after, 2).effects) > len(one_of(state, 2).effects)
