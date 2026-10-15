"""Воспроизводимые бои законных сборок; не обращается к службам и игрокам."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from unittest.mock import patch

from tests.domain.test_combat_balance import armed, build

from mmorpg.domain.entities import Character, Equipment, GameContent, SkillLoadout
from mmorpg.domain.entities.combat import (
    ActionKind,
    BattleAction,
    BattleState,
    Combatant,
    EventKind,
    Verdict,
)
from mmorpg.domain.entities.content import OwnerKind
from mmorpg.domain.entities.location import Enemy, EnemyRank
from mmorpg.domain.entities.statuses import StatusKind
from mmorpg.domain.procgen import items as item_procgen
from mmorpg.domain.procgen.enemies import generate_group
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import equipment, skills, talents
from mmorpg.domain.rules.combat import (
    act,
    blow_of,
    hero_combatant,
    monster_combatant,
    open_battle,
    skill_price,
)
from mmorpg.domain.rules.progression import skill_points_between
from mmorpg.domain.rules.skill_effects import EffectCategory
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.infrastructure.content import load_content

LEVELS = (1, 10, 30, 60, 100, 150)
SEED = b"character-rework-20261015"
PROFILES = ("developing", "developed", "developed_rare")


@dataclass
class DecisionMemory:
    last_barrier_round: dict[int, int] = field(default_factory=dict)


@dataclass
class FighterMetrics:
    minimum_health_ratio: float = 1.0
    received_damage: int = 0
    used_resource: int = 0
    restored_resource: int = 0
    barrier_absorbed: int = 0


@contextmanager
def observed_battle(roster: Mapping[int, Character]) -> Iterator[dict[int, FighterMetrics]]:
    """Измерить реальные переходы движка, не меняя их результата.

    Журнал сообщает урон до барьера, но не его поглощение и не все списания
    ресурса. Поэтому наблюдаем вызов damaged и записи бойцов. Измерители живут
    только внутри одного моделируемого боя; значения и правила не подменяются.
    """
    metrics = {one: FighterMetrics() for one in roster}
    original_damage = Combatant.damaged
    original_replace = BattleState.replace_combatant

    def damaged(one: Combatant, amount: int) -> tuple[Combatant, int]:
        if one.id in metrics:
            metrics[one.id].barrier_absorbed += min(one.barrier, max(0, amount))
        return original_damage(one, amount)

    def replaced(state: BattleState, updated: Combatant) -> BattleState:
        if updated.id in metrics:
            measured = metrics[updated.id]
            measured.minimum_health_ratio = min(
                measured.minimum_health_ratio, updated.health / max(1, updated.max_health)
            )
            before = state.by_id(updated.id)
            if before is not None and before.max_resource == updated.max_resource:
                delta = updated.resource - before.resource
                measured.used_resource += max(0, -delta)
                measured.restored_resource += max(0, delta)
        return original_replace(state, updated)

    with (
        patch.object(Combatant, "damaged", damaged),
        patch.object(BattleState, "replace_combatant", replaced),
    ):
        yield metrics


def observe_events(state: BattleState, metrics: dict[int, FighterMetrics]) -> None:
    for event in state.events:
        if event.kind in {EventKind.DAMAGE, EventKind.CRIT} and event.target_id in metrics:
            metrics[event.target_id].received_damage += event.amount


def developed(
    content: GameContent, class_id: str, level: int, *, partial: bool = False
) -> Character:
    """Законная сборка: половина либо весь бюджет, без бесплатных чужих рангов.

    Неполная сборка вкладывает половину очков умений и талантов и не выбирает
    финальные грани пятого ранга. Неистраченные очки остаются на персонаже.
    Испытания первого согласованного пути специализации считаются пройденными.
    """
    base = build(content, class_id, level)
    starter = min(content.skills_of(f"class:{class_id}"), key=lambda one: one.level)
    budget = skill_points_between(1, level, content.rules.levels_per_skill_point)
    available_budget = budget // 2 if partial else budget
    withheld = budget - available_budget
    character = replace(
        base,
        loadout=SkillLoadout(
            actives=(starter.code, None, None, None, None, None),
            racial=content.racial_active(base.race_id).code,
        ),
        unspent_skill_points=available_budget,
    )
    # Испытания считаем пройденными. Выбор согласован с родителями и уровнем;
    # это подготовленная сборка, а не измерение длительности прохождения квестов.
    path: list[str] = []
    for tier in content.subclass_tiers(class_id):
        offered = [
            one
            for one in content.subclasses_of(class_id, tier)
            if one.level <= level and (not one.parent or one.parent in path)
        ]
        if offered:
            path.append(offered[0].id)
    character = replace(character, subclass_ids=tuple(path))
    pool = [
        one
        for one in skills.teachable(content, character)
        if one.is_active and one.owner_kind is not OwnerKind.RACE
    ]
    attacks = sorted(
        (one for one in pool if skills.spec_of(one).category is EffectCategory.DAMAGE),
        key=lambda one: (one.power * skills.spec_of(one).hits, one.level),
        reverse=True,
    )
    support = sorted(
        (one for one in pool if skills.spec_of(one).category is not EffectCategory.DAMAGE),
        key=lambda one: (
            skills.spec_of(one).category in {EffectCategory.HEAL, EffectCategory.BARRIER},
            one.level,
        ),
        reverse=True,
    )
    panel: list[str] = []
    forks: set[str] = set()
    for one in (*attacks[:4], *support, *attacks[4:]):
        if len(panel) == content.rules.active_slots:
            break
        if one.fork and one.fork in forks:
            continue
        if not skills.is_known(character, one.code):
            learned = skills.learn(content, character, one)
            if learned is None:
                continue
            character = learned
        panel.append(one.code)
        forks.add(one.fork)
    if not panel:
        panel = [starter.code]
    character = replace(
        character,
        loadout=replace(character.loadout, actives=tuple(panel + [None] * (6 - len(panel)))),
    )
    # Вкладываемся в панель, затем в пассивные умения. Все покупки проходят
    # настоящие ограничения уровня, класса, развилок и бюджета.
    preferred = [content.skill(code) for code in panel]
    passives = [one for one in skills.teachable(content, character) if not one.is_active]
    for _ in range(content.rules.max_rank):
        for one in (*preferred, *passives):
            learned = skills.learn(content, character, one)
            if learned is not None:
                character = learned
    for one in preferred:
        while choices := skills.mastery_choices(content, character, one):
            if partial and skills.mastery_tier_due(content, character, one) == 2:
                break
            chosen = skills.choose_mastery(content, character, one, choices[0].code)
            assert chosen is not None
            character = chosen
    talent_budget = talents.points(character) // 2 if partial else talents.points(character)
    for _ in range(talent_budget):
        for one in talents.pool(content, character):
            chosen = talents.choose(content, character, one.code)
            if chosen is not None:
                character = chosen
                break
        else:
            break
    equipment = armed(content, content.character_class(class_id), level, character.loadout.actives)
    tier = item_procgen.tier_at(content, level)
    if tier is not None:
        jewelry = next(
            (one for one in content.gear_archetypes if one.slot == "trinket" and not one.tool_type),
            None,
        )
        if jewelry is not None:
            equipment = equipment.equip(
                "trinket", item_procgen.gear_id(jewelry.id, tier.level, "common")
            )
    return replace(
        character,
        unspent_skill_points=character.unspent_skill_points + withheld,
        equipment=equipment,
    )


def action_for(
    content: GameContent,
    character: Character,
    state: BattleState,
    memory: DecisionMemory | None = None,
) -> BattleAction:
    """Один явный алгоритм: расход по боевой цене, лечение по ранам, урон по цели."""
    actor = state.by_id(character.id)
    assert actor is not None
    targets = state.foes_of(actor.id)
    if not targets:
        return BattleAction(kind=ActionKind.ATTACK)
    target = min(targets, key=lambda one: (one.health, one.id))
    best = (
        blow_of(content, character, actor.effects),
        BattleAction(kind=ActionKind.ATTACK, target=target.id),
    )
    proactive: tuple[float, BattleAction] | None = None
    for slot, code in enumerate(character.loadout.actives):
        if code is None:
            continue
        one = content.skill(code)
        if equipment.skill_refusal(content, character, one):
            continue
        if one.requires_stealth and not actor.effects.has(StatusKind.UNSEEN):
            continue
        if actor.cooldown_of(code) or skill_price(content, character, one, actor) > actor.resource:
            continue
        spec = skills.effective_spec(content, character, one)
        power = one.power_at_rank(character.loadout.rank_of(code)) / 100
        blow = blow_of(content, character, actor.effects)
        if spec.category is EffectCategory.DAMAGE:
            worth = blow * power * spec.damage_scale * spec.hits
            worth *= len(targets) if spec.aoe else 1
            worth += blow * spec.dot_scale * min(3, spec.dot_turns)
            recipient = target.id
        elif spec.category is EffectCategory.HEAL:
            allies = (
                [
                    one
                    for one in state.combatants
                    if one.alive and one.side == actor.side and (one.id == actor.id or one.is_hero)
                ]
                if one.target == "ally" or spec.aoe
                else [actor]
            )
            patient = min(allies, key=lambda one: one.health / one.max_health)
            worth = min(patient.max_health - patient.health, patient.max_health * power) * 1.3
            recipient = patient.id
        elif spec.category is EffectCategory.BARRIER:
            shield = actor.max_health * power
            last = memory.last_barrier_round.get(actor.id, -10) if memory else state.round
            cadence = state.round - last >= 4 if memory else (state.round - 1) % 4 == 0
            if not cadence or actor.barrier >= shield * 0.2:
                continue
            eligible = (
                shield >= actor.max_health * 0.25
                and sum(one.health for one in targets) > best[0] * 2
            )
            if eligible:
                proactive = max(
                    (proactive or (0.0, BattleAction(kind=ActionKind.ATTACK))),
                    (shield, BattleAction(kind=ActionKind.SKILL, slot=slot, target=actor.id)),
                    key=lambda pair: pair[0],
                )
            worth = shield * (1.0 - actor.health / actor.max_health) * 0.6
            recipient = actor.id
        elif any(held.kind is StatusKind.UNSEEN for held in spec.holds):
            if actor.effects.has(StatusKind.UNSEEN):
                continue
            followups = []
            for next_code in character.loadout.actives:
                if next_code is None:
                    continue
                next_skill = content.skill(next_code)
                if (
                    not next_skill.requires_stealth
                    or actor.cooldown_of(next_code)
                    or equipment.skill_refusal(content, character, next_skill)
                ):
                    continue
                combined_cost = skill_price(content, character, one, actor) + skill_price(
                    content, character, next_skill, actor
                )
                recovery = derived_stats(content, character, actor.effects).resource_regen
                if combined_cost > actor.resource + recovery:
                    continue
                next_spec = skills.effective_spec(content, character, next_skill)
                followups.append(
                    blow
                    * next_skill.power_at_rank(character.loadout.rank_of(next_code))
                    / 100
                    * next_spec.damage_scale
                    * next_spec.hits
                )
            if not followups:
                continue
            # Подготовка тратит действие, но скрытность также исключает героя
            # из обычных целей. Оценка следующего удара учитывает оба свойства.
            worth = max(followups) * 0.75
            recipient = actor.id
        else:
            continue
        candidate = BattleAction(kind=ActionKind.SKILL, slot=slot, target=recipient)
        if worth > best[0]:
            best = worth, candidate
    if proactive is not None and actor.health >= actor.max_health * 0.4:
        if memory is not None:
            memory.last_barrier_round[actor.id] = state.round
        return proactive[1]
    if memory is not None and best[1].kind is ActionKind.SKILL and best[1].slot is not None:
        selected = character.loadout.actives[best[1].slot]
        if selected and skills.spec_of(content.skill(selected)).category is EffectCategory.BARRIER:
            memory.last_barrier_round[actor.id] = state.round
    return best[1]


def with_rare_gear(content: GameContent, character: Character) -> Character:
    worn: dict[str, str] = {}
    source = random.Random(f"equipment:{character.class_id}:{character.level}")
    for slot, item_id in character.equipment.items.items():
        parsed = item_procgen.parse_gear_id(item_id)
        assert parsed is not None
        archetype_id, level, _, _ = parsed
        archetype = content.gear_archetype(archetype_id)
        roll = item_procgen.new_roll(content, source, archetype, class_id=character.class_id)
        worn[slot] = item_procgen.gear_id(archetype_id, level, "rare", roll)
    return replace(character, equipment=Equipment(worn))


def simulate(
    content: GameContent,
    roster: Mapping[int, Character],
    enemies: tuple[Enemy, ...],
    seed: bytes,
    *,
    limit: int = 80,
) -> dict[str, object]:
    """Бой до исхода либо предела команд; начальные ходы входят в измерение."""
    fighters = [
        hero_combatant(content, character, combatant_id=index, side=0, live=True)
        for index, character in roster.items()
    ]
    first_enemy = max(roster) + 1
    fighters.extend(
        monster_combatant(enemy, combatant_id=index + first_enemy, side=1)
        for index, enemy in enumerate(enemies)
    )
    memory = DecisionMemory()
    with observed_battle(roster) as metrics:
        state = open_battle(content, roster, fighters, seed)
        observe_events(state, metrics)
        actions = 0
        while not state.is_over and actions < limit:
            actor = state.active
            assert actor is not None and actor.id in roster
            character = roster[actor.id]
            actions += 1
            state = act(
                content,
                roster,
                state,
                action_for(content, character, state, memory),
                derive(seed, "action", actions),
            )
            observe_events(state, metrics)
    finals = [state.by_id(index) for index in roster]
    return {
        "won": state.verdict_for(next(iter(roster))) is Verdict.VICTORY,
        "unfinished": not state.is_over,
        "actions": actions,
        "health_spent": statistics.mean(
            1 - max(0, one.health) / one.max_health for one in finals if one is not None
        ),
        "minimum_health_ratio": min(one.minimum_health_ratio for one in metrics.values()),
        "received_damage": sum(one.received_damage for one in metrics.values()),
        "used_resource": sum(one.used_resource for one in metrics.values()),
        "restored_resource": sum(one.restored_resource for one in metrics.values()),
        "barrier_absorbed": sum(one.barrier_absorbed for one in metrics.values()),
    }


def summarized(results: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {
        "wins": sum(bool(one["won"]) for one in results),
        "unfinished": sum(bool(one["unfinished"]) for one in results),
    }
    for name in (
        "actions",
        "health_spent",
        "minimum_health_ratio",
        "received_damage",
        "used_resource",
        "restored_resource",
        "barrier_absorbed",
    ):
        summary[f"median_{name}"] = round(statistics.median(float(one[name]) for one in results), 4)
    return summary


def measure(
    content: GameContent,
    trials: int,
    *,
    levels: tuple[int, ...] = LEVELS,
    class_ids: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for klass in content.classes:
        if class_ids is not None and klass.id not in class_ids:
            continue
        for level in levels:
            for profile in PROFILES:
                character = developed(content, klass.id, level, partial=profile == "developing")
                if profile == "developed_rare":
                    character = with_rare_gear(content, character)
                for rank in EnemyRank:
                    results: list[dict[str, object]] = []
                    for trial in range(trials):
                        seed = derive(SEED, klass.id, level, rank.value, trial)
                        enemies = generate_group(
                            seed,
                            archetypes=content.enemy_archetypes,
                            biome="*",
                            level=level,
                            rank=rank,
                            elite_titles=content.elite_titles,
                        )
                        results.append(simulate(content, {character.id: character}, enemies, seed))
                    summary = summarized(results)
                    rows.append(
                        {
                            "class": klass.id,
                            "level": level,
                            "profile": profile,
                            "enemy": rank.value,
                            "trials": trials,
                            **summary,
                            "median_turns": summary["median_actions"],
                            "skill_ranks": sum(character.loadout.ranks.values()),
                            "talent_points": talents.spent(content, character),
                            "health": derived_stats(content, character).max_health,
                            "unspent_skill_points": character.unspent_skill_points,
                            "equipment_slots": sorted(character.equipment.items),
                        }
                    )
        print(f"Проверен класс {klass.id}", flush=True)
    return rows


def measure_parties(
    content: GameContent, trials: int, *, levels: tuple[int, ...] = LEVELS
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for class_ids in (("warrior", "mage", "cleric"), ("barbarian", "rogue", "druid")):
        for level in levels:
            roster = {
                index: replace(developed(content, class_id, level), id=index, user_id=index)
                for index, class_id in enumerate(class_ids, 1)
            }
            outcomes: list[dict[str, object]] = []
            for trial in range(trials):
                seed = derive(SEED, "party", level, trial)
                enemies = generate_group(
                    seed,
                    archetypes=content.enemy_archetypes,
                    biome="*",
                    level=level,
                    rank=EnemyRank.BOSS,
                    elite_titles=content.elite_titles,
                )
                outcomes.append(simulate(content, roster, enemies, seed, limit=240))
            summary = summarized(outcomes)
            rows.append(
                {
                    "classes": class_ids,
                    "level": level,
                    "enemy": "boss",
                    "trials": trials,
                    **summary,
                    "median_player_actions": summary["median_actions"],
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, default=Path("content"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.trials <= 100:
        parser.error("trials must be between 1 and 100")
    content = load_content(args.content)
    rows = measure(content, args.trials)
    parties = measure_parties(content, args.trials)
    args.output.write_text(
        json.dumps(
            {
                "schema": 2,
                "seed": SEED.decode(),
                "levels": LEVELS,
                "policy": (
                    "Three legal solo profiles: developing with half skill/talent budgets and "
                    "no final masteries; developed common; developed rare. Six equipment slots; "
                    "first specialization path with completed trials assumed. Two common-gear "
                    "parties. Proactive barriers at most once per four rounds per hero."
                ),
                "metrics": {
                    "aggregation": (
                        "Each median is across trials; solo actions are player commands."
                    ),
                    "minimum_health_ratio": (
                        "Minimum stored health/max_health within combat, before later healing; "
                        "party minimum over all members."
                    ),
                    "received_damage": (
                        "Sum of DAMAGE/CRIT event amounts aimed at measured heroes, after "
                        "mitigation and before barrier, including overkill and periodic damage."
                    ),
                    "barrier_absorbed": (
                        "Exact min(current barrier, damage amount) observed in Combatant.damaged; "
                        "expiration and barrier refresh are excluded."
                    ),
                    "used_resource": (
                        "All negative resource changes in stored fighter states with unchanged "
                        "max_resource; not net spend. Includes hostile drains when present."
                    ),
                    "restored_resource": (
                        "All positive resource changes in stored fighter states with unchanged "
                        "max_resource, including passive recovery and refunds."
                    ),
                    "party": (
                        "Damage, resource and absorption summed over measured members; "
                        "health_spent is their mean final deficit."
                    ),
                },
                "limitations": (
                    "Simulation, not live acceptance. One deterministic heuristic, not optimal "
                    "play: no consumables, racial active use, PvP, all alternative builds or "
                    "party compositions. Healing and attack scores are estimates; barriers are "
                    "proactive only in longer fights; stealth is used to enable available "
                    "stealth attacks, other control/buff sequences are not searched. "
                    "One seed family; item seed is fixed per "
                    "class/level. Equipment wear within a fight is real; each trial starts with "
                    "repaired gear and full health/resource. Costs of repair, travel, "
                    "trial quests and obtaining gear are not simulated."
                ),
                "rows": rows,
                "parties": parties,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Записано {len(rows) * args.trials} боёв: {args.output}")


if __name__ == "__main__":
    main()
