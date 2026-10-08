"""Все местные задания достижимы только в своём месте; испытания свободны."""

from dataclasses import replace

from mmorpg.domain.entities.character import Character
from mmorpg.domain.entities.location import Enemy, EnemyRank, NodeKind
from mmorpg.domain.entities.quest import ObjectiveKind, QuestLog
from mmorpg.domain.procgen.enemies import candidates
from mmorpg.domain.rules import quests


def test_every_local_target_is_reachable_and_does_not_count_in_other_places(content):
    checked = 0
    for quest in content.quests:
        if quest.is_trial or not quest.location_slot:
            continue
        city = content.city(quest.city_id)
        site = city.location(quest.location_slot)
        hero = Character(
            id=1,
            user_id=1,
            name="Аргус",
            race_id="human",
            class_id="warrior",
            level=quest.level,
            city_id="another_city",
            quests=QuestLog(taken={quest.id: 0}, done=("paid_before_update",)),
        )
        if quest.objective in {ObjectiveKind.KILL, ObjectiveKind.ELITE}:
            pool = candidates(content.enemy_archetypes, site.biome)
            target = next(
                one
                for one in pool
                if not quest.target_kind or quest.target_kind in {one.id, one.kind.value}
            )
            enemy = Enemy(
                archetype_id=target.id,
                name=target.name,
                kind=target.kind,
                level=quest.level,
                max_health=1,
                damage=1,
                armor=0,
                initiative=1.0,
                loot=(),
                gold=0,
                rank=EnemyRank.ELITE
                if quest.objective is ObjectiveKind.ELITE
                else EnemyRank.NORMAL,
            )

            def record(city_id, slot, hero=hero, enemy=enemy):
                return quests.record_kills(
                    content, hero, (enemy,), city_id=city_id, location_slot=slot
                )[0]
        else:
            kind = NodeKind(quest.target_kind or "cache")

            def record(city_id, slot, hero=hero, kind=kind):
                return quests.record_search(
                    content, hero, kind, city_id=city_id, location_slot=slot
                )[0]

        assert record(quest.city_id, quest.location_slot).progress(quest.id) == 1, quest.id
        assert record("another_city", quest.location_slot).progress(quest.id) == 0, quest.id
        assert record(quest.city_id, quest.location_slot + 1).progress(quest.id) == 0, quest.id
        assert record(quest.city_id, quest.location_slot).done == hero.quests.done
        checked += 1
    assert checked >= 51


def test_free_personal_trials_count_each_participant_in_the_same_event(content):
    trial = next(q for q in content.quests if q.is_trial and q.objective is ObjectiveKind.KILL)
    target = next(
        one
        for one in content.enemy_archetypes
        if not trial.target_kind or trial.target_kind in {one.id, one.kind.value}
    )
    enemy = Enemy(
        archetype_id=target.id,
        name=target.name,
        kind=target.kind,
        level=20,
        max_health=1,
        damage=1,
        armor=0,
        initiative=1.0,
        loot=(),
        gold=0,
    )
    first = Character(
        id=1,
        user_id=1,
        name="Аргус",
        race_id="human",
        class_id="warrior",
        level=30,
        quests=QuestLog(taken={trial.id: 0}),
    )
    second = replace(first, id=2, user_id=2, name="Мерла", city_id="last_beacon")
    for participant in (first, second):
        log, _ = quests.record_kills(
            content, participant, (enemy,), city_id="farhold", location_slot=2
        )
        assert log.progress(trial.id) == 1
