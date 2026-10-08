"""Воспроизводимая матрица развития; расчёт не подменяет игру людьми."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mmorpg.application.dto.creation import CharacterDraft
from mmorpg.domain.entities.character import Character, Equipment
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Verdict
from mmorpg.domain.entities.content import GameContent, Item, OwnerKind
from mmorpg.domain.entities.location import EnemyRank, NodeKind
from mmorpg.domain.entities.quest import ObjectiveKind
from mmorpg.domain.entities.stats import StatBlock
from mmorpg.domain.procgen.enemies import RANK_FACTORS, generate_enemy, generate_group, group_scale
from mmorpg.domain.procgen.items import tier_at
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import (
    adventure,
    economy,
    equipment,
    modifiers,
    party,
    progression,
    quests,
    repair,
    skills,
    subclass,
    tutorial,
)
from mmorpg.domain.rules.combat import act, hero_combatant, monster_combatant, open_battle
from mmorpg.domain.rules.skill_effects import EffectCategory, spec_for
from mmorpg.domain.rules.skill_mastery import changed, words
from mmorpg.domain.rules.stats import primary_stats
from mmorpg.infrastructure.content import load_content

CHECKPOINTS = (1, 12, 29, 30, 74, 75, 149, 150)
SEED = b"vellar-m04-20261008"


@dataclass(frozen=True)
class PathResult:
    class_id: str
    race_id: str
    members: int
    rank: str
    battles: int
    earned_gold: int
    equipment_cost: int
    repair_cost: int
    travel_cost: int
    paid_healing_cost: int
    affordable_paid_healing: bool
    free_healing_available: bool
    remaining_gold: int
    minimum_gold: int
    reached_level: int
    blocked: str


def market_gear(content: GameContent) -> dict[tuple[str, int], tuple[Item, ...]]:
    """Берём только вещи, реально появившиеся на обычных полках за 128 оборотов."""
    result = {}
    for tier in content.gear_tiers:
        seen: dict[str, Item] = {}
        for rotation in range(128):
            for item in economy.roll_assortment(
                content,
                world_seed="m04-market",
                city_id=content.cities[0].id,
                rotation=rotation,
                character_level=tier.level,
            ):
                if item.rarity == "common" and item.level <= tier.level:
                    seen[item.id] = item
        for klass in content.classes:
            picked = []
            for slot in ("weapon", "head", "body", "hands", "feet"):
                candidates = [
                    item
                    for item in seen.values()
                    if item.slot == slot
                    and (
                        klass.can_wield(item.weapon_type)
                        if item.is_weapon
                        else item.is_armor and klass.can_wear(item.armor_type)
                    )
                ]
                if not candidates:
                    raise ValueError(f"No market equipment: {klass.id}, {tier.level}, {slot}")
                # Доступная по пути обычная вещь; не редкая добыча и не идеальный оттиск.
                picked.append(max(candidates, key=lambda item: (item.level, -item.price)))
            result[klass.id, tier.level] = tuple(picked)
    return result


def newcomer(content: GameContent, class_id: str, race_id: str) -> Character:
    klass = content.character_class(class_id)
    allocated: dict[str, int] = {}
    for index in range(content.rules.free_points_at_creation):
        code = klass.key_stats[index % len(klass.key_stats)].value
        allocated[code] = allocated.get(code, 0) + 1
    return replace(
        CharacterDraft(
            name="Проба",
            class_id=class_id,
            race_id=race_id,
            allocated=StatBlock.from_mapping(allocated),
        ).to_character(content, 1),
        id=1,
    )


def prepare_level(content: GameContent, character: Character, level: int) -> Character:
    """Очки получены кривой опыта; умения оплачены разрешённым числом очков."""
    wanted = max(0, progression.experience_to_reach(level) - character.experience)
    low, high = 0, max(1, wanted)
    while progression.earned(content, character, high) < wanted:
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if progression.earned(content, character, middle) < wanted:
            low = middle + 1
        else:
            high = middle
    character, _ = progression.grant_experience(content, character, low)
    assert character.level == level
    klass = content.character_class(character.class_id)
    allocated = character.allocated
    for index in range(character.unspent_stat_points):
        allocated = allocated.with_change(klass.key_stats[index % len(klass.key_stats)], 1)
    character = replace(character, allocated=allocated, unspent_stat_points=0)
    candidates = [
        s
        for s in skills.teachable(content, character)
        if s.is_active and not equipment.skill_refusal(content, character, s)
    ]
    while character.unspent_skill_points:
        available = [s for s in candidates if skills.learnable(content, character, s)]
        if not available:
            break
        selected = min(
            available, key=lambda s: (skills.is_known(character, s.code), s.level, s.code)
        )
        learned = skills.learn(content, character, selected)
        assert learned is not None
        character = learned
    known = [s for s in candidates if skills.is_known(character, s.code)]
    selected = sorted(
        known,
        key=lambda s: (spec_for(s.effect).category == EffectCategory.HEAL, s.level),
        reverse=True,
    )[:6]
    loadout = replace(
        character.loadout, actives=tuple([s.code for s in selected] + [None] * (6 - len(selected)))
    )
    return replace(character, loadout=loadout)


def path(
    content: GameContent,
    klass: str,
    race: str,
    members: int,
    market: dict[tuple[str, int], tuple[Item, ...]],
    rank: EnemyRank = EnemyRank.NORMAL,
) -> PathResult:
    hero = adventure.apply_tutorial_rewards(
        content,
        replace(newcomer(content, klass, race), tutorial=(1 << len(tutorial.ORDER)) - 1),
        frozenset(tutorial.ORDER),
    ).character
    # Все шесть шагов и завершение дают эти постоянные суммы; предметы не продаём.
    gold = len(tutorial.ORDER) * tutorial.STEP_REWARD.gold + tutorial.COMPLETION_REWARD.gold
    earned = gold
    gear_cost = repairs = healing = battles = travel = 0
    last_tier = 0
    city = content.cities[0]
    free_available = True
    minimum = gold
    blocked = ""

    def rewards(level: int) -> tuple[int, int]:
        place = next(loc for loc in city.locations if loc.covers(level))
        enemies = generate_group(
            derive(SEED, "path", level, rank.value),
            archetypes=content.enemy_archetypes,
            biome=place.biome,
            level=level,
            rank=rank,
            elite_titles=content.elite_titles,
        )
        reward = party.split(sum(e.gold for e in enemies), members)[-1]
        experience = round(
            group_scale(len(enemies))
            * sum(
                progression.experience_reward(enemy_level=e.level, character_level=level)
                * RANK_FACTORS[e.rank].experience
                * e.stakes
                for e in enemies
            )
        )
        experience = progression.earned(content, hero, party.split(experience, members)[-1])
        return reward, experience

    def earn(count: int, level: int) -> None:
        nonlocal gold, earned, battles, repairs, hero, minimum
        # Чиним раз в 20 побед, раньше поломки даже стартовых вещей.
        while count:
            batch = min(20, count)
            assert all(repair.left(hero, item) > batch for item in repair.gear_on(content, hero))
            income = batch * rewards(level)[0]
            worn, _ = repair.wear(content, hero, batch)
            bill = repair.bill(content, worn)
            cost = repair.total(bill)
            gold += income - cost
            earned += income
            repairs += cost
            battles += batch
            minimum = min(minimum, gold)
            hero = repair.repaired(worn, (item for item, _ in bill))
            count -= batch

    def fund(cost: int, level: int) -> bool:
        attempts = 0
        while gold < cost:
            before = gold
            earn(20, min(level, city.level_max))
            attempts += 1
            if gold <= before or attempts > 1000:
                return False
        return True

    for level in range(hero.level, 151):
        hero = prepare_level(content, hero, level)
        destinations = [
            c
            for c in content.cities_available_at(level)
            if any(loc.covers(level) for loc in c.locations)
            and {"shop", "tavern", "forge", "mentor"} <= set(c.services)
        ]
        if not destinations:
            raise ValueError(f"No accessible development route at level {level}")
        destination = min(destinations, key=lambda c: abs(c.order - city.order))
        if destination.id != city.id:
            cost = economy.travel_price(level, abs(destination.order - city.order))
            if not fund(cost, level):
                blocked = "travel"
                break
            travel += cost
            gold -= cost
            city = destination
            minimum = min(minimum, gold)
        tier = tier_at(content, level)
        assert tier is not None
        if tier.level != last_tier:
            pieces = market[klass, tier.level]
            bought = [item for item in pieces if hero.equipment.item_in(item.slot) != item.id]
            cost = sum(
                economy.buy_price(
                    content,
                    item,
                    charisma=primary_stats(content, hero).CHA,
                    modifiers=modifiers.collect_modifiers(content, hero),
                )
                for item in bought
            )
            # Прежде покупки можно заработать в прежнем снаряжении. Цена этих
            # дополнительных боёв отдельно включена; модель не обещает их победу.
            if not fund(cost, level):
                blocked = "equipment"
                break
            gold -= cost
            gear_cost += cost
            hero = replace(hero, equipment=Equipment({item.slot: item.id for item in pieces}))
            last_tier = tier.level
            minimum = min(minimum, gold)
        if level == 150:
            break
        experience = rewards(level)[1]
        needed = progression.experience_to_reach(level + 1) - hero.experience
        count = math.ceil(needed / max(1, experience))
        earn(count, level)
        if gold < 0:
            blocked = "repair"
            break
        healing += economy.inn_price(level) * count
        injured = replace(hero, health=1, gold=0)
        free_available &= adventure.rest(content, injured, paid=False).healed > 0
    return PathResult(
        klass,
        race,
        members,
        rank.value,
        battles,
        earned,
        gear_cost,
        repairs,
        travel,
        healing,
        gold >= healing,
        free_available,
        gold,
        minimum,
        level,
        blocked,
    )


def battle_probe(
    content: GameContent, character: Character, trial: int, members: int, clever: bool
) -> dict[str, object]:
    seed = derive(SEED, character.class_id, character.race_id, character.level, trial, members)
    enemies = generate_group(
        seed,
        archetypes=content.enemy_archetypes,
        biome="*",
        level=character.level,
        rank=EnemyRank.NORMAL,
        elite_titles=content.elite_titles,
    )
    roster = {i: replace(character, id=i, user_id=i) for i in range(1, members + 1)}
    fighters = [
        hero_combatant(content, h, combatant_id=i, side=0, live=True) for i, h in roster.items()
    ]
    fighters.extend(
        monster_combatant(e, combatant_id=members + i, side=1) for i, e in enumerate(enemies, 1)
    )
    state = open_battle(content, roster, fighters, seed)
    turns = 0
    while not state.is_over and turns < 200:
        actor = state.active
        assert actor is not None
        hero = roster[actor.character_id]
        action = BattleAction(ActionKind.ATTACK)
        if clever:
            available = []
            for slot, code in enumerate(hero.loadout.actives):
                if not code:
                    continue
                skill = content.skill(code)
                if actor.cooldown_of(code) or equipment.skill_refusal(content, hero, skill):
                    continue
                spec = spec_for(skill.effect)
                if spec.category == EffectCategory.HEAL and actor.health < actor.max_health * 0.6:
                    available.append((10_000, slot))
                elif spec.category == EffectCategory.DAMAGE:
                    available.append((skill.power_at_rank(hero.loadout.rank_of(code)), slot))
            if available:
                action = BattleAction(ActionKind.SKILL, slot=max(available)[1])
        updated = act(content, roster, state, action, derive(seed, "turn", turns))
        if updated == state:  # отказ не двигает ход: обычный удар остаётся доступным
            updated = act(
                content,
                roster,
                state,
                BattleAction(ActionKind.ATTACK),
                derive(seed, "fallback", turns),
            )
        state = updated
        turns += 1
    return {
        "class": character.class_id,
        "race": character.race_id,
        "level": character.level,
        "members": members,
        "trial": trial,
        "clever": clever,
        "won": state.verdict_for(1) == Verdict.VICTORY,
        "turns": turns,
        "health": state.by_id(1).health,
        "finished": state.is_over,
    }


def content_matrix(content: GameContent) -> dict[str, int]:
    trials = 0
    for branch in content.subclasses:
        parent_chain = []
        parent = branch.parent
        while parent:
            parent_chain.insert(0, parent)
            parent = content.subclass(parent).parent
        hero = replace(
            newcomer(content, branch.class_id, "human"),
            level=branch.level,
            subclass_ids=tuple(parent_chain),
        )
        assert branch in subclass.offered(content, hero, branch.tier)
        chain = subclass.trial_quests(content, branch)
        assert len(chain) == 3
        for quest in chain:
            assert quest.is_trial and quest.target_count > 0
            assert quests.is_open(quest, hero)
            hero = replace(hero, quests=hero.quests.take(quest.id))
            if quest.objective in {ObjectiveKind.KILL, ObjectiveKind.ELITE}:
                places = [
                    loc
                    for city in content.cities_available_at(hero.level)
                    for loc in city.locations
                ]
                source = next(
                    (arch, loc)
                    for arch in content.enemy_archetypes
                    for loc in places
                    if not arch.dungeon
                    and arch.fits(loc.biome)
                    and (not quest.target_kind or quest.target_kind in {arch.id, arch.kind.value})
                )
                enemy = generate_enemy(
                    derive(SEED, quest.id),
                    archetypes=(source[0],),
                    biome=source[1].biome,
                    level=source[1].level_min,
                    rank=EnemyRank.ELITE
                    if quest.objective is ObjectiveKind.ELITE
                    else EnemyRank.NORMAL,
                )
                log, _ = quests.record_kills(content, hero, (enemy,) * quest.target_count)
                hero = replace(hero, quests=log)
            elif quest.objective is ObjectiveKind.SEARCH:
                from mmorpg.presentation.telegram.flows.play import build_location
                from mmorpg.presentation.telegram.flows.state import LocationSession

                kind = NodeKind(quest.target_kind or "cache")
                assert any(
                    kind
                    in {
                        node.kind
                        for node in build_location(
                            content, "m04-trials", LocationSession(city.id, loc.slot, 1, 0)
                        ).nodes
                    }
                    for city in content.cities_available_at(hero.level)
                    for loc in city.locations
                )
                for _ in range(quest.target_count):
                    log, _ = quests.record_search(content, hero, kind)
                    hero = replace(hero, quests=log)
            else:
                raise ValueError(f"Unverified trial objective: {quest.id}")
            payout = quests.hand_in(content, hero, quest)
            assert payout is not None and quests.hand_in(content, payout.character, quest) is None
            hero = payout.character
            trials += 1
        assert subclass.choose(content, hero, branch.id) is not None
    masteries = 0
    for skill in content.skills:
        for mastery in skill.masteries:
            spec = changed(mastery, spec_for(skill.effect))
            assert spec is not None and words(mastery)
            assert mastery.tier in {1, 2}
            class_id = (
                content.subclass(skill.owner_id).class_id
                if skill.owner_kind is OwnerKind.SUBCLASS
                else skill.owner_id
                if skill.owner_kind is OwnerKind.CLASS
                else "warrior"
            )
            race_id = skill.owner_id if skill.owner_kind is OwnerKind.RACE else "human"
            hero = newcomer(content, class_id, race_id)
            if skill.owner_kind is OwnerKind.SUBCLASS:
                chain = []
                branch = content.subclass(skill.owner_id)
                while branch:
                    chain.insert(0, branch.id)
                    branch = content.subclass(branch.parent) if branch.parent else None
                hero = replace(hero, subclass_ids=tuple(chain))
            hero, _ = progression.grant_experience(
                content, hero, progression.experience_to_reach(150)
            )
            hero = replace(
                hero,
                loadout=replace(
                    hero.loadout, ranks={}, masteries={}, actives=(None,) * 6, racial=None
                ),
            )
            for _ in range(5):
                learned = skills.learn(content, hero, skill)
                assert learned is not None, skill.code
                hero = learned
            if mastery.tier == 2:
                first = next(one for one in skill.masteries if one.tier == 1)
                hero = skills.choose_mastery(content, hero, skill, first.code)
                assert hero is not None
            chosen = skills.choose_mastery(content, hero, skill, mastery.code)
            assert chosen is not None and mastery.code in chosen.loadout.masteries_of(skill.code)
            masteries += 1
    return {
        "classes": len(content.classes),
        "races": len(content.races),
        "branches": len(content.subclasses),
        "trials": trials,
        "masteries": masteries,
    }


def run(content: GameContent, *, trials: int = 3) -> dict[str, object]:
    market = market_gear(content)
    paths = [
        asdict(path(content, k.id, r.id, members, market, rank))
        for k in content.classes
        for r in content.races
        for members, rank in ((1, EnemyRank.NORMAL), (5, EnemyRank.NORMAL), (5, EnemyRank.BOSS))
    ]
    battles = []
    for klass in content.classes:
        for race in content.races:
            hero = newcomer(content, klass.id, race.id)
            for level in CHECKPOINTS:
                tier = tier_at(content, level)
                hero = replace(
                    hero, equipment=Equipment({i.slot: i.id for i in market[klass.id, tier.level]})
                )
                hero = prepare_level(content, hero, level)
                for members in (1, 5):
                    for clever in (False, True):
                        battles.extend(
                            battle_probe(content, hero, trial, members, clever)
                            for trial in range(trials)
                        )
    return {
        "seed": SEED.decode(),
        "trials": trials,
        "matrix": content_matrix(content),
        "paths": paths,
        "battles": battles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("backups/m04-progression.json"))
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    result = run(load_content(Path("content")), trials=args.trials)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["matrix"], ensure_ascii=False))
    print(f"Report saved: {args.output}")


if __name__ == "__main__":
    main()
