"""Contracts and skill points: what a city offers, and what a point buys.

Both are ledgers on the character, and both have the same failure mode - paying
twice, or counting something that was never taken. That is what is pinned here.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mmorpg.domain.entities import Character, GameContent, QuestLog, SkillLoadout
from mmorpg.domain.entities.location import Enemy, EnemyKind, EnemyRank, NodeKind
from mmorpg.domain.rules import edges as edge_rules
from mmorpg.domain.rules import quests as quest_rules
from mmorpg.domain.rules import skills as skill_rules
from mmorpg.domain.rules.skill_effects import spec_for


@pytest.fixture
def newcomer() -> Character:
    return Character(id=1, user_id=1, name="Аргус", race_id="human", class_id="warrior", level=1)


@pytest.fixture
def veteran() -> Character:
    return Character(
        id=2,
        user_id=2,
        name="Мерла",
        race_id="human",
        class_id="warrior",
        level=20,
        gold=1000,
        unspent_skill_points=5,
    )


def a_beast(*, elite: bool = False) -> Enemy:
    return Enemy(
        archetype_id="grey_wolf",
        name="Серый волк",
        kind=EnemyKind.BEAST,
        level=3,
        max_health=40,
        damage=5,
        armor=1,
        initiative=8.0,
        rank=EnemyRank.ELITE if elite else EnemyRank.NORMAL,
        loot=(),
        gold=5,
    )


# --- what the board shows ---------------------------------------------


def test_the_first_contract_is_open_and_the_next_one_is_not(
    content: GameContent, newcomer: Character
) -> None:
    offered = quest_rules.available(content, newcomer)
    assert [quest.id for quest in offered] == ["farhold_tallies"]


def test_a_contract_opens_once_the_one_it_follows_is_paid(
    content: GameContent, veteran: Character
) -> None:
    veteran = replace(veteran, city_id="farhold")
    assert not any(q.id == "farhold_meadow_teeth" for q in quest_rules.available(content, veteran))
    closed = replace(veteran, quests=QuestLog(done=("farhold_tallies",)))
    assert any(q.id == "farhold_meadow_teeth" for q in quest_rules.available(content, closed))


def test_a_contract_above_your_level_stays_off_the_board(
    content: GameContent, newcomer: Character
) -> None:
    ahead = replace(newcomer, quests=QuestLog(done=("farhold_tallies",)))
    offered = quest_rules.available(content, ahead)
    assert all(quest.level <= ahead.level for quest in offered)


def test_a_taken_contract_leaves_the_board(content: GameContent, newcomer: Character) -> None:
    took = quest_rules.take(content, newcomer, content.quest("farhold_tallies"))
    assert took.quests.is_taken("farhold_tallies")
    assert quest_rules.available(content, took) == ()


# --- counting ---------------------------------------------------------


def test_kills_only_count_for_the_kind_the_contract_asked_for(
    content: GameContent, veteran: Character
) -> None:
    hunting = replace(
        veteran, quests=QuestLog(taken={"farhold_meadow_teeth": 0}, done=("farhold_tallies",))
    )
    log, steps = quest_rules.record_kills(content, hunting, (a_beast(), a_beast()))
    assert log.progress("farhold_meadow_teeth") == 2
    assert len(steps) == 1

    humans = replace(a_beast(), kind=EnemyKind.HUMANOID)
    log, steps = quest_rules.record_kills(content, hunting, (humans,))
    assert log.progress("farhold_meadow_teeth") == 0
    assert steps == ()


def test_only_elites_count_towards_an_elite_contract(
    content: GameContent, veteran: Character
) -> None:
    hunting = replace(veteran, quests=QuestLog(taken={"farhold_ravine_leader": 0}))
    log, _ = quest_rules.record_kills(content, hunting, (a_beast(),))
    assert log.progress("farhold_ravine_leader") == 0
    log, _ = quest_rules.record_kills(content, hunting, (a_beast(elite=True),))
    assert log.progress("farhold_ravine_leader") == 1


def test_a_counter_never_runs_past_what_was_asked(content: GameContent, veteran: Character) -> None:
    quest = content.quest("farhold_meadow_teeth")
    nearly = replace(veteran, quests=QuestLog(taken={quest.id: quest.target_count - 1}))
    log, _ = quest_rules.record_kills(content, nearly, tuple(a_beast() for _ in range(5)))
    assert log.progress(quest.id) == quest.target_count


def test_searching_counts_only_for_search_contracts(
    content: GameContent, newcomer: Character
) -> None:
    took = quest_rules.take(content, newcomer, content.quest("farhold_tallies"))
    log, steps = quest_rules.record_search(content, took, NodeKind.CACHE)
    assert log.progress("farhold_tallies") == 1
    assert steps and steps[0].progress == 1

    log, _ = quest_rules.record_kills(content, took, (a_beast(),))
    assert log.progress("farhold_tallies") == 0


def test_a_made_thing_counts_for_the_contract_that_asked_for_it(
    content: GameContent, veteran: Character
) -> None:
    """Contracts and crafts used to be two games in one bot (Roadmap, "Риски")."""
    ready = replace(veteran, quests=QuestLog(done=("farhold_tallies",)))
    took = quest_rules.take(content, ready, content.quest("farhold_whetstones"))
    log, steps = quest_rules.record_craft(content, took, "whetstone", 2)
    assert log.progress("farhold_whetstones") == 2
    assert steps and steps[0].progress == 2

    # Something else out of the same workshop is still something else.
    other, _ = quest_rules.record_craft(content, took, "medium_head@6#common")
    assert other.progress("farhold_whetstones") == 0


def test_a_craft_contract_never_counts_past_what_was_asked(
    content: GameContent, veteran: Character
) -> None:
    quest = content.quest("farhold_whetstones")
    ready = replace(veteran, quests=QuestLog(done=("farhold_tallies",)))
    took = quest_rules.take(content, ready, quest)
    log, _ = quest_rules.record_craft(content, took, "whetstone", quest.target_count + 5)
    assert log.progress(quest.id) == quest.target_count


def test_nothing_counts_for_a_contract_that_was_never_taken(
    content: GameContent, newcomer: Character
) -> None:
    log, steps = quest_rules.record_search(content, newcomer, NodeKind.CACHE)
    assert log == newcomer.quests
    assert steps == ()


# --- paying out -------------------------------------------------------


def test_a_contract_pays_once_and_cannot_be_handed_in_twice(
    content: GameContent, newcomer: Character
) -> None:
    quest = content.quest("farhold_tallies")
    ready = replace(newcomer, quests=QuestLog(taken={quest.id: quest.target_count}))
    payout = quest_rules.hand_in(content, ready, quest)
    assert payout is not None
    assert payout.gold == quest.reward_gold
    assert payout.character.gold == newcomer.gold + quest.reward_gold
    assert payout.character.quests.is_done(quest.id)
    assert quest_rules.hand_in(content, payout.character, quest) is None


def test_an_unfinished_contract_pays_nothing(content: GameContent, newcomer: Character) -> None:
    quest = content.quest("farhold_tallies")
    started = replace(newcomer, quests=QuestLog(taken={quest.id: 1}))
    assert quest_rules.hand_in(content, started, quest) is None


def test_handing_in_can_raise_a_level(content: GameContent, newcomer: Character) -> None:
    quest = content.quest("farhold_tallies")
    ready = replace(newcomer, quests=QuestLog(taken={quest.id: quest.target_count}))
    payout = quest_rules.hand_in(content, ready, quest)
    assert payout is not None
    assert payout.character.level > newcomer.level
    assert payout.level_up.levels_gained > 0


# --- skill points -----------------------------------------------------


def test_a_point_learns_a_skill_and_the_next_one_raises_its_rank(
    content: GameContent, veteran: Character
) -> None:
    skill = skill_rules.teachable(content, veteran)[0]
    learned = skill_rules.learn(content, veteran, skill)
    assert learned is not None
    assert learned.loadout.rank_of(skill.code) == 1
    assert learned.unspent_skill_points == veteran.unspent_skill_points - 1

    raised = skill_rules.learn(content, learned, skill)
    assert raised is not None
    assert raised.loadout.rank_of(skill.code) == 2


def test_without_a_point_nothing_is_learned(content: GameContent, veteran: Character) -> None:
    broke = replace(veteran, unspent_skill_points=0)
    assert skill_rules.learn(content, broke, skill_rules.teachable(content, broke)[0]) is None


def test_a_rank_stops_at_the_maximum(content: GameContent, veteran: Character) -> None:
    skill = skill_rules.teachable(content, veteran)[0]
    maxed = replace(
        veteran,
        loadout=SkillLoadout(ranks={skill.code: content.rules.max_rank}),
        unspent_skill_points=9,
    )
    assert skill_rules.learn(content, maxed, skill) is None


def test_the_edge_is_asked_for_at_rank_three_and_only_once(
    content: GameContent, veteran: Character
) -> None:
    skill = skill_rules.teachable(content, veteran)[0]
    at_edge = replace(veteran, loadout=SkillLoadout(ranks={skill.code: content.rules.edge_rank}))
    assert skill_rules.needs_edge(content, at_edge, skill)

    chosen = skill_rules.choose_edge(at_edge, skill, skill.edges[0].code)
    assert chosen is not None
    assert not skill_rules.needs_edge(content, chosen, skill)
    assert skill_rules.choose_edge(chosen, skill, skill.edges[1].code) is None


def test_every_edge_in_the_game_declares_what_it_does(content: GameContent) -> None:
    """Ни одной грани-надписи.

    Это и была поломка: у всех 128 умений обе грани описаны своим действием, а
    движок делал для любой первой одно и то же, для любой второй - другое одно и
    то же. Грань без объявленной механики - обещание, которое некому выполнить.
    """
    silent = [
        f"{skill.code}:{edge.name}"
        for skill in content.skills
        for edge in skill.edges
        if edge.effect.empty
    ]
    assert silent == []


def test_an_edge_changes_the_numbers_it_promises(content: GameContent, veteran: Character) -> None:
    """Выбранная грань доходит до умения, а невыбранная ничего не трогает."""
    # Умение, которое само крови не пускает: кровь на цели - работа грани.
    skill = content.skill("warrior_sekushchiy_roscherk")
    heavier, bleeding = skill.edges

    plain = replace(veteran, loadout=SkillLoadout(ranks={skill.code: 3}))
    assert skill_rules.chosen_edge(plain, skill) is None
    assert edge_rules.applied(spec_for(skill.effect), None).dot_turns == 0

    cutting = replace(plain, loadout=replace(plain.loadout, edges={skill.code: bleeding.code}))
    chosen = skill_rules.chosen_edge(cutting, skill)
    assert chosen is not None
    # «Цель истекает кровью 3 хода» - именно это и происходит.
    assert edge_rules.applied(spec_for(skill.effect), chosen).dot_turns == 3

    # А вторая грань трогает силу и откат, но не само действие.
    strong = replace(plain, loadout=replace(plain.loadout, edges={skill.code: heavier.code}))
    harder = skill_rules.chosen_edge(strong, skill)
    assert harder is not None
    assert harder.power == pytest.approx(25.0)
    assert edge_rules.applied(spec_for(skill.effect), harder).dot_turns == 0


def test_an_edge_of_a_skill_that_changed_is_read_as_unchosen(
    content: GameContent, veteran: Character
) -> None:
    """Содержимое переживает сохранённого персонажа (``Claude.md``, правило 8)."""
    skill = content.skill("warrior_rassechenie")
    stale = replace(veteran, loadout=SkillLoadout(edges={skill.code: "warrior_rassechenie_z"}))

    assert skill_rules.chosen_edge(stale, skill) is None
    assert skill_rules.power_factor(stale, skill) == 1.0


def test_a_skill_sits_in_exactly_one_slot(content: GameContent, veteran: Character) -> None:
    skill = next(s for s in skill_rules.teachable(content, veteran) if s.is_active)
    known = replace(veteran, loadout=SkillLoadout(ranks={skill.code: 1}))
    first = skill_rules.put_in_slot(content, known, 0, skill.code)
    assert first is not None
    second = skill_rules.put_in_slot(content, first, 3, skill.code)
    assert second is not None
    assert second.loadout.actives[0] is None
    assert second.loadout.actives[3] == skill.code


def test_an_unknown_skill_never_reaches_the_panel(content: GameContent, veteran: Character) -> None:
    skill = skill_rules.teachable(content, veteran)[0]
    assert skill_rules.put_in_slot(content, veteran, 0, skill.code) is None


def test_a_passive_does_not_fit_an_active_slot(content: GameContent, veteran: Character) -> None:
    """Постоянное умение и работает само - в панели ему нечего делать."""
    passive = next(s for s in skill_rules.teachable(content, veteran) if not s.is_active)
    known = replace(veteran, loadout=SkillLoadout(ranks={passive.code: 1}))
    assert skill_rules.put_in_slot(content, known, 0, passive.code) is None


def test_forgetting_hands_every_point_back_and_empties_the_slot(
    content: GameContent, veteran: Character
) -> None:
    skill = next(s for s in skill_rules.teachable(content, veteran) if s.is_active)
    student = replace(
        veteran,
        loadout=SkillLoadout(
            actives=(skill.code, None, None, None, None, None),
            ranks={skill.code: 3},
            edges={skill.code: skill.edges[0].code},
        ),
    )
    forgotten = skill_rules.forget(content, student, skill)
    assert forgotten is not None
    assert forgotten.unspent_skill_points == veteran.unspent_skill_points + 3
    assert not skill_rules.is_known(forgotten, skill.code)
    assert forgotten.loadout.actives[0] is None
    assert forgotten.loadout.edge_of(skill.code) is None


def test_a_contract_belongs_to_the_city_the_player_is_standing_in(
    content: GameContent,
) -> None:
    """Доска в чужом городе показывала задания родного и ноль своих."""
    from mmorpg.domain.rules import quests as quest_rules

    traveller = Character(
        id=9,
        user_id=9,
        name="Мерла",
        race_id="human",
        class_id="warrior",
        level=30,
        city_id="farhold",
    )
    at_home = quest_rules.available(content, traveller)
    assert all(quest.city_id == "farhold" for quest in at_home)

    away = quest_rules.available(content, traveller, "dusk_harbor")
    assert all(quest.city_id == "dusk_harbor" for quest in away)


def test_a_skill_removed_from_the_game_hands_its_points_back(content: GameContent) -> None:
    """Иначе выкатка, убравшая умение, оставляет игрока без панели и без очка.

    Ровно это и случилось бы с разбойниками, у которых «Удар в спину» перестал
    быть умением первого уровня.
    """
    stranded = Character(
        id=3,
        user_id=3,
        name="Тень",
        race_id="human",
        class_id="rogue",
        level=1,
        loadout=SkillLoadout(
            actives=("умения-такого-нет", None, None, None, None, None),
            racial="расового-тоже-нет",
            ranks={"умения-такого-нет": 2, "расового-тоже-нет": 1},
        ),
    )
    repaired = skill_rules.reclaim_lost(content, stranded)
    assert repaired is not None
    assert repaired.loadout.actives[0] is None
    assert "умения-такого-нет" not in repaired.loadout.ranks
    assert repaired.unspent_skill_points == 3, "вернулось ровно столько, сколько было вложено"
    # Расовое умение не выбирают: вместо пропавшего встаёт то, что есть у расы.
    assert repaired.loadout.racial == content.race("human").active_code

    assert skill_rules.reclaim_lost(content, repaired) is None, "второй раз забирать нечего"
