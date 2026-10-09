"""Воспроизводимый проход спуска и оценка объёма чтения; без Telegram."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from scripts.m04_progression import newcomer, prepare_level

from mmorpg.application.services.battle import begin
from mmorpg.domain.entities.character import Character, Equipment
from mmorpg.domain.entities.combat import ActionKind, BattleAction, Verdict
from mmorpg.domain.entities.content import GameContent
from mmorpg.domain.procgen.seeds import derive
from mmorpg.domain.rules import adventure, combat, expedition, participation
from mmorpg.domain.rules.stats import derived_stats
from mmorpg.infrastructure.content import load_content
from mmorpg.presentation.telegram.flows.combat import render

SEED = b"vellar-m06-20261009"
COMPOSITIONS = (
    ("warrior", "warrior"),
    ("rogue", "mage", "druid"),
    ("warrior", "ranger", "mage", "cleric", "paladin"),
)


def prepared(content: GameContent, class_id: str, key: int) -> Character:
    klass = content.character_class(class_id)
    gear = Equipment()
    for slot in ("weapon", "head", "body", "hands", "feet"):
        candidates = [
            one
            for one in content.items
            if one.slot == slot
            and one.level <= 15
            and one.rarity == "common"
            and (
                klass.can_wield(one.weapon_type)
                if one.is_weapon
                else one.is_armor and klass.can_wear(one.armor_type)
            )
        ]
        if candidates:
            picked = max(
                candidates,
                key=lambda one: (one.level, one.damage.average if one.damage else one.armor),
            )
            gear = gear.equip(slot, picked.id)
    character = prepare_level(
        content, replace(newcomer(content, class_id, "human"), equipment=gear), 15
    )
    return replace(character, id=key, user_id=1000 + key, name=f"Участник {key}", health=0)


def probe(content: GameContent, classes: tuple[str, ...], trial: int) -> dict[str, object]:
    heroes = [prepared(content, cls, i) for i, cls in enumerate(classes, 1)]
    dungeon = content.city("farhold").dungeon("farhold_flooded_drift")
    words = [0] * len(heroes)
    decisions = [0] * len(heroes)
    potions = [0] * len(heroes)
    credits: tuple[tuple[int, int], ...] = ()
    gold = [0] * len(heroes)
    completed = 0
    room_actions: list[list[int]] = []
    for layer, meeting in enumerate(dungeon.encounters):
        seed = derive(SEED, trial, layer)
        enemies = expedition.foes(
            content,
            meeting,
            seed=seed,
            level=dungeon.level,
            stakes=1.0,
            bounty=1.0,
            participants=len(heroes),
        )
        session, roster = begin(
            content,
            battle_id=f"m06-{trial}-{layer}",
            attackers=[(one, True) for one in heroes],
            enemies=enemies,
            seed=seed,
            participation_rule=1,
            briefing=f"{meeting.name}. {meeting.briefing}",
        )
        state = session.state
        turns = 0
        while not state.is_over and turns < 300:
            actor = state.active
            assert actor is not None
            for i, character in enumerate(heroes, 1):
                words[i - 1] += len(
                    render(content, character, replace(session, state=state), i).text().split()
                )
            action = combat._chosen_by_engine(content, roster, state, actor, seed)
            if actor.health < actor.max_health * 0.45 and potions[actor.id - 1] < 3:
                action = BattleAction(ActionKind.ITEM, item_id="small_healing_potion")
                potions[actor.id - 1] += 1
            updated = combat.act(content, roster, state, action, seed)
            if (updated.round, updated.cursor) == (
                state.round,
                state.cursor,
            ) and not updated.is_over:
                updated = combat.act(content, roster, state, BattleAction(ActionKind.ATTACK), seed)
            state = updated
            decisions[actor.id - 1] += 1
            turns += 1
        if state.verdict_for(1) is not Verdict.VICTORY:
            break
        completed += 1
        room_actions.append([one.actions for one in state.heroes()])
        winners = tuple(one for one in state.heroes() if participation.eligible(one))
        credits = participation.room_credit(credits, winners, layer)
        shares = participation.reward_shares(state.gold, len(winners))
        exps = participation.reward_shares(state.experience, len(winners))
        for j, one in enumerate(winners):
            i = one.character_id - 1
            won = adventure.resolve_victory(
                content, heroes[i], state, one.id, gold=shares[j], experience=exps[j], loot=()
            )
            stats = derived_stats(content, won.character)
            healed = min(
                stats.max_health,
                won.character.health_or(stats.max_health)
                + stats.max_health * meeting.heal_percent // 100,
            )
            heroes[i] = won.character.with_health(healed, stats.max_health)
            gold[i] += won.gold
    return {
        "classes": classes,
        "trial": trial,
        "completed": completed,
        "room_actions": room_actions,
        "full_participants": [key for key, count in credits if count == 4],
        "decisions": decisions,
        "words": words,
        "reading_minutes_at_180_words": [round(one / 180, 1) for one in words],
        "potions": potions,
        "battle_gold": gold,
    }


def run(content: GameContent, trials: int = 16) -> dict[str, object]:
    if trials < 1:
        raise ValueError("At least one trial")
    compositions = (*COMPOSITIONS, *((one.id,) for one in content.classes))
    return {
        "seed": SEED.decode(),
        "trials_per_composition": trials,
        "reading_is_an_estimate": True,
        "runs": [
            probe(content, classes, trial) for classes in compositions for trial in range(trials)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("backups/m06-descent-model.json"))
    parser.add_argument("--trials", type=int, default=16)
    args = parser.parse_args()
    report = run(load_content(Path("content")), args.trials)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = report["runs"]
    print(f"Модель: {len(rows)} заходов; результат записан в {args.output}")


if __name__ == "__main__":
    main()
